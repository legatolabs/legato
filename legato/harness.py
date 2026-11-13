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
import time
from dataclasses import dataclass, field

from legato.actions import Action, parse
from legato.env import Env, png_size
from legato.rl.policy import PolicyClient, PolicyConfig, PolicyError

CLAUDE_STYLE_ACTIONS = ["left_click", "right_click", "double_click", "left_click_drag", "type", "key", "scroll",
                        "wait", "done", "fail"]

SYSTEM = """You control a computer desktop ({w}x{h} pixels) to complete the user's task. Each turn you see the \
current screenshot. Act by calling the `computer` tool.
{coords}
Actions: left_click, right_click, double_click (coordinate); left_click_drag (coordinate to end_coordinate); \
type (text, into the focused field); key (keys, e.g. "ctrl+s", "Return", "Tab"); scroll (coordinate, direction \
up/down/left/right, amount); wait; done (the task is complete and saved); fail (the task cannot be done).
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


def _calls(rec: dict) -> list:
    """Tool-call argument dicts from a reply: native tool_calls first, else JSON objects in the text."""
    out = []
    for tc in rec.get("tool_calls") or []:
        a = (tc.get("function") or {}).get("arguments")
        if isinstance(a, str):
            try:
                a = json.loads(a)
            except json.JSONDecodeError:
                continue
        if isinstance(a, dict):
            out.append(a)
    if out:
        return out
    text, depth, start = rec.get("content") or "", 0, None
    for i, ch in enumerate(text):                # top-level {...} objects in the text, in order
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
                    a = a.get("arguments", a) if "action" not in a else a
                    if isinstance(a, dict) and "action" in a:
                        out.append(a)
    return out


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


@dataclass
class Episode:
    """One task's conversation: the screen size, the size the model sees, and the turns so far."""
    task: str
    w: int
    h: int
    mw: int
    mh: int
    turns: list = field(default_factory=list)   # {"role": "user", "pngs": [b64...]} / {"role": "assistant", "text"}


@dataclass
class Plan:
    """One model turn: what to run. An action of None means "take a screenshot" (the reference's screenshot action)."""
    rec: dict
    messages: list
    actions: list
    reply: list
    done: bool = False
    failed: bool = False
    cut_off: bool = False       # the reply hit the token limit before any action (a thinking model ran out of room)


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
    usage: dict = field(default_factory=lambda: {"prompt": 0, "completion": 0, "cached": 0, "cost_usd": 0.0})

    def report(self) -> str:
        tot = {k: sum(t.get(k, 0.0) for t in self.timings) for k in ("model_s", "act_s", "settle_s", "obs_s")}
        other = max(0.0, self.wall_s - sum(tot.values()))
        share = lambda v: f"{100 * v / self.wall_s:.0f}%" if self.wall_s else "-"  # noqa: E731
        status = "done" if self.done else "failed" if self.failed else self.error or "stopped"
        return (f"{status} in {self.wall_s:.1f} s, {self.steps} model turns, {len(self.actions)} actions\n"
                f"model {tot['model_s']:.1f} s ({share(tot['model_s'])}) · actions {tot['act_s']:.1f} s "
                f"({share(tot['act_s'])}) · waiting for the screen {tot['settle_s']:.1f} s ({share(tot['settle_s'])}) · "
                f"screenshots {tot['obs_s']:.1f} s ({share(tot['obs_s'])}) · other {other:.1f} s")


class Harness:
    """A computer-use agent loop for any OpenAI-compatible model."""

    def __init__(self, model: str, base_url: str, api_key: str = "", *, coords: str | None = None,
                 image_max: int | None = 8, fold: int = 4, max_batch: int | None = 3, step_timeout_s: float = 60.0,
                 retries: int = 1, temperature: float = 0.0, max_tokens: int = 4096, native_tools: bool = True,
                 system: str | None = None, screenshot_each_action: bool = False, end_on_text: bool = False,
                 screenshot_action: bool = False, model_size: str | tuple | None = "auto",
                 cache_breakpoints: bool | None = None, extra_body: dict | None = None, profile: str = "legato"):
        """coords: "norm1000" or "pixels" (default: pixels for Claude, norm1000 otherwise). image_max: screenshots kept
        in the prompt (None keeps all). max_batch: tool calls run per turn (None runs all). model_size: the size
        screenshots are sent at ("auto": Anthropic's recommended size for Claude, the screen size otherwise; None:
        the screen size; or (w, h)). cache_breakpoints: ask for prompt caching with a top-level cache_control (default: on for
        Claude; OpenRouter and Anthropic-compatible APIs read it)."""
        coords = coords or ("pixels" if is_claude(model) else "norm1000")
        if coords not in COORDS:
            raise ValueError(f"coords must be one of {list(COORDS)}")
        self.model, self.profile = model, profile
        self.client = PolicyClient(PolicyConfig(base_url=base_url, model=model, api_key=api_key, version=profile,
                                                timeout_s=step_timeout_s, retries=retries, logprobs=False,
                                                token_ids=False, extra_body=dict(extra_body or {})))
        self.coords, self.image_max, self.fold = coords, image_max, max(1, fold)
        self.max_batch = max(1, max_batch) if max_batch else None
        self.temperature, self.max_tokens, self.native_tools, self.system = temperature, max_tokens, native_tools, system
        self.screenshot_each_action, self.end_on_text, self.screenshot_action = (screenshot_each_action, end_on_text,
                                                                                 screenshot_action)
        self.model_size = model_size
        self.cache = is_claude(model) if cache_breakpoints is None else cache_breakpoints

    @classmethod
    def reference(cls, model: str, base_url: str, api_key: str = "", **kw) -> "Harness":
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
        sys_text = (self.system or SYSTEM).format(w=w, h=h, coords=COORDS[self.coords], batch=self.max_batch or "several")
        n_img = sum(len(t.get("pngs") or [t["png_b64"]]) for t in turns if t["role"] == "user")
        excess = max(0, n_img - self.image_max) if self.image_max is not None else 0
        drop = -(-excess // self.fold) * self.fold if excess else 0     # round up to a whole chunk
        msgs, seen, first = [{"role": "system", "content": sys_text}], 0, True
        for t in turns:
            if t["role"] == "user":
                parts = [{"type": "text", "text": f"Task: {task}"}] if first else []
                first = False
                if t.get("note"):
                    parts.append({"type": "text", "text": t["note"]})
                pngs = t.get("pngs") or [t["png_b64"]]
                for j, b64 in enumerate(pngs):
                    seen += 1
                    if len(pngs) > 1:
                        parts.append({"type": "text", "text": f"(screenshot after action {j + 1})"})
                    if seen <= drop:
                        parts.append({"type": "text", "text": "(older screenshot omitted)"})
                    else:
                        parts.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}})
                msgs.append({"role": "user", "content": parts})
            else:
                msgs.append({"role": "assistant", "content": t["text"]})
        return msgs

    # ------------------------------------------------------------ turn API (used by run() and by benchmark adapters)
    def begin(self, task: str, png: bytes) -> Episode:
        w, h = png_size(png)
        mw, mh = self._size(w, h)
        ep = Episode(task, w, h, mw, mh)
        ep.turns.append({"role": "user", "pngs": [self._png(ep, png)]})
        return ep

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
        tools = [computer_tool(names)] if self.native_tools else None
        # Claude caches only where asked: OpenRouter's automatic caching (one top-level marker that follows the end of
        # the conversation) stands in for the reference implementation's per-turn breakpoints
        rec = self.client.chat(msgs, max_tokens=self.max_tokens, temperature=self.temperature, tools=tools,
                               tool_choice="auto" if tools else None,
                               extra={"cache_control": {"type": "ephemeral"}} if self.cache else None)
        calls = _calls(rec)
        plan = Plan(rec, msgs, [], [])
        text = (rec.get("content") or "").strip()
        if text and rec.get("tool_calls"):
            plan.reply.append(text[:2000])
        for args in calls[: self.max_batch] if self.max_batch else calls:
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

    def feed(self, ep: Episode, plan: Plan, pngs: list[bytes]) -> None:
        """Record the turn: the model's reply, then the screenshot(s) taken after its actions."""
        ep.turns.append({"role": "assistant", "text": "\n".join(plan.reply) or "(no action)"})
        keep = pngs if self.screenshot_each_action else pngs[-1:]
        turn = {"role": "user", "pngs": [self._png(ep, p) for p in keep]}
        if plan.cut_off:            # a thinking model ran out of tokens mid-reasoning on 22% of turns (docs/benchmarks.md)
            turn["note"] = CUT_OFF_NOTE
        ep.turns.append(turn)

    # ------------------------------------------------------------ the loop
    def run(self, task: str, env: Env, max_steps: int = 30, on_step=None) -> RunResult:
        """Run `task` on an attached desktop. on_step(turn, actions, timing) is called after each model turn."""
        res, t0 = RunResult(task), time.monotonic()
        obs = env.observe()
        ep = self.begin(task, obs.screenshot)
        for turn in range(max_steps):
            try:
                plan = self.decide(ep)
            except PolicyError as e:
                res.error = str(e)[:300]
                break
            u = plan.rec.get("usage") or {}
            for k in ("prompt", "completion", "cached"):
                res.usage[k] += u.get(k) or 0
            res.usage["cost_usd"] += u.get("cost") or 0.0
            timing = {"model_s": plan.rec["s"], "act_s": 0.0, "settle_s": 0.0, "obs_s": 0.0}
            pngs = []
            for a in plan.actions:
                obs = env.observe() if a is None else env.act(a)
                pngs.append(obs.screenshot)
                for k in ("act_s", "settle_s", "obs_s"):
                    timing[k] += env.last.get(k, 0.0)
            res.steps += 1
            res.actions += [a.describe() if a else "screenshot" for a in plan.actions]
            res.timings.append(timing)
            res.done, res.failed = plan.done, plan.failed
            if on_step:
                on_step(turn, plan.actions, timing)
            if plan.done or plan.failed:
                break
            self.feed(ep, plan, pngs or [obs.screenshot])
        res.wall_s = time.monotonic() - t0
        return res


def _parse_pixels(args: dict, w: int, h: int) -> tuple[Action | None, str]:
    """Pixel coordinates of a w x h screenshot onto the same w x h screen."""
    return Harness("m", "http://x/v1", coords="pixels", model_size=None)._parse(args, Episode("", w, h, w, h))
