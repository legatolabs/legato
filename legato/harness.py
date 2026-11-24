"""An open computer-use harness for open models: bring any OpenAI-compatible endpoint (OpenRouter, Together, Fireworks,
your own vLLM or SGLang) and any desktop sandbox, and get a Claude-style `computer` tool loop that wastes less time.

    import legato
    agent = legato.Harness(model="qwen/qwen3-vl-235b-a22b-instruct", base_url="https://openrouter.ai/api/v1",
                           api_key=os.environ["OPENROUTER_API_KEY"])
    result = agent.run("Add a Total row under the table and save", legato.attach(e2b_sandbox))
    print(result.report())

What it does differently from a reference loop (each measured, see docs/benchmarks.md):
- Waits until the screen is ready after each action instead of sleeping a fixed 2-3 s (the sandbox adapter's ready
  signal; on E2B it runs inside the sandbox).
- Keeps the prompt cache-friendly: screenshots are dropped from the history in chunks, not one per step, so the
  model server's prefix cache keeps hitting (in our OSWorld study, dropping one per step cut the hit rate from 85% to 25%; docs/benchmarks.md).
- Lets the model batch a few actions it is sure of (click a field, type, press Enter) into one turn, and sends one
  screenshot after the batch instead of one per action.
- Asks for short replies: output tokens are the slowest part of a hosted model call.
- Bounds every model call (timeout and retry), so one stuck request cannot stall a run.
- Times every phase, so `result.report()` shows where the run's time went.

`Harness.reference(...)` is the comparison point: the loop of Anthropic's computer-use reference implementation
(anthropics/claude-quickstarts, computer-use-demo). It runs every tool call the model returns, sleeps 2 s before
each screenshot, sends a screenshot after every action, keeps every screenshot in the history and lets the model
write freely. Pair it with a sandbox that sleeps 2 s per action to reproduce that loop's timing.
"""
from __future__ import annotations

import base64
import io
import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Literal, TypedDict

from legato.actions import Action, parse
from legato.env import Env, png_size
from legato.rl.policy import PolicyClient, PolicyConfig, PolicyError
from legato.safety import Denied, Stopped, Unavailable
from legato.tools import Tool, ToolCall
from legato import plan as plans

if TYPE_CHECKING:
    from legato.delegate import Planner

CLAUDE_STYLE_ACTIONS = ["left_click", "right_click", "double_click", "triple_click", "left_click_drag", "type", "key",
                        "scroll", "wait", "done", "fail"]

SYSTEM = """You control a computer desktop ({w}x{h} pixels) to complete the user's task. Each turn you see the \
current screenshot. Act by calling the `computer` tool.
{coords}
Actions: left_click, right_click, double_click, triple_click (coordinate; triple_click selects a line or field; \
left_click with keys "shift" or "cmd" holds that key, to select several items); \
left_click_drag (coordinate to end_coordinate); type (text, into the focused field); key (keys, e.g. "ctrl+s", \
"Return", "Tab"); scroll (coordinate, direction up/down/left/right, amount); wait; done (the task is complete and \
saved); fail (the task cannot be done).
You may call the tool up to {batch} times in one turn when you are sure of the sequence without seeing the screen \
in between (for example: click a field, type, press Return). Otherwise call it once.
Keep any text before your tool calls to one short sentence.
Prefer reliable keyboard shortcuts. Save files in their current format."""

# The computer-use parts of the reference implementation's system prompt (computer_use_demo/loop.py), with the
# bash-tool and browser lines dropped because this harness gives the model only the `computer` tool.
REFERENCE_SYSTEM = """<SYSTEM_CAPABILITY>
* You are utilising an Ubuntu virtual machine ({w}x{h} screenshots) through the `computer` tool.
* {coords}
* Actions: screenshot; left_click, right_click, double_click (coordinate); left_click_drag (coordinate to \
end_coordinate); type (text); key (keys, e.g. "ctrl+s"); scroll (coordinate, direction, amount); wait; done when \
the task is complete; fail if it cannot be done.
* When viewing a page it can be helpful to zoom out so that you can see everything on the page.  Either that, or \
make sure you scroll down to see everything before deciding something isn't available.
* When using your computer function calls, they take a while to run and send back to you.  Where possible/feasible, \
try to chain multiple of these calls all into one function calls request.
</SYSTEM_CAPABILITY>"""

DENIED_NOTE = ("The person at this computer did not allow this action, so it was NOT run, and neither were the "
               "actions after it in your last turn: {what}. Do not repeat it. Choose another way, or call fail if "
               "the task cannot be done without it.")
UNAVAILABLE_NOTE = ("This desktop can't do that action, so it was NOT run, and neither were the actions after it in your "
                    "last turn: {what}. Do not repeat it. Do it another way, or call fail if there is none.")


def _refused(e: Denied) -> str:
    return (UNAVAILABLE_NOTE if isinstance(e, Unavailable) else DENIED_NOTE).format(what=e)

END_NOTE = ("Your last reply had no action, so nothing happened. If the task is complete, call done; otherwise take "
            "the next step.")
CUT_OFF_NOTE = ("Your last reply reached the token limit before you called the tool, so nothing happened. Keep your "
                "reasoning short and call the tool now.")

COORDS = {"norm1000": "Coordinates are [x, y] on a 0-1000 scale of the screenshot's width and height (origin top-left).",
          "pixels": "Coordinates are [x, y] in pixels of the screenshot (origin top-left)."}

# Screenshot sizes Anthropic recommends for Claude computer use (the reference implementation scales to these).
CLAUDE_TARGETS = [(1024, 768), (1280, 800), (1366, 768)]


def computer_tool(actions: list[str] | None = None) -> dict:
    """The `computer` tool in OpenAI function format, with Claude computer-use action names."""
    return {"type": "function", "function": {
        "name": "computer",
        "description": "Act on the desktop: click, type, press keys, scroll, wait, or finish.",
        "parameters": {"type": "object", "required": ["action"], "properties": {
            "action": {"type": "string", "enum": actions or CLAUDE_STYLE_ACTIONS},
            "coordinate": {"type": "array", "items": {"type": "number"}, "description": "[x, y]"},
            "end_coordinate": {"type": "array", "items": {"type": "number"}, "description": "[x, y] for left_click_drag"},
            "text": {"type": "string"}, "keys": {"type": "string"},
            "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
            "amount": {"type": "integer"}}}}}


def _text_calls(text: str, names: tuple = (), _retry: bool = False) -> list:
    """(tool name, arguments) pairs written as top-level {...} objects in a reply's text, in order. An object with an
    "action" is a `computer` call; {"name": n, "arguments": {...}} is a call to tool n, if n is in `names`;
    {"tool": n, ...} is a call to tool n with the other fields, or, if n is a screen action, done or fail, a `computer`
    call with that action."""
    out, depth, start = [], 0, None
    for i, ch in enumerate(text):
        if ch == "{":
            depth, start = depth + 1, (i if depth == 0 else start)
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0:
                try:
                    a = json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    continue
                if isinstance(a, dict):
                    if "action" not in a and a.get("name") in names and isinstance(a.get("arguments"), dict):
                        out.append((a["name"], a["arguments"]))
                        continue
                    if "action" not in a and isinstance(a.get("tool"), str):
                        # the shape past calls are written back in ({"tool": "bash", ...}), which models copy
                        name = a["tool"]
                        rest = a["arguments"] if isinstance(a.get("arguments"), dict) else \
                            {k: v for k, v in a.items() if k != "tool"}
                        if name in names:
                            out.append((name, rest))
                        elif name.lower() in CLAUDE_STYLE_ACTIONS:
                            out.append(("computer", {"action": name.lower(), **rest}))
                        continue
                    a = a.get("arguments", a) if "action" not in a else a
                    if isinstance(a, dict) and "action" in a:
                        out.append(("computer", a))
    if depth and start is not None and not _retry:
        # An object left open before the model's own closing tags, e.g. Qwen3.8-27B's
        # '{"action": "left_click", "coordinate": [806, 155] </parameter> </function> </tool_call>': the action is
        # complete but for its braces (legato-research EXP-0075: 12 of 866 executor turns did nothing for this).
        tail = text[start:]
        cut = min((tail.find(t) for t in ("</parameter>", "</function>", "</tool_call>") if t in tail), default=-1)
        if cut > 0:
            out += _text_calls(tail[:cut].rstrip().rstrip(",") + "}" * depth, names, _retry=True)
    return out


def _named_calls(rec: dict, names: tuple = ()) -> list:
    """(tool name, arguments) pairs from a reply, in the order the model gave them: calls written as JSON in the text,
    then the native tool_calls. A model that writes two steps and calls the tool for the third means all three.
    `names` are the extra tools that may be called from the text."""
    native = []
    for tc in rec.get("tool_calls") or []:
        fn = tc.get("function") or {}
        a = fn.get("arguments")
        if isinstance(a, str):
            try:
                a = json.loads(a)
            except json.JSONDecodeError:
                continue
        if isinstance(a, dict):
            native.append((fn.get("name") or "computer", a))
    content = rec.get("content") or ""
    if isinstance(content, list):
        content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
    written = [c for c in _text_calls(content, names) if c not in native]   # the same call said and made: once
    return written + native


def _calls(rec: dict) -> list:
    """Tool-call argument dicts from a reply, in the order the model gave them (see `_named_calls`)."""
    return [a for _, a in _named_calls(rec)]


def is_claude(model: str) -> bool:
    m = model.lower()
    return "claude" in m or m.startswith("anthropic/")


def claude_size(w: int, h: int) -> tuple[int, int]:
    """The reference implementation's screenshot size for a w x h screen: the recommended size with the same aspect
    ratio, if it is smaller than the screen; else the screen itself."""
    for tw, th in CLAUDE_TARGETS:
        if abs(tw / th - w / h) < 0.02:
            return (tw, th) if tw < w else (w, h)
    return w, h


def resize_png(png: bytes, size: tuple[int, int]) -> bytes:
    from PIL import Image
    im = Image.open(io.BytesIO(png))
    if im.size == tuple(size):
        return png
    buf = io.BytesIO()
    im.convert("RGB").resize(size, Image.LANCZOS).save(buf, format="PNG")
    return buf.getvalue()


def _progress(rec: dict) -> str | None:
    """The `progress` line of a reply's plan call, if any."""
    for c in rec.get("tool_calls") or []:
        f = c.get("function") or {}
        if f.get("name") != "plan":
            continue
        try:
            args = json.loads(f.get("arguments") or "{}")
        except ValueError:
            continue
        if isinstance(args, dict) and isinstance(args.get("progress"), str) and args["progress"].strip():
            return args["progress"].strip()
    return None


def jpeg_b64(png_b64: str, quality: int = 85) -> str:
    """A base64 PNG screenshot as base64 JPEG: the same size and pixels in about a fifth of the bytes."""
    from PIL import Image
    buf = io.BytesIO()
    Image.open(io.BytesIO(base64.b64decode(png_b64))).convert("RGB").save(buf, format="JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode()


@dataclass
class Episode:
    """One task's conversation: the screen size, the size the model sees, and the turns so far."""
    task: str
    w: int
    h: int
    mw: int
    mh: int
    turns: list = field(default_factory=list)   # {"role": "user", "pngs": [b64...]} / {"role": "assistant", "text"}
    last_png: bytes | None = None               # change_log: the screen the model saw last, and its text lines
    last_lines: list | None = None


@dataclass
class Plan:
    """One model turn: what to run, in order. Each action is an `Action`, a `ToolCall` (a tool given in `tools=`), or
    None for "take a screenshot" (the reference's screenshot action)."""
    rec: dict
    messages: list
    actions: list
    reply: list
    done: bool = False
    failed: bool = False
    cut_off: bool = False       # the reply hit the token limit before any action (a thinking model ran out of room)


# How a run ended (RunResult.status); docs/python.md "When a run fails" says what to do about each:
#   done       the model called done                     failed     the model called fail (the task cannot be done)
#   max_steps  max_steps ran out first                   timeout    run(timeout_s=) ran out
#   cancelled  run(cancel=) was set                      stopped    the person stopped it (legato.safety.Stopped)
#   error      the model call or the desktop failed: `error` says which and why
RunStatus = Literal["done", "failed", "max_steps", "timeout", "cancelled", "stopped", "error"]


class Usage(TypedDict):
    """A run's model calls that answered: tokens, and the cost the provider reports (OpenRouter does; others leave
    it 0). complete is False when a call was sent and cut off before its reply (a timeout, the run's deadline, a
    dropped connection) or sent twice (hedge_after_s): the provider may bill those, and they are not counted here."""
    prompt: int
    completion: int
    cached: int
    cost_usd: float
    complete: bool


def _no_usage() -> Usage:
    return {"prompt": 0, "completion": 0, "cached": 0, "cost_usd": 0.0, "complete": True}


def _book(usage: Usage, rec: dict | None = None, error: Exception | None = None) -> None:
    """Add a model call's usage (rec), or note that a failed call (error) may have been billed."""
    if rec is not None:
        u = rec.get("usage") or {}
        for k in ("prompt", "completion", "cached"):
            usage[k] += u.get(k) or 0                     # type: ignore[literal-required]
        usage["cost_usd"] += u.get("cost") or 0.0
    if (rec is not None and (rec.get("cut") or rec.get("hedged"))) or getattr(error, "cut", 0):
        usage["complete"] = False


@dataclass
class RunResult:
    task: str
    done: bool = False
    failed: bool = False
    steps: int = 0              # model turns
    actions: list = field(default_factory=list)
    timings: list = field(default_factory=list)
    wall_s: float = 0.0
    error: str | None = None
    stopped: bool = False       # the person stopped the run (legato.safety.Stopped): `error` says how
    answer: str = ""            # what the model said when it ended (the answer to "find out ..." tasks)
    usage: Usage = field(default_factory=_no_usage)
    planner: dict = field(default_factory=dict)   # with a planner: its calls, by trigger, seconds, tokens, cost
    trace: list = field(default_factory=list)     # with a planner: each planner call and executor turn, in order
    status: RunStatus = "error"  # how it ended (RunStatus above); every run sets it

    def report(self) -> str:
        tot = {k: sum(t.get(k, 0.0) for t in self.timings) for k in ("model_s", "act_s", "settle_s", "obs_s", "tool_s")}
        other = max(0.0, self.wall_s - sum(tot.values()))
        share = lambda v: f"{100 * v / self.wall_s:.0f}%" if self.wall_s else "-"  # noqa: E731
        cost = (self.usage or {}).get("cost_usd")
        counts = f"{self.steps} model turns, {len(self.actions)} actions" + (f", ${cost:.4f}" if cost else "")
        if self.done or self.failed or not self.error:
            status = "done" if self.done else "failed" if self.failed else (
                "out of steps" if self.status == "max_steps" else self.status)
            head = f"{status} in {self.wall_s:.1f} s, {counts}"
        else:                                            # the error first, then how long it took
            head = f"{self.error} (after {self.wall_s:.1f} s, {counts})"
        return (f"{head}\n"
                f"model {tot['model_s']:.1f} s ({share(tot['model_s'])}) · actions {tot['act_s']:.1f} s "
                f"({share(tot['act_s'])}) · waiting for the screen {tot['settle_s']:.1f} s ({share(tot['settle_s'])}) · "
                f"screenshots {tot['obs_s']:.1f} s ({share(tot['obs_s'])}) · "
                + (f"tools {tot['tool_s']:.1f} s ({share(tot['tool_s'])}) · " if tot["tool_s"] else "")
                + f"other {other:.1f} s")


class _Limits:
    """A run's deadline (timeout_s) and cancel event, checked between model turns."""

    def __init__(self, timeout_s: float | None = None, cancel: threading.Event | None = None):
        self.timeout_s, self.cancel = timeout_s, cancel
        self.deadline = None if timeout_s is None else time.monotonic() + max(0.0, float(timeout_s))

    def hit(self, res: RunResult) -> bool:
        """True, with the result's status and error saying why, when the run has to end now."""
        if self.cancel is not None and self.cancel.is_set():
            res.status, res.error = "cancelled", "cancelled"
        elif self.deadline is not None and time.monotonic() >= self.deadline:
            res.status, res.error = "timeout", f"the run's time limit ({self.timeout_s:g} s) ran out"
        else:
            return False
        return True


def _desktop_error(e: Exception) -> str:
    """A desktop's exception as RunResult.error."""
    if isinstance(e, PolicyError):
        return str(e)[:500]
    said = str(e) if isinstance(e, (RuntimeError, OSError)) and str(e) else f"{type(e).__name__}: {e}"
    return f"the desktop failed: {said}"[:300]


class Harness:
    """A computer-use agent loop for any OpenAI-compatible model."""

    def __init__(self, model: str | None = None, base_url: str | None = None, api_key: str | None = None, *,
                 coords: str | None = None,
                 image_max: int | None = 8, fold: int = 4, max_batch: int | None = 3, step_timeout_s: float = 60.0,
                 retries: int = 1, temperature: float = 0.0, max_tokens: int = 4096, native_tools: bool = True,
                 system: str | None = None, screenshot_each_action: bool = False, end_on_text: bool = False,
                 screenshot_action: bool = False, model_size: str | tuple | None = "auto",
                 cache_breakpoints: bool | None = None, extra_body: dict | None = None, profile: str = "legato",
                 tools: list[Tool] | None = None, plan: bool = False, plan_max_steps: int = 12,
                 end_fix: bool = False, change_log: bool = False, hedge_after_s: float | None = None,
                 plan_trust_first_click: bool = False, ocr_cache: bool = False,
                 ocr_near: bool = False, strict_done: bool = False, image_format: str = "jpeg", jpeg_quality: int = 85,
                 change_log_lines: int = 15, change_log_turns: int | None = None, progress_note: bool = False,
                 history_scale: float | None = None, planner: "Planner | None" = None):
        """plan: one `plan` tool whose steps (screen actions, click_text, shell through the `bash` tool in `tools`,
        wait_for, done) run in order until one cannot go on (legato/plan.py). end_fix: a reply with no action gets
        a note to call done if the task is complete, and a second such reply in a row ends the task as done. coords: "norm1000" or "pixels" (default: pixels for Claude, norm1000 otherwise). image_max: screenshots kept
        in the prompt (None keeps all). max_batch: tool calls run per turn (None runs all). model_size: the size
        screenshots are sent at ("auto": Anthropic's recommended size for Claude, the screen size otherwise; None:
        the screen size; or (w, h)). cache_breakpoints: ask for prompt caching with a top-level cache_control (default: on for
        Claude; OpenRouter and Anthropic-compatible APIs read it). tools: more tools beside `computer`, such as
        `legato.tools.bash(env)`; each result goes back to the model as text. image_format: "jpeg" (default,
        jpeg_quality) or "png": screenshots go to the model as JPEG, the same pixels in about a fifth of the bytes; the
        upload is most of a hosted call's wait for its first token (legato-research EXP-0052: a call 1.57 s against
        3.11 s at median through OpenRouter, the next action unchanged; EXP-0053: on 27 Mac tasks a call 2.28 against
        3.80 s and a median task 18.5 against 23.7 s with passes held). "png" sends the screenshots lossless."""
        key_source = "" if api_key is None else "the api_key passed to legato.Harness"
        if model is None or base_url is None or api_key is None:     # what `legato setup` saved
            from legato import config
            cfg = config.load()
            if model is None and base_url is None:
                model, base_url = cfg.model or None, cfg.base_url or None
            elif base_url is None:
                base_url = cfg.base_url or None
            if not model or not base_url:
                raise ValueError(cfg.missing() or "pass model= and base_url=, or run legato setup")
            if api_key is None:                     # the saved key goes only to the saved server
                same = base_url.rstrip("/") == cfg.base_url.rstrip("/")
                api_key, key_source = (cfg.key, cfg.key_source) if same else ("", "")
        if image_format not in ("png", "jpeg"):
            raise ValueError("image_format must be 'png' or 'jpeg'")
        coords = coords or ("pixels" if is_claude(model) else "norm1000")
        if coords not in COORDS:
            raise ValueError(f"coords must be one of {list(COORDS)}")
        self.model, self.profile = model, profile
        self.client = PolicyClient(PolicyConfig(base_url=base_url, model=model, api_key=api_key, version=profile,
                                                timeout_s=step_timeout_s, retries=retries, logprobs=False,
                                                token_ids=False, extra_body=dict(extra_body or {})))
        self.client.key_source = key_source        # named if the server refuses the key
        self.coords, self.image_max, self.fold = coords, image_max, max(1, fold)
        self.plan_mode, self.plan_max = plan, max(1, plan_max_steps)
        self.end_fix = end_fix
        self.image_format, self.jpeg_quality = image_format, jpeg_quality
        self._jpeg: dict[str, str] = {}               # PNG b64 -> JPEG b64: each kept screenshot is encoded once
        # change_log: each turn carries the text that appeared and went on the screen (macOS Vision), so the history
        # holds earlier screens as text and image_max can be 1. Older screenshots then go one at a time (fold=1).
        self.change_log = change_log
        # change_log_lines: at most this many appeared and gone lines a turn; change_log_turns: only the newest N
        # turns keep their change text (older ones say it was left out), so a long task's prompt stops growing
        self.change_log_lines, self.change_log_turns = max(1, change_log_lines), change_log_turns
        # progress_note: each plan carries `progress`, one line of what is done and what is left; it stays in the
        # history as the model's own memory when older screenshots go
        self.progress_note = progress_note
        # history_scale: kept screenshots other than the newest go at this fraction of their size (e.g. 0.5, a
        # quarter of the image tokens); clicks are on the newest, so they are unaffected
        self.history_scale = history_scale
        self._scaled: dict[tuple, str] = {}
        # planner: a stronger model plans and checks, this harness's model executes one step at a time with a short
        # context (legato/delegate.py); run() then delegates
        self.planner = planner
        self.on_usage = None                  # called with each pointer call's usage (legato/point.py), for a cost meter
        # hedge_after_s: a call with no reply after this many seconds is sent again, and the first reply wins (the
        # provider stalls now and then: 25 calls of 12-66 s with short replies in legato-research EXP-0044/0046)
        self.hedge_after_s = hedge_after_s
        # plan_trust_first_click: a labelled click before any step of its plan has acted clicks where the model
        # aimed, without reading the screen (it is the screen the model just saw)
        self.plan_trust_first_click = plan_trust_first_click
        if ocr_cache:                         # a still screen is read once (the VM's reader takes ~1 s a read)
            plans.OCR_CACHE = True
        # ocr_near: a labelled click reads the box around the model's coordinate first, the whole screen only when
        # the label is not in it
        self.ocr_near = ocr_near
        # strict_done: done in the same plan counts only after a shell or applescript step that exits 0 and printed
        # what it checked; a wait_for does not count (plans.STRICT_DONE_SWAPS says so in the prompt)
        self.strict_done = strict_done
        if change_log:
            self.fold = 1
        self.max_batch = max(1, max_batch) if max_batch else None
        self.temperature, self.max_tokens, self.native_tools, self.system = temperature, max_tokens, native_tools, system
        self.screenshot_each_action, self.end_on_text, self.screenshot_action = (screenshot_each_action, end_on_text,
                                                                                 screenshot_action)
        self.model_size = model_size
        self.cache = is_claude(model) if cache_breakpoints is None else cache_breakpoints
        self.tools: dict[str, Tool] = {}
        for t in tools or []:
            if t.name == "computer" or t.name in self.tools:
                raise ValueError(f"tool name {t.name!r} is taken")
            self.tools[t.name] = t

    @classmethod
    def reference(cls, model: str | None = None, base_url: str | None = None, api_key: str | None = None,
                  **kw) -> "Harness":
        """Anthropic's computer-use reference loop (see the module docstring)."""
        opts = dict(image_max=None, max_batch=None, max_tokens=4096, system=REFERENCE_SYSTEM,
                    screenshot_each_action=True, end_on_text=True, screenshot_action=True, profile="reference")
        opts.update(kw)
        return cls(model, base_url, api_key, **opts)

    # ------------------------------------------------------------ prompt
    def _size(self, w: int, h: int) -> tuple[int, int]:
        if self.model_size == "auto":
            return claude_size(w, h) if is_claude(self.model) else (w, h)
        if self.model_size:
            return tuple(self.model_size)
        return w, h

    def _png(self, ep: Episode, png: bytes) -> str:
        if (ep.mw, ep.mh) != (ep.w, ep.h):
            png = resize_png(png, (ep.mw, ep.mh))
        return base64.b64encode(png).decode()

    def _messages(self, task: str, w: int, h: int, turns: list) -> list:
        """System + task, then one user turn per observation and one assistant turn per reply. Old screenshots are
        replaced by a placeholder `fold` at a time, so the prefix stays identical for several turns."""
        if self.plan_mode:
            sys_text = (self.system or plans.PLAN_SYSTEM).format(w=w, h=h, coords=COORDS[self.coords], batch=self.plan_max)
            if self.strict_done:
                sys_text = plans.strict_system(sys_text)
            if self.progress_note:
                sys_text += plans.PROGRESS_NOTE
        else:
            sys_text = (self.system or SYSTEM).format(w=w, h=h, coords=COORDS[self.coords], batch=self.max_batch or "several")
        if self.tools and not self.plan_mode:
            sys_text += "\n" + self._tools_prompt()
        n_img = sum(len(t.get("pngs") or [t["png_b64"]]) for t in turns if t["role"] == "user")
        excess = max(0, n_img - self.image_max) if self.image_max is not None else 0
        drop = -(-excess // self.fold) * self.fold if excess else 0     # round up to a whole chunk
        msgs, seen, first = [{"role": "system", "content": sys_text}], 0, True
        users = sum(1 for t in turns if t["role"] == "user")
        u = 0
        for t in turns:
            if t["role"] == "user":
                u += 1
                parts = [{"type": "text", "text": f"Task: {task}"}] if first else []
                first = False
                if t.get("note"):
                    parts.append({"type": "text", "text": t["note"]})
                for text in t.get("results") or []:
                    parts.append({"type": "text", "text": text})
                if t.get("change"):
                    keep_change = self.change_log_turns is None or users - u < self.change_log_turns
                    parts.append({"type": "text", "text": t["change"] if keep_change else
                                  "(what changed on this older screen is left out)"})
                pngs = t.get("pngs") or [t["png_b64"]]
                for j, b64 in enumerate(pngs):
                    seen += 1
                    if len(pngs) > 1:
                        parts.append({"type": "text", "text": f"(screenshot after action {j + 1})"})
                    if seen <= drop:
                        parts.append({"type": "text", "text": "(older screenshot omitted)" if not self.change_log else
                                      "(older screenshot omitted: what changed is in the text)"})
                    elif self.history_scale and seen < n_img:
                        parts.append({"type": "image_url", "image_url": {"url": self._scaled_url(b64)}})
                    else:
                        parts.append({"type": "image_url", "image_url": {"url": self._image_url(b64)}})
                msgs.append({"role": "user", "content": parts})
            else:
                msgs.append({"role": "assistant", "content": t["text"]})
        return msgs

    def _scaled_url(self, b64: str) -> str:
        """An older kept screenshot at history_scale of its size, in image_format."""
        key = (b64, self.history_scale, self.image_format)
        out = self._scaled.get(key)
        if out is None:
            from PIL import Image
            im = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
            im = im.resize((max(1, round(im.width * self.history_scale)), max(1, round(im.height * self.history_scale))),
                           Image.LANCZOS)
            buf = io.BytesIO()
            if self.image_format == "jpeg":
                im.save(buf, format="JPEG", quality=self.jpeg_quality)
            else:
                im.save(buf, format="PNG")
            out = base64.b64encode(buf.getvalue()).decode()
            if len(self._scaled) >= 32:
                self._scaled.pop(next(iter(self._scaled)))
            self._scaled[key] = out
        return f"data:image/{'jpeg' if self.image_format == 'jpeg' else 'png'};base64," + out

    def _image_url(self, b64: str) -> str:
        if self.image_format != "jpeg":
            return "data:image/png;base64," + b64
        out = self._jpeg.get(b64)
        if out is None:
            try:
                out = jpeg_b64(b64, self.jpeg_quality)
            except Exception:                         # not an image Pillow reads: send it as it came, never fail a turn
                out = ""
            if len(self._jpeg) >= 32:                 # more than any prompt keeps; the oldest goes first
                self._jpeg.pop(next(iter(self._jpeg)))
            self._jpeg[b64] = out
        return "data:image/jpeg;base64," + out if out else "data:image/png;base64," + b64

    def _tools_prompt(self) -> str:
        """The system-prompt lines for the extra tools. Without native tool calls the model gets no tool specs, so it
        is told each tool's parameters and the JSON form to write, which `_named_calls` reads."""
        if self.native_tools:
            return "Besides `computer` you have: " + "; ".join(f"`{t.name}` ({t.description})"
                                                               for t in self.tools.values())
        lines = ['Besides `computer` you have these tools. Call one by writing {"name": "<tool>", "arguments": {...}}:']
        lines += [f"- {t.name}: {t.description} Parameters: {json.dumps(t.parameters)}" for t in self.tools.values()]
        return "\n".join(lines)

    # ------------------------------------------------------------ turn API (used by run() and by benchmark adapters)
    def begin(self, task: str, png: bytes) -> Episode:
        w, h = png_size(png)
        mw, mh = self._size(w, h)
        ep = Episode(task, w, h, mw, mh)
        ep.turns.append({"role": "user", "pngs": [self._png(ep, png)]})
        ep.last_png = png
        return ep

    def _changes(self, ep: Episode, png: bytes) -> str | None:
        """change_log: the text that appeared and went on the screen since the model last saw it, or None when the
        screen's text cannot be read here."""
        t = time.monotonic()
        try:
            if ep.last_lines is None and ep.last_png is not None:
                ep.last_lines = plans.screen_lines(ep.last_png)
            now = plans.screen_lines(png)
        except Exception:  # noqa: BLE001 - no text reader (not macOS): the screenshot alone
            return None
        finally:
            ep.change_s = getattr(ep, "change_s", 0.0) + time.monotonic() - t
        before = ep.last_lines or []
        ep.last_png, ep.last_lines = png, now
        w, h = png_size(png)
        sx, sy = (1000.0 / w, 1000.0 / h) if self.coords == "norm1000" else (ep.mw / w, ep.mh / h)

        def place(ls):                            # where the model would click: its own coordinates
            return [(t, round(x * sx), round(y * sy)) for t, x, y in ls]
        return plans.describe_changes(place(before), place(now), most=self.change_log_lines)

    def _parse(self, args: dict, ep: Episode) -> tuple[Action | None, str]:
        if self.coords == "norm1000":
            return parse(args, ep.w, ep.h)
        a = dict(args)            # pixels of the image the model saw: onto the 0-1000 grid, then onto the screen
        for key in ("coordinate", "end_coordinate"):
            v = a.get(key)
            if isinstance(v, (list, tuple)) and len(v) >= 2:
                try:
                    a[key] = [float(v[0]) * 1000.0 / ep.mw, float(v[1]) * 1000.0 / ep.mh]
                except (TypeError, ValueError):
                    pass
        return parse(a, ep.w, ep.h, scale=1000.0)

    def decide(self, ep: Episode) -> Plan:
        """One model call: the actions to run next. Raises PolicyError when the call fails."""
        msgs = self._messages(ep.task, ep.mw, ep.mh, ep.turns)
        names = CLAUDE_STYLE_ACTIONS + (["screenshot"] if self.screenshot_action else [])
        if self.plan_mode:
            tools = [plans.plan_tool(self.plan_max, progress=self.progress_note)] if self.native_tools else None
        else:
            tools = [computer_tool(names)] + [t.spec() for t in self.tools.values()] if self.native_tools else None
        # Claude caches only where asked: OpenRouter's automatic caching (one top-level marker that follows the end of
        # the conversation) stands in for the reference implementation's per-turn breakpoints
        rec = self._chat(msgs, max_tokens=self.max_tokens, temperature=self.temperature, tools=tools,
                               tool_choice="auto" if tools else None,
                               extra={"cache_control": {"type": "ephemeral"}} if self.cache else None)
        if self.plan_mode:
            return self._decide_plan(rec, msgs, ep)
        calls = _named_calls(rec, tuple(self.tools))
        plan = Plan(rec, msgs, [], [])
        text = (rec.get("content") or "").strip()
        if text and rec.get("tool_calls"):
            plan.reply.append(text[:2000])
        for name, args in calls[: self.max_batch] if self.max_batch else calls:
            if name in self.tools:
                plan.actions.append(ToolCall(name, args))
                plan.reply.append(json.dumps({"tool": name, **args}, ensure_ascii=False))
                continue
            if self.tools and name != "computer":     # without tools, any name is read as `computer`, as before
                plan.reply.append(f"(unknown tool: {name})")
                break
            if self.screenshot_action and str(args.get("action", "")).lower() == "screenshot":
                plan.actions.append(None)
                plan.reply.append(json.dumps({"action": "screenshot"}))
                continue
            a, why = self._parse(args, ep)
            if a is None:
                plan.reply.append(f"(invalid action: {why})")
                break
            plan.reply.append(json.dumps({k: v for k, v in args.items() if v is not None}))
            if a.done:
                plan.done, plan.failed = a.type == "done", a.type == "fail"
                break
            plan.actions.append(a)
        if not calls:
            plan.cut_off = rec.get("finish_reason") == "length"
            plan.reply.append(text or ("(reply cut off at the token limit)" if plan.cut_off else "(no action)"))
            if self.end_on_text:   # the reference loop ends when the model answers without a tool call (any stop reason)
                plan.done = True
        return plan

    def _chat(self, *a, **kw) -> dict:
        """client.chat, sent again if it has not answered within hedge_after_s; the first reply wins."""
        if not self.hedge_after_s:
            return self.client.chat(*a, **kw)
        import concurrent.futures as cf
        t0 = time.monotonic()
        pool = cf.ThreadPoolExecutor(max_workers=2)
        try:
            first = pool.submit(self.client.chat, *a, **kw)
            try:
                return first.result(timeout=self.hedge_after_s)
            except cf.TimeoutError:
                pass
            second = pool.submit(self.client.chat, *a, **kw)
            done, _ = cf.wait([first, second], return_when=cf.FIRST_COMPLETED)
            win = next(iter(done))
            try:
                rec = win.result()
            except Exception:  # noqa: BLE001 - the other one may still answer
                rec = (second if win is first else first).result()
            rec = dict(rec)
            rec["s"], rec["hedged"] = time.monotonic() - t0, True
            return rec
        finally:
            pool.shutdown(wait=False)

    def _decide_plan(self, rec: dict, msgs: list, ep: Episode) -> Plan:
        """A plan reply: its steps, in order, as Action / ToolCall / plans.FindClick / plans.WaitFor; done or fail last
        (asked for: run() decides whether it counts)."""
        plan = Plan(rec, msgs, [], [])
        text = (rec.get("content") or "").strip()
        if text and rec.get("tool_calls"):
            plan.reply.append(text[:2000])
        if self.progress_note:
            note = _progress(rec)
            if note:
                plan.reply.append(f"Progress: {note[:300]}")
        steps = plans.plan_steps(rec) or []
        shell = next((t for t in self.tools.values() if t.name == "bash"), None)
        for st in steps[: self.plan_max]:
            kind = str(st.get("action", "")).lower().strip()
            plan.reply.append(json.dumps({k: v for k, v in st.items() if v is not None}, ensure_ascii=False))
            if kind in ("done", "fail"):
                plan.done, plan.failed = kind == "done", kind == "fail"
                break
            label = st.get("text")
            if kind in plans.LABELLED and isinstance(label, str) and label.strip() and \
                    label.strip().lower() not in plans.MODIFIERS:
                a, why = self._parse(st, ep)       # a labelled click: found by its label when it runs
                if a is None:
                    plan.reply.append(f"(invalid step: {why})")
                    break
                plan.actions.append(plans.FindClick(label.strip(), a.type, (a.x, a.y), fallback=(a.x, a.y)))
                continue
            if kind == "applescript":
                tool = self.tools.get("applescript")
                if tool is None or not isinstance(st.get("script"), str):
                    plan.reply.append("(invalid step: applescript needs a script, and the applescript tool)")
                    break
                plan.actions.append(ToolCall("applescript", {k: st[k] for k in ("script", "check") if st.get(k)}))
                continue
            if kind == "shell":
                if shell is None or not isinstance(st.get("command"), str):
                    plan.reply.append("(invalid step: shell needs a command, and a shell)")
                    break
                plan.actions.append(ToolCall("bash", {"command": st["command"]}))
            elif kind in ("click_text", "wait_for"):
                label = str(st.get("text") or "").strip()
                if not label:
                    plan.reply.append(f"(invalid step: {kind} needs text)")
                    break
                if kind == "wait_for":
                    t = min(plans.MAX_WAIT_S, max(0.5, float(st.get("timeout_s") or 5.0)))
                    plan.actions.append(plans.WaitFor(label, t))
                else:
                    near = None
                    if st.get("coordinate") is not None:
                        a, _ = self._parse({"action": "left_click", "coordinate": st["coordinate"]}, ep)
                        near = (a.x, a.y) if a else None
                    k = "double_click" if st.get("double") else "right_click" if st.get("right") else "click"
                    plan.actions.append(plans.FindClick(label, k, near))
            else:
                a, why = self._parse(st, ep)
                if a is None:
                    plan.reply.append(f"(invalid step: {why})")
                    break
                if a.done:
                    plan.done, plan.failed = a.type == "done", a.type == "fail"
                    break
                plan.actions.append(a)
        if not steps:
            plan.cut_off = rec.get("finish_reason") == "length"
            plan.reply.append(text or ("(reply cut off at the token limit)" if plan.cut_off else "(no action)"))
        return plan

    def _run_plan(self, plan: Plan, env: Env, seen: bytes, timing: dict) -> tuple[list, list]:
        """Run a plan's steps in order until one cannot go on. Returns (screenshots, texts for the model). Sets
        plan.done only when done followed a check that passed and every step ran."""
        pngs, results, notes = [], [], []
        changed, check, last_xy, ran = False, None, None, 0
        stop = None

        def acted(obs) -> None:
            pngs.append(obs.screenshot)
            for k in ("act_s", "settle_s", "obs_s"):
                timing[k] += env.last.get(k, 0.0)
            if env.last.get("wait"):
                timing.setdefault("waits", []).append(env.last["wait"])

        def look() -> bytes:
            t = time.monotonic()
            png = env.observe().screenshot
            timing["obs_s"] += time.monotonic() - t
            return png

        def find(label: str, near):
            t = time.monotonic()
            try:
                png = look()
                if self.ocr_near and near is not None:
                    xy = plans.find_text_near(png, label, near)
                    if xy:
                        timing["found_near"] = timing.get("found_near", 0) + 1
                        return xy
                return plans.find_text(png, label, near)
            finally:
                timing["find_s"] = timing.get("find_s", 0.0) + time.monotonic() - t
        n = len(plan.actions)
        for i, st in enumerate(plan.actions, 1):
            try:
                if isinstance(st, ToolCall):
                    t = time.monotonic()
                    out = self.call_tool(st)
                    timing["tool_s"] = timing.get("tool_s", 0.0) + time.monotonic() - t
                    results.append(f"{st.name} result:\n{out}")
                    changed = True
                    check = not plans.tool_failed(out)
                    if check and self.strict_done and not plans.printed(out):
                        check = None                # it ran, but shows nothing that proves the task is done
                    if not check:
                        stop = f"step {i} ({'shell' if st.name == 'bash' else st.name}) failed"
                        break
                elif isinstance(st, plans.WaitFor):
                    t_end, found = time.monotonic() + st.timeout_s, None
                    while True:
                        found = find(st.text, None)
                        if found or time.monotonic() >= t_end:
                            break
                        time.sleep(0.3)
                    check = bool(found)
                    if found and self.strict_done:
                        check = None                # text on the screen, not proof: it may have been there before
                    if not found:
                        stop = f'step {i}: "{st.text}" did not appear within {st.timeout_s:g} s'
                        break
                elif isinstance(st, plans.FindClick):
                    if self.plan_trust_first_click and not changed and st.fallback is not None:
                        xy = st.fallback                # nothing has acted yet: the screen is the one the model saw
                        timing["label_trusted"] = timing.get("label_trusted", 0) + 1
                    else:
                        xy = find(st.text, st.near or last_xy)
                    if xy and st.fallback is not None:
                        timing["label_found"] = timing.get("label_found", 0) + 1
                    if not xy and st.fallback is not None:   # not there: the click the model aimed, if still safe
                        timing["label_missed"] = timing.get("label_missed", 0) + 1
                        xy = st.fallback
                        if changed and plans.target_moved(seen, look(), xy[0], xy[1]):
                            stop = f'step {i}: no "{st.text}" on the screen, and the screen changed where it would click'
                            break
                    if not xy:
                        stop = f'step {i}: no "{st.text}" on the screen'
                        break
                    acted(env.act(Action(st.kind, x=xy[0], y=xy[1])))
                    changed, check, last_xy = True, None, xy
                else:
                    if changed and st.type in plans.POINTER and st.x is not None and \
                            plans.target_moved(seen, look(), st.x, st.y):
                        stop = f"step {i}: the screen changed where it would {st.type.replace('_', ' ')}"
                        break
                    acted(env.act(st))
                    changed, check = True, None
                    if st.x is not None:
                        last_xy = (st.x, st.y)
            except RuntimeError as e:          # no text reader here
                stop = f"step {i}: {e}"
                break
            ran = i
        if stop:
            notes.append(f"The plan ran {ran} of {n} steps and stopped: {stop}. Nothing after it ran.")
        if plan.done and (stop or (n and check is not True)):
            if not stop:
                notes.append(("done was not taken: it counts only right after a shell or applescript step that exits "
                              "0 and prints what it checked." if self.strict_done else
                              "done was not taken: it counts only right after a check that passed (wait_for, or a "
                              "shell command that exits 0).") + " Look at the screen, then call done if it is finished.")
            plan.done = False
        if plan.failed and stop:                # fail was never reached: the model sees why the plan stopped first
            plan.failed = False
        timing["plan_steps"], timing["plan_ran"] = n, ran
        if stop:
            timing["plan_stop"] = stop[:120]
        last = plan.actions[ran - 1] if ran else None
        if stop or (changed and not isinstance(last, (Action, plans.FindClick))):
            pngs.append(look())
        return pngs, results + notes

    def feed(self, ep: Episode, plan: Plan, pngs: list[bytes], results: list[str] | None = None,
             note: str | None = None) -> None:
        """Record the turn: the model's reply, then the tool results (text) and the screenshot(s) taken after its
        actions. `note` is text the model reads with them (an action the person did not allow)."""
        ep.turns.append({"role": "assistant", "text": "\n".join(plan.reply) or "(no action)"})
        keep = pngs if self.screenshot_each_action else pngs[-1:]
        turn = {"role": "user", "pngs": [self._png(ep, p) for p in keep]}
        if results:
            turn["results"] = list(results)
        if self.change_log and pngs:
            change = self._changes(ep, pngs[-1])
            if change:
                turn["change"] = change
        if plan.cut_off:            # a thinking model ran out of tokens mid-reasoning on 22% of turns (docs/benchmarks.md)
            turn["note"] = CUT_OFF_NOTE
        if note:
            turn["note"] = note
        ep.turns.append(turn)

    # ------------------------------------------------------------ the loop
    def call_tool(self, call: ToolCall) -> str:
        """Run one tool call. A handler that raises is reported to the model, not raised."""
        try:
            return str(self.tools[call.name].handler(call.args))
        except Exception as e:  # noqa: BLE001 - the model sees the error and can try something else
            return f"error: {type(e).__name__}: {str(e)[:500]}"

    def run(self, task: str, env: Env, max_steps: int = 40, on_step: Callable | None = None, *,
            timeout_s: float | None = None, cancel: threading.Event | None = None,
            raise_desktop_errors: bool = True) -> RunResult:
        """Run `task` on an attached desktop. on_step(turn, actions, timing) is called after each model turn.
        result.status says how it ended (RunStatus).

        timeout_s: a deadline for the whole run. It is checked between model turns, and each model call gets at most
        the time left; the result's status is then "timeout". cancel: a threading.Event; once it is set (from any
        thread) the run ends before its next model call with status "cancelled". Both keep the steps, actions and
        usage so far. raise_desktop_errors: a desktop that fails (a screenshot or an action raises) raises out of
        run(), as it always has; False returns it as a result with status "error" (what legato.run and `legato run`
        do). An exception raised by on_step is always raised."""
        res, t0 = RunResult(task), time.monotonic()
        lim = _Limits(timeout_s, cancel)
        clients = [c for c in (self.client, getattr(self.planner, "client", None)) if isinstance(c, PolicyClient)]
        for c in clients:
            c.deadline = lim.deadline
        mine: list = []                             # on_step raised: the caller's own exception, never a result

        def guarded(*a):
            try:
                return on_step(*a)
            except BaseException:
                mine.append(True)
                raise
        step = guarded if on_step is not None and not raise_desktop_errors else on_step
        try:
            if lim.hit(res):                        # cancelled or out of time before it started
                pass
            elif self.planner is not None and self.planner.style == "point":
                from legato.point import run_pointed
                run_pointed(self, task, env, max_steps=max_steps, on_step=step, res=res, limits=lim)
            elif self.planner is not None:
                from legato.delegate import run_delegated
                run_delegated(self, task, env, max_steps=max_steps, on_step=step, res=res, limits=lim)
            else:
                self._loop(task, env, max_steps, step, res, lim)
        except Exception as e:  # noqa: BLE001 - the desktop failed: returned with what was done so far, if asked
            if raise_desktop_errors or mine:
                raise
            res.status, res.error = "error", _desktop_error(e)
        finally:
            for c in clients:
                c.deadline = None
            res.wall_s = time.monotonic() - t0
        return res

    def _loop(self, task: str, env: Env, max_steps: int, on_step, res: RunResult, lim: _Limits) -> None:
        """run() without a planner: one model turn, then its actions, until done, fail or a limit."""
        res.status = "max_steps"                    # unless a turn ends it first
        obs = env.observe()
        ep = self.begin(task, obs.screenshot)
        seen = obs.screenshot                       # the screenshot the model plans on
        idle = 0                                    # replies in a row with no action (end_fix)
        if self.plan_mode:                          # the text reader loads its model once (~10 s): during the first call
            threading.Thread(target=plans.warm_up, args=(obs.screenshot,), daemon=True).start()
        for turn in range(max_steps):
            if lim.hit(res):
                break
            try:
                plan = self.decide(ep)
            except PolicyError as e:
                _book(res.usage, error=e)
                if not lim.hit(res):                # a call cut short by the deadline is a timeout
                    res.status, res.error = "error", str(e)[:500]
                break
            _book(res.usage, plan.rec)
            timing = {"model_s": plan.rec["s"], "act_s": 0.0, "settle_s": 0.0, "obs_s": 0.0}
            pngs, results, stale, ran, note, denied = [], [], False, [], None, None
            if self.plan_mode:
                try:
                    pngs, results = self._run_plan(plan, env, seen, timing)
                except Stopped as e:              # the person's stop, during a step
                    res.stopped, res.error = True, f"stopped by the person: {e}"
                except Denied as e:               # the person's no to a step: the rest did not run
                    note = _refused(e)
                    denied = f"{'not possible' if isinstance(e, Unavailable) else 'not allowed'}: {e}"
                    obs = env.observe()
                    pngs, results = [obs.screenshot], []
                except Exception:                 # the desktop failed: the turn counts, then run() decides
                    res.steps += 1
                    res.timings.append(timing)
                    raise
                ran = list(plan.actions[: timing.get("plan_ran", 0)])
            for a in ([] if self.plan_mode else plan.actions):
                if isinstance(a, ToolCall):
                    t = time.monotonic()
                    results.append(f"{a.name} result:\n{self.call_tool(a)}")
                    timing["tool_s"] = timing.get("tool_s", 0.0) + time.monotonic() - t
                    stale = True    # a tool may have changed the screen
                    ran.append(a)
                    continue
                try:
                    obs = env.observe() if a is None else env.act(a)
                except Stopped as e:              # the person's stop: end here, with what was done so far
                    res.stopped, res.error = True, f"stopped by the person: {e}"
                    break
                except Denied as e:               # the person's no: nothing ran; the model is told and chooses again
                    note = _refused(e)
                    denied = f"{'not possible' if isinstance(e, Unavailable) else 'not allowed'}: {a.describe()}"
                    obs = env.observe()
                    pngs.append(obs.screenshot)
                    stale = False
                    break
                except Exception:                 # the desktop failed: the turn and what ran count, then run() decides
                    res.steps += 1
                    res.actions += [x.describe() if x else "screenshot" for x in ran]
                    res.timings.append(timing)
                    raise
                ran.append(a)
                pngs.append(obs.screenshot)
                stale = False
                for k in ("act_s", "settle_s", "obs_s"):
                    timing[k] += env.last.get(k, 0.0)
            if stale and not res.stopped:
                obs = env.observe()
                pngs.append(obs.screenshot)
                timing["obs_s"] += env.last.get("obs_s", 0.0)
            if self.end_fix:
                silent = not plan.rec.get("tool_calls") and not plans._objects(str(plan.rec.get("content") or ""))
                if silent and not plan.actions and not plan.done and not plan.failed and not plan.cut_off:
                    idle += 1
                    if idle >= 2:                   # it has said it is finished, twice, without the done action
                        plan.done, timing["ended"] = True, "no action twice"
                    else:
                        results = list(results or []) + [END_NOTE]
                else:
                    idle = 0
            res.steps += 1
            res.actions += [a.describe() if a else "screenshot" for a in ran] + ([denied] if denied else [])
            if self.plan_mode and (plan.done or plan.failed):
                res.actions.append("done" if plan.done else "fail")
            res.timings.append(timing)
            if on_step:
                on_step(turn, ran, timing)
            if res.stopped:
                res.status = "stopped"
                break
            if not note:                          # a "done" after an action that was not allowed does not count
                res.done, res.failed = plan.done, plan.failed
                if plan.done or plan.failed:
                    res.status = "done" if plan.done else "failed"
                    res.answer = _answer(plan.rec, plan.actions)
                    break
            self.feed(ep, plan, pngs or [obs.screenshot], results, note)
            seen = (pngs or [obs.screenshot])[-1]


def _answer(rec: dict, actions: list) -> str:
    """What the model said when it ended: the text of its done (or fail) step, else its reply without the tool calls
    some models write into it."""
    said = [a.text for a in actions if isinstance(a, Action) and a.done and a.text]
    if not said:
        try:
            said = [st["text"] for st in plans.plan_steps(rec) or []
                    if st.get("action") in ("done", "fail") and isinstance(st.get("text"), str) and st["text"].strip()]
        except Exception:  # noqa: BLE001 - an odd reply: fall back to its text
            said = []
    if said:
        return said[-1].strip()[:2000]
    text = rec.get("content") if isinstance(rec.get("content"), str) else ""
    text = re.sub(r"<tool_call>.*?(</tool_call>|$)|<function.*?(</function>|$)|```(?:json)?\s*\{.*?\}\s*```", "",
                  text, flags=re.S)
    text = "\n".join(line for line in text.splitlines() if not line.strip().startswith('{"'))
    return text.strip()[:2000]


def _parse_pixels(args: dict, w: int, h: int) -> tuple[Action | None, str]:
    """Pixel coordinates of a w x h screenshot onto the same w x h screen."""
    return Harness("m", "http://x/v1", "", coords="pixels", model_size=None)._parse(args, Episode("", w, h, w, h))
