"""Planner and pointer: a strong model says what to do, a fast model finds where.

    planner = Planner("openai/gpt-6.1-terra", "https://openrouter.ai/api/v1", key, style="point")
    harness = Harness("qwen/qwen3.8-27b", base_url, key, planner=planner)
    harness.run(task, env)

Each planner call sees the task, the screen and what has been run so far, and names the next actions as far ahead as
it is sure of without seeing the screen in between: clicks and drags on things it describes ("the Save button in the
dialog"), keys, typed text, scrolls, waits. The harness runs them in order. Keys and text are sent as written. For a
click, a drag or a scroll the planner may give the coordinate itself (`at`) when the thing is on the screenshot it is
looking at; the harness uses it if the screen around that point is still as the planner saw it. Otherwise the
harness's own model (the pointer) is asked one thing: where the described thing is on the screenshot in front of it.
If it is not there, the rest of the batch is dropped and the planner sees the screen. After each batch the planner is
called again, and only the planner ends the task. That call also gets up to FRAMES screens from during the batch (the
harness has one after every action), so the planner can see what its actions did and not only where they ended.

Model calls a task = planner calls + pointer calls. Nothing is spent on reporting a step or checking it.

With Harness(progress_note=True) every planner call also writes one line of what is done and what is left, kept in
the list of actions it reads next (the same option a plan has). `Pointing` is the same thing a step at a time, for a
caller that runs the actions itself: `legato rl collect --harness legato --harness-opts '{"planner": {...}}'`.
"""
from __future__ import annotations

import base64
import json
import re
import time
from typing import TYPE_CHECKING, Callable

from legato.rl.policy import PolicyError
from legato.safety import Denied, Stopped, Unavailable
from legato.tools import ToolCall, clip

if TYPE_CHECKING:
    from legato.env import Env
    from legato.harness import Harness, RunResult

PLANNER_SYSTEM = """You operate a computer desktop through a fast assistant that can only find things on the screen: \
you say what to do, it finds where. You see the task, the current screen and what has been run so far.
Call the `act` tool:
- status "continue" with the next actions to run in order (at most {max_actions}). Go as far ahead as you are sure of \
without seeing the screen in between (for example: click a menu, click its item; click a field, select all, type, \
press Return). Stop the list where you need to see the result.
- status "done" when the screen shows the task is complete and saved. If the task asks for information, put the \
answer itself in "answer" (the number, name or sentence asked for).
After a list has run you may be shown screens from during it, each labelled with the action it followed, before \
the screen as it is now. Check on them that every action did what you meant (the right thing was selected, the \
right dialog opened) before you go on or say done; redo what went wrong.
- status "fail" when the task cannot be done on this computer, with why in "answer".
Actions:
- {{"click": "<what to click>"}}, with "button": "double", "right" or "triple" when it is not a plain click, and \
"hold": "shift" or "ctrl" to hold a key. Describe the thing so it can be found on the screenshot without knowing the \
task: its visible text or icon, what kind of control it is, and where it is (for example: the "Fill Cells" item in \
the open Sheet menu; the slide 4 thumbnail in the left Slides pane; cell B12 in the spreadsheet grid).
- {{"key": "ctrl+s"}} and {{"type": "text"}}: sent exactly as written to whatever has the keyboard focus.
- {{"scroll": "<where>", "direction": "down", "amount": 3}}
- {{"drag": "<what to drag>", "to": "<where to drop it>"}}
- Add "at": [x, y] to a click, a scroll or a drag (and "to_at" for where a drag ends) when the thing is on the \
screenshot in front of you: the coordinate of its centre. It is then used directly, which is faster and more exact. \
Always give the description too. Leave "at" out for a thing that only appears, or moves, after an earlier action in \
the list (an item of a menu the list opens, a button of a dialog it opens): the assistant finds that one on the \
screen as it is then. The screen now is {w}x{h} pixels; "at" is [x, y] in its pixels, from the top-left corner.
- {{"wait": 5}}: only for something that keeps loading for several seconds. After every action the screen is \
already waited for until it stops changing, before the next action runs: do not add waits between actions.
Prefer reliable keyboard shortcuts. Save files in their current format. If something was not found or did not work, \
do it another way."""

ACT_TOOL = {"type": "function", "function": {
    "name": "act", "description": "The next actions to run, or the task's end.",
    "parameters": {"type": "object", "properties": {
        "status": {"type": "string", "enum": ["continue", "done", "fail"]},
        "actions": {"type": "array", "description": "with continue: the actions to run now, in order; [] with done or fail",
                    "items": {"type": "object", "properties": {
            "click": {"type": "string"}, "button": {"type": "string", "enum": ["left", "double", "right", "triple"]},
            "hold": {"type": "string"}, "key": {"type": "string"}, "type": {"type": "string"},
            "scroll": {"type": "string"}, "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
            "amount": {"type": "number"}, "drag": {"type": "string"}, "to": {"type": "string"},
            "at": {"type": "array", "items": {"type": "number"}},
            "to_at": {"type": "array", "items": {"type": "number"}},
            "wait": {"type": "number"}}}},
        "answer": {"type": "string", "description": "with done: what the task asked to find out, if anything; "
                                                    "with fail: why"}},
        "required": ["status", "actions"]}}}

POINTER_SYSTEM = """You find things on a screenshot of a computer desktop ({w}x{h} pixels). You are given a \
description of one thing. Call the `point` tool with the coordinate of its centre, or with found false if it is not \
on the screen. {coords}"""

POINTER_TEXT = ("\nAnswer with JSON only, nothing else: {{\"found\": true, \"coordinate\": [x, y]}} or "
                "{{\"found\": false}}.")       # for a model without tool calls: Harness(native_tools=False)

POINT_TOOL = {"type": "function", "function": {
    "name": "point", "description": "Where the described thing is on the screenshot.",
    "parameters": {"type": "object", "properties": {
        "found": {"type": "boolean"},
        "coordinate": {"type": "array", "items": {"type": "number"}, "description": "[x, y] of its centre"}},
        "required": ["found"]}}}

BUTTONS = {"left": "left_click", "double": "double_click", "right": "right_click", "triple": "triple_click"}
# Keys that only edit the focused field or move its caret: nothing opens, so what follows needs no settled screen.
INERT_KEYS = {"backspace", "delete", "home", "end", "left", "right", "up", "down", "tab", "shift+tab", "ctrl+a",
              "ctrl+c", "ctrl+x", "ctrl+z", "ctrl+left", "ctrl+right", "ctrl+backspace", "ctrl+delete", "ctrl+home",
              "ctrl+end", "shift+left", "shift+right", "shift+up", "shift+down", "shift+home", "shift+end",
              "ctrl+shift+left", "ctrl+shift+right", "ctrl+shift+home", "ctrl+shift+end"}
QUICK_WAIT_S = 0.1
FRAMES, FRAME_SIDE = 3, 1024       # screens from during the last batch shown to the planner, and their longest side


def pick_frames(frames: list, n: int = FRAMES) -> list:
    """Up to n of a batch's screens, (action number, png) each, spread evenly, without the last one (the planner gets
    that as the screen now) and without screens identical to the one after them."""
    mid = [f for f, nxt in zip(frames, frames[1:]) if f[1] != nxt[1]]
    if len(mid) <= n:
        return mid
    return [mid[round((i + 1) * (len(mid) + 1) / (n + 1)) - 1] for i in range(n)]


def small_png(png: bytes, side: int = FRAME_SIDE) -> bytes:
    from legato.env import png_size
    from legato.harness import resize_png
    w, h = png_size(png)
    k = side / max(w, h)
    return png if k >= 1 else resize_png(png, (max(1, round(w * k)), max(1, round(h * k))))


def inert(x: dict) -> bool:
    """A planner action that only edits the focused field: text without a line break, or one of INERT_KEYS."""
    if "type" in x:
        return "\n" not in str(x["type"]) and "\r" not in str(x["type"])
    if "key" in x:
        return "+".join(k.strip().lower() for k in str(x["key"]).split("+") if k.strip()) in INERT_KEYS
    return False


# A coordinate the planner gave is used only when the screen there is as the planner saw it: at most AT_PIXELS pixels
# changed by over AT_DELTA grey levels in the AT_HALF-px box around it (a blinking caret is about 36 px), and at most
# AT_SCREEN of the 4x-downscaled screen changed (a new window, dialog or page).
AT_HALF, AT_PIXELS, AT_DELTA, AT_SCREEN = 40, 64, 24, 0.25


def moved(before_png: bytes, after_png: bytes, x: float, y: float) -> str | None:
    """Why a point chosen on `before_png` (screen pixels) should not be used on `after_png`; None if it can."""
    import io

    from PIL import Image, ImageChops
    if before_png is after_png or before_png == after_png:
        return None
    try:
        b0 = Image.open(io.BytesIO(before_png)).convert("L")
        b1 = Image.open(io.BytesIO(after_png)).convert("L")
    except Exception:  # noqa: BLE001 - a screenshot that cannot be read cannot vouch for the point
        return "the screens could not be compared"
    if b0.size != b1.size:
        return "the screen size changed"

    def over(d) -> int:
        return d.point(lambda v: 255 if v > AT_DELTA else 0).histogram()[255]
    w, h = b0.size
    x, y = int(x), int(y)
    box = (max(0, x - AT_HALF), max(0, y - AT_HALF), min(w, x + AT_HALF), min(h, y + AT_HALF))
    if box[2] > box[0] and box[3] > box[1] and over(ImageChops.difference(b0.crop(box), b1.crop(box))) > AT_PIXELS:
        return "the screen changed there"
    sw, sh = max(1, w // 4), max(1, h // 4)
    s0, s1 = b0.resize((sw, sh), Image.BILINEAR), b1.resize((sw, sh), Image.BILINEAR)
    if over(ImageChops.difference(s0, s1)) > AT_SCREEN * sw * sh:
        return "most of the screen changed"
    return None


def _args(rec: dict) -> dict:
    from legato.delegate import _plan_args
    return _plan_args(rec)


def _actions(a: dict) -> list:
    """The actions in a planner's reply. Tool calling through some hosts is loose (Qwen3.8-27B through OpenRouter):
    one action as an object, or under "action", "actions.1", "actions[0]"."""
    acts = a.get("actions")
    if isinstance(acts, dict):
        acts = [acts]
    if not isinstance(acts, list):
        acts = []
        for k in sorted(k for k in a if re.fullmatch(r"actions?(\W*\d+\W*)?", str(k))):
            v = a[k]
            acts += v if isinstance(v, list) else [v]
    return [x for x in acts if isinstance(x, dict)]


def _point_args(rec: dict) -> dict:
    """The pointer's answer: its tool call or JSON, else the first [x, y] or (x, y) pair in its text (a small
    grounding model often answers in its own notation, e.g. click(start_box='(412,96)') or <point>412 96</point>)."""
    import re
    a = _args(rec)
    if a:
        return a
    text = str(rec.get("content") or "")
    if re.search(r"not (found|there|visible|on the screen)|\bfound\W+false", text, re.I):
        return {"found": False}
    m = re.search(r"[\[(<>]\s*(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)\s*[\])<>]", text)
    return {"found": True, "coordinate": [float(m.group(1)), float(m.group(2))]} if m else {}


SHELL_NOTE = """
You also have the computer's shell. Take the fastest route that gets the task right: a command wherever one can do \
the step or read what you need (files, documents, text, settings, an app's own data), the screen for what only the \
screen reaches. Read a file with a command rather than opening it in a window.
- {{"shell": "<command>"}}: runs in {shell} on this computer; its output and exit code are in the list you read next.{osa}
Several of these can go in one list. Their output is shown to you before you say done: check it, and the screen, then \
say done."""
OSA_NOTE = """
- {{"applescript": "<script>"}}: runs with osascript; prefer it for Calendar, Reminders, Notes, Contacts, Finder and \
System Events."""
SHELL_PROPS = {"shell": {"type": "string"}, "applescript": {"type": "string"}}
NOTES_NOTE = """
Every call may also carry `notes`: what you have worked out and must not lose (values read from a file or a screen, \
the items still to do, what is already checked). Your notes are given back to you at every later call, in full, and \
a new `notes` replaces them: keep what still matters. Work from your notes instead of reading the same thing twice."""
NOTES = {"type": "string", "description": "what you have worked out and must keep; replaces your earlier notes"}
NOTES_MAX = 4000
EXPECT_NOTE = """
Speed: say what each action will make true. Whenever an action opens or changes a window, add "expect" to it, and \
the next action starts the moment that is true instead of after a wait for the whole screen to stop changing.
- "expect": {{"title": "<text>"}}: the window in front has this text in its title once the action has worked. Use it \
on every action that opens a page (a click on a link, Return on an address: a distinctive word of the page's \
name), starts or switches to a program (its name), opens a dialog (its name, such as "Save As" or "Run") or saves \
or renames a document (the file's name).
- "expect": {{"file": "<full path>"}}: this file exists once the action has worked (a save, an export, a download).
For example: {{"key": "Return", "expect": {{"title": "Hacker News"}}}}; {{"click": "the Switzerland link", \
"expect": {{"title": "Switzerland"}}}}; {{"key": "ctrl+s", "expect": {{"title": "Save As"}}}}.
Use only text you are sure will be in the title and that is not in it already. Leave "expect" out only for an \
action that changes no window title and creates no file (typing, a click inside a page or a drawing, a scroll)."""
EXPECT_PROP = {"expect": {"type": "object", "description": "what will be true once this action has worked; add it to "
                          "every action that opens a page, a program or a dialog, or saves a file",
                          "properties": {"title": {"type": "string"}, "file": {"type": "string"}}}}
EXPECT_TIMEOUT_S = 1.5             # an expectation that does not come true in this long: go on with the screen as it is
EXPECT_RULE = {"floor": 0.0, "quiet": 0.12, "min_same": 2}     # after it is true: a short quiet, so the frame shows it
EXPECT_QUIET_CAP_S = 0.5
SHELL_OUT = 1500                   # characters of a command's output kept in the list the planner reads


ANSWER_ASK = ("You said done without an answer. This is the screen now: if the task asks for information, say done "
              "again with the answer itself in `answer` (the number, name or sentence asked for); if what you need "
              "is not on the screen yet, go on with the actions that get it. If the task asks for no information, "
              "say done again.")


PROGRESS = {"type": "string", "description": "one line: what is done so far and what is left"}
PROGRESS_NOTE = ("\nEvery call also carries `progress`: one line, what is done so far, what you have checked and "
                 "what is left. Your next call sees only the task, the list of actions run with these lines, and the "
                 "screen, so your progress lines are how you keep your place: do not check again what a line says is "
                 "checked.")


def act_tool(progress: bool = False, shell: tuple = (), notes: bool = False, expect: bool = False) -> dict:
    """The planner's `act` tool (progress: a required one-line `progress`, as Harness(progress_note=True) asks of
    a plan; shell: the names among "shell" and "applescript" its actions may carry; notes: a `notes` text it keeps;
    expect: its actions may carry an `expect`)."""
    if not progress and not shell and not notes and not expect:
        return ACT_TOOL
    f = dict(ACT_TOOL["function"])
    f["parameters"] = par = dict(f["parameters"])
    if shell or expect:
        items = par["properties"]["actions"]["items"]
        items = {**items, "properties": {**items["properties"], **{k: SHELL_PROPS[k] for k in shell},
                                         **(EXPECT_PROP if expect else {})}}
        par["properties"] = {**par["properties"], "actions": {**par["properties"]["actions"], "items": items}}
    if notes:
        par["properties"] = {**par["properties"], "notes": NOTES}
    if progress:
        par["properties"] = {**par["properties"], "progress": PROGRESS}
        par["required"] = ["status", "actions", "progress"]
    return {"type": "function", "function": f}


class Pointing:
    """One task in the planner-and-pointer style, a step at a time, for a caller that runs the actions itself
    (run_pointed below for Harness.run; legato.rl's rollout loop):

        s = Pointing(harness, task, png)
        while True:
            actions = s.next(png)                 # run these now, in order; [] when the task has ended
            if not actions:
                break                             # s.status is "done" or "fail", or s.error says why it stopped
            for a in actions:
                png = <run a and wait for the screen>
                s.ran(png)

    next() calls the planner when no planned action is left, and gives back the planned actions up to the next one
    that has to be looked for on a screen nobody has yet: that one starts the following next(). max_calls counts
    every model call, the planner's and the pointer's."""

    def __init__(self, h: "Harness", task: str, png: bytes, max_calls: int = 30):
        self.h, self.p, self.task, self.max_calls = h, h.planner, task, max_calls
        self.ep = h.begin(task, png)            # for the sizes: the pointer is shown the screen at the model's size
        self.history: list[str] = []            # every action run, with what came of it
        self.frames: list[tuple[int, bytes]] = []   # the last batch's screens: (the action's number in history, png)
        self.pending: list[dict] = []           # planned actions not yet given out
        self.running: list[tuple] = []          # given out and not yet run: (Action, what it is on, its plan entry)
        self.quick: list[bool] = []             # for the actions next() last gave: no settled screen is needed after
        self.expects: list = []                 # and what the planner said will be true after each (Planner(expect=True))
        self.status, self.error = "continue", None
        self.empty = 0                          # planner replies in a row that had no actions
        self.why = ("start", "Nothing has been run yet.")
        self.seen = png                         # the screen the planner's latest call looked at
        self.pointer_calls = 0
        self.planner = {"calls": 0, "by": {}, "s": 0.0, "prompt": 0, "completion": 0, "cached": 0, "cost_usd": 0.0}
        self.answer = ""                        # what the planner said with done (the answer) or fail (why)
        self.ask = h._asks(task)                # a done without an answer gets one more planner call (ask_answer)
        self.pointer = {"s": 0.0, "prompt": 0, "completion": 0, "cached": 0, "cost_usd": 0.0}
        self.trace: list[dict] = []
        self.calls: list[tuple] = []            # (request, record) of every model call made by the latest next()
        # the harness's bash and applescript tools, if it has them: the planner may then name commands to run
        self.shell = tuple(k for k, t in (("shell", "bash"), ("applescript", "applescript")) if t in h.tools)
        self.notes = ""                         # the planner's own notes (Planner(notes=True)), given back every call

    @property
    def ended(self) -> bool:
        return self.status != "continue" or self.error is not None

    def _spent(self) -> bool:
        if self.pointer_calls + self.planner["calls"] >= self.max_calls:
            self.error = f"model call limit ({self.max_calls})"
        return self.error is not None

    def _plan(self, png: bytes) -> None:
        h, p, t = self.h, self.p, time.monotonic()
        key, why = self.why
        self.seen = png
        first = max(0, len(self.history) - 40)
        ran = "\n".join(f"{first + i + 1}. {x}" for i, x in enumerate(self.history[first:])) or "nothing yet"
        kept = f"\n\nYour notes:\n{self.notes}" if self.notes else ""
        content = [{"type": "text", "text": f"Task: {self.task}{kept}\n\nRun so far:\n{ran}\n\n{why}"}]
        shown = pick_frames(self.frames)
        for n, f in shown:
            url = h._image_url(base64.b64encode(small_png(f)).decode())
            content += [{"type": "text", "text": f"The screen after action {n} (smaller):"},
                        {"type": "image_url", "image_url": {"url": url}}]
        if shown:
            content.append({"type": "text", "text": "The screen now:"})
        # the planner sees the screen now at full size and answers in its pixels, whatever the pointer's convention
        content.append({"type": "image_url", "image_url": {"url": h._image_url(base64.b64encode(png).decode())}})
        system = PLANNER_SYSTEM.format(max_actions=p.max_steps, w=self.ep.w, h=self.ep.h)
        if self.shell:
            system += SHELL_NOTE.format(shell=p.shell_name or getattr(h.tools.get("bash"), "runs", "") or "bash", osa=OSA_NOTE.format() if "applescript" in self.shell else "")
        if p.notes:
            system += NOTES_NOTE
        if getattr(p, "expect", False):
            system += EXPECT_NOTE.format()
        msgs = [{"role": "system", "content": system + (PROGRESS_NOTE if h.progress_note else "")},
                {"role": "user", "content": content}]
        rec = p.client.chat(msgs, max_tokens=p.max_tokens, temperature=p.temperature,
                            tools=[act_tool(h.progress_note, self.shell, p.notes, getattr(p, "expect", False))],
                            tool_choice={"type": "function", "function": {"name": "act"}})
        self.calls.append(({"messages": msgs, "max_tokens": p.max_tokens, "temperature": p.temperature}, rec))
        a = _args(rec)
        status = str(a.get("status") or "").lower()
        acts = [x for x in _actions(a) if
                any(k in x for k in ("click", "key", "type", "scroll", "drag", "wait") + self.shell)][: p.max_steps]
        noted = isinstance(a.get("notes"), str) and bool(a["notes"].strip())
        if status not in ("continue", "done", "fail"):
            status = "continue" if acts or noted else "fail"
        elif status == "fail" and noted and not str(a.get("answer") or "").strip():
            status = "continue"                     # a fail with no reason beside notes on what to do next (Qwen): ask again
        empty = status == "continue" and not acts   # a turn spent on notes or thinking, with nothing to run (Qwen does
        #                                              this on its first call): ask again, twice at most, then it fails
        self.empty = self.empty + 1 if empty else 0
        if empty and self.empty > 2:
            notes = a.get("notes").strip()[:300] if isinstance(a.get("notes"), str) else ""
            status, a = "fail", {**a, "answer": a.get("answer") or "the planner gave no actions to run, three calls in a row"
                                 + (f" (its notes: {notes})" if notes else "")}
        if p.notes and isinstance(a.get("notes"), str) and a["notes"].strip():
            self.notes = a["notes"].strip()[:NOTES_MAX]
        progress = " ".join(str(a.get("progress") or "").split())[:400]
        if progress and h.progress_note:
            self.history.append(f"(progress: {progress})")
        u, pl, dt = rec.get("usage") or {}, self.planner, time.monotonic() - t
        pl["calls"] += 1
        pl["by"][key] = pl["by"].get(key, 0) + 1
        pl["s"] += dt
        pl["prompt"] += u.get("prompt") or 0
        pl["completion"] += u.get("completion") or 0
        pl["cached"] += u.get("cached") or 0
        pl["cost_usd"] += u.get("cost") or 0.0
        entry = {"planner": key, "status": status, "s": round(dt, 2), "frames": [n for n, _ in shown],
                 "steps": [[json.dumps(x, ensure_ascii=False)[:150], ""] for x in acts]}
        if p.notes:
            entry["notes"] = pl["notes"] = self.notes          # the notes as they are after this call
        if status != "continue":
            self.answer = entry["answer"] = " ".join(str(a.get("answer") or "").split())[:2000]
        self.trace.append(entry)
        self.frames.clear()
        self.status, self.pending = status, (acts if status == "continue" else [])
        self.why = ("next", "Those actions ran. This is the screen now.")
        if empty and status == "continue":
            self.why = ("empty", "Your last reply had no actions, so nothing ran" + (" (your notes are kept)" if p.notes else "") +
                        ". Give the next actions to run now, or done, or fail with why. This is the screen now.")
        if status == "done" and not self.answer and self.ask and key != "answer" and \
                self.pointer_calls + pl["calls"] < self.max_calls:
            self.ask = False                    # once: the next call asks for the answer, or goes on to get it
            self.status, self.why = "continue", ("answer", ANSWER_ASK)

    def _point(self, target: str, png: bytes) -> list | None:
        """The pointer's coordinate for `target` on this screen (in the harness's coords), or None."""
        from legato.harness import COORDS
        h, ep = self.h, self.ep
        system = POINTER_SYSTEM.format(w=ep.mw, h=ep.mh, coords=COORDS[h.coords])
        if not h.native_tools:
            system = system.replace("Call the `point` tool with", "Give") + POINTER_TEXT.format()
        msgs = [{"role": "system", "content": system},
                {"role": "user", "content": [
                    {"type": "text", "text": f"Find: {target}"},
                    {"type": "image_url", "image_url": {"url": h._image_url(h._png(ep, png))}}]}]
        max_tokens = min(h.max_tokens or 300, 300)
        tools = {"tools": [POINT_TOOL], "tool_choice": "auto"} if h.native_tools else {}
        rec = h._chat(msgs, max_tokens=max_tokens, temperature=h.temperature, **tools)
        self.calls.append(({"messages": msgs, "max_tokens": max_tokens, "temperature": h.temperature}, rec))
        self.pointer_calls += 1
        u = rec.get("usage") or {}
        for k in ("prompt", "completion", "cached"):
            self.pointer[k] += u.get(k) or 0
        self.pointer["cost_usd"] += u.get("cost") or 0.0
        self.pointer["s"] += rec.get("s") or 0.0
        if getattr(h, "on_usage", None):
            h.on_usage(u)
        a = _point_args(rec)
        c = a.get("coordinate")
        ok = a.get("found") is not False and isinstance(c, (list, tuple)) and len(c) >= 2
        c = [float(c[0]), float(c[1])] if ok else None
        self.trace.append({"point": target[:120], "at": c, "s": round(rec.get("s") or 0.0, 2)})
        return c

    def _where(self, at, target: str, png: bytes) -> list | None:
        """The planner's own coordinate if it gave one and the screen there is as it saw it, else the pointer's."""
        h, ep = self.h, self.ep
        if isinstance(at, (list, tuple)) and len(at) >= 2 and all(isinstance(v, (int, float)) for v in at[:2]):
            x, y = float(at[0]), float(at[1])                        # pixels of the full screenshot
            why = "off the screen" if not (0 <= x < ep.w and 0 <= y < ep.h) else moved(self.seen, png, x, y)
            if why is None:
                self.trace.append({"aimed": target[:120], "at": [x, y]})
                k = (1000 / ep.w, 1000 / ep.h) if h.coords == "norm1000" else (ep.mw / ep.w, ep.mh / ep.h)
                return [x * k[0], y * k[1]]                          # in the harness's convention, as the pointer's
            self.trace.append({"aimed": target[:120], "at": None, "why": why})
        if self._spent():
            return None
        return self._point(target, png)

    def _action(self, x: dict, png: bytes) -> tuple:
        """A planned action as a legato Action: (action, what it is on, None), or (None, None, why not)."""
        from legato.actions import parse as parse_action
        h, ep = self.h, self.ep
        for k in self.shell:                    # a command: run by the harness's tool, no screen needed
            if isinstance(x.get(k), str) and x[k].strip():
                return (ToolCall("bash", {"command": x[k]}) if k == "shell" else
                        ToolCall("applescript", {"script": x[k]})), None, None
        if "key" in x or "type" in x or "wait" in x:
            args = ({"action": "key", "keys": str(x["key"])} if "key" in x else
                    {"action": "type", "text": str(x["type"])} if "type" in x else
                    {"action": "wait", "amount": x.get("wait") or 1})
            what = None
        else:
            what = str(x.get("click") or x.get("drag") or x.get("scroll") or "")
            c = self._where(x.get("at"), what, png)
            if c is None:
                return None, None, f'could NOT find "{what}" on the screen; the actions after it were not run'
            if "click" in x:
                args = {"action": BUTTONS.get(str(x.get("button") or "left").lower(), "left_click"), "coordinate": c}
                if x.get("hold"):
                    args["keys"] = str(x["hold"])
            elif "scroll" in x:
                args = {"action": "scroll", "coordinate": c, "direction": str(x.get("direction") or "down"),
                        "amount": x.get("amount") or 3}
            else:
                c2 = self._where(x.get("to_at"), str(x.get("to") or ""), png)
                if c2 is None:
                    return None, None, f'could NOT find "{x.get("to")}" to drop on; the actions after it were not run'
                args = {"action": "left_click_drag", "coordinate": c, "end_coordinate": c2}
        a, why = h._parse(args, ep) if "coordinate" in args else parse_action(args, ep.w, ep.h)
        if a is None:
            return None, None, f"invalid action {json.dumps(x)[:80]}: {why}"
        return a, what, None

    def next(self, png: bytes) -> list:
        """The actions to run now on the screen `png`, in order. [] when the task has ended (status, error)."""
        self.h._frame_size(self.ep, png)
        self.calls, self.quick, self.expects = [], [], []
        while not self.ended and not self.running:
            if not self.pending:
                if self._spent():
                    break
                if self.why[0] == "answer":     # it had said done: it ends done, with or without the answer
                    try:
                        self._plan(png)
                    except PolicyError:
                        self.status = "done"
                    continue
                self._plan(png)
                continue
            while self.pending:
                x = self.pending[0]
                if self.running and any(k in x for k in ("click", "drag", "scroll")):
                    break                       # it has to be found on the screen the earlier ones leave
                a, what, why = self._action(x, png)
                if a is None:
                    if self.error is None:      # not found: the rest of the batch is dropped and the planner looks
                        self.history.append(why)
                        self.why = ("missing", f"The last batch stopped: {why}. This is the screen now.")
                    self.pending = []
                    break
                self.pending.pop(0)
                self.running.append((a, what, x))
        for i, (_a, _what, x) in enumerate(self.running):
            nxt = self.running[i + 1][2] if i + 1 < len(self.running) else None
            self.quick.append(inert(x) and nxt is not None and ("key" in nxt or "type" in nxt))
            self.expects.append(x.get("expect") if getattr(self.p, "expect", False) and isinstance(x.get("expect"), dict) else None)
        return [a for a, _what, _x in self.running]

    def ran(self, png: bytes, result: str | None = None) -> str:
        """The first action next() gave has run and this is the screen after it (result: a command's output).
        Returns its line in the history."""
        a, what, x = self.running.pop(0)
        if isinstance(a, ToolCall):
            k = "shell" if a.name == "bash" else a.name
            said = f"{k}: {clip(str(x.get(k)), 600)}\n   -> {clip(str(result or '').strip(), SHELL_OUT)}"
        else:
            said = f'{a.describe()} on "{what}"' if what else a.describe()
        self.history.append(said)
        self.frames.append((len(self.history), png))
        return said

    def refused(self, said: str) -> None:
        """The first action next() gave was not run (not allowed, not possible): the planner looks again."""
        self.running, self.pending = [], []
        self.history.append(said)
        self.why = ("denied", "The last action was not allowed and was not run. This is the screen now.")


def front_title() -> Callable[[], str] | None:
    """A reader of the title of the window in front on this computer, or None where there is none yet."""
    import sys
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        user = ctypes.windll.user32
        user.GetForegroundWindow.restype = wintypes.HWND

        def read() -> str:
            buf = ctypes.create_unicode_buffer(1024)
            user.GetWindowTextW(wintypes.HWND(user.GetForegroundWindow()), buf, len(buf))
            return buf.value
        return read
    if sys.platform == "darwin":
        try:
            import Quartz as q

            from legato.local import front_windows
        except ImportError:
            return None

        def read() -> str:
            _, windows = front_windows(q)
            return next((str(w.get("kCGWindowName", "")) for w in windows if w.get("kCGWindowLayer") == 0), "")
        return read
    return None


def expectation(exp: dict | None, title: Callable[[], str] | None = None) -> Callable[[], bool] | None:
    """The check for what a planner said will be true after an action ({"title": ...}, {"file": ...}), or None if
    it said nothing this computer can read: the action then waits for the screen as usual."""
    import os
    if not isinstance(exp, dict):
        return None
    checks: list[Callable[[], bool]] = []
    want = exp.get("title")
    if isinstance(want, str) and want.strip():
        title = title or front_title()
        if title is None:
            return None
        checks.append(lambda: want.strip().lower() in title().lower())
    path = exp.get("file")
    if isinstance(path, str) and path.strip():
        checks.append(lambda: os.path.exists(os.path.expandvars(os.path.expanduser(path.strip()))))
    return (lambda: all(c() for c in checks)) if checks else None


def act_expecting(env: "Env", a, check: Callable[[], bool], timing: dict):
    """Send `a` once and go on as soon as `check` is true and the screen has been still for a moment (so the
    screenshot shows it), not when the whole screen has settled. Returns the observation, and counts in `timing`
    how the expectation ended: confirmed, already true before (the ordinary wait), or not met in time."""
    from legato.verify import checked_step
    t = time.monotonic()
    out = checked_step(env, a, check, timeout_s=EXPECT_TIMEOUT_S)
    sent = dict(env.last or {})
    if out.status == "settled":                 # already true: it proved nothing, and the action ran the ordinary way
        timing["expect_already"] = timing.get("expect_already", 0) + 1
        return out.observation
    if out.status == "confirmed":
        timing["expect_met"] = timing.get("expect_met", 0) + 1
        w = getattr(env, "waiter", None)
        if w is not None and isinstance(getattr(w, "rules", None), dict):
            w.rules.setdefault("expect", EXPECT_RULE)
            w.settle("expect", cap_s=EXPECT_QUIET_CAP_S)
    else:
        timing["expect_missed"] = timing.get("expect_missed", 0) + 1
    obs = env.observe()
    took = time.monotonic() - t
    act_s, obs_s = sent.get("act_s", 0.0), (env.last or {}).get("obs_s", 0.0)
    env.last = {"act_s": act_s, "obs_s": obs_s, "settle_s": max(0.0, took - act_s - obs_s)}
    return obs


def run_pointed(h: "Harness", task: str, env: "Env", max_steps: int = 40,
                on_step: Callable | None = None, *, res: "RunResult | None" = None, limits=None) -> "RunResult":
    """Harness.run with a planner whose style is "point" (see the module docstring). max_steps counts every model
    call, the planner's and the pointer's, and so does the result's steps; its usage and timings hold both models'
    tokens, cost and seconds (result.planner has the planner's share, and its final notes). res: the result to fill
    in (Harness.run's); limits: its deadline, cancel event and cost limit, checked before each planner turn.
    on_step(turn, actions, timing) is called after each batch, and after the planner's last call."""
    from legato.harness import RunResult, _book
    res = RunResult(task) if res is None else res
    t0 = time.monotonic()
    ended = False                           # the limits ended it: res.status says how
    obs = env.observe()
    png = obs.screenshot
    s = Pointing(h, task, png, max_calls=max_steps)
    res.planner, res.trace = s.planner, s.trace
    if limits is not None:
        limits.shot(png, "start.png")

    def account() -> dict:
        """Books the model calls the latest next() made, the planner's and the pointer's; returns their timing."""
        for _req, rec in s.calls:
            _book(res.usage, rec)
        planner_s, pointer_s = s.planner["s"] - seen[0], s.pointer["s"] - seen[1]
        seen[:] = s.planner["s"], s.pointer["s"]
        res.steps = s.pointer_calls + s.planner["calls"]
        timing = {"model_s": planner_s + pointer_s, "act_s": 0.0, "settle_s": 0.0, "obs_s": 0.0}
        if planner_s:
            timing["planner_s"] = planner_s
        return timing
    seen = [0.0, 0.0]                       # the planner's and the pointer's seconds already in the timings

    def told(timing: dict, ran: list) -> None:
        res.timings.append(timing)
        if limits is not None:
            limits.step(res, timing, png)
            if timing.get("screenshot"):
                s.trace.append({"ran": [a.describe() for a in ran], "screenshot": timing["screenshot"]})
        if on_step:
            on_step(len(res.timings) - 1, ran, timing)

    try:
        while not res.stopped:
            if limits is not None and limits.hit(res):
                ended = True
                break
            try:
                actions = s.next(png)
            except PolicyError as e:
                res.error = (f"planner: {str(e)[:480]}" if not s.planner["calls"] or not s.running else str(e)[:500])
                if getattr(e, "cut", 0):
                    res.usage["complete"] = False
                timing = account()
                if timing["model_s"]:
                    res.timings.append(timing)
                ended = limits is not None and limits.hit(res)    # a call cut short by the deadline: a timeout
                break
            timing, ran = account(), []
            if not actions:
                if timing["model_s"] or s.status != "continue":   # the planner's last call: done, or fail
                    told(timing, ran)
                break
            try:
                for a, quick, exp in zip(actions, list(s.quick), list(s.expects) or [None] * len(actions)):
                    if isinstance(a, ToolCall):     # a command: its output goes into the list the planner reads
                        t1 = time.monotonic()
                        out = h.call_tool(a)
                        timing["act_s"] += time.monotonic() - t1
                        timing["shell"] = timing.get("shell", 0) + 1
                        obs = env.observe()
                        png = obs.screenshot
                        res.actions.append(a.describe())
                        ran.append(a)
                        s.ran(png, out)
                        continue
                    check = None if quick else expectation(exp)
                    if check is not None:          # the planner said what will be true: go on as soon as it is
                        obs = act_expecting(env, a, check, timing)
                    elif quick:    # it only edits the focused field and a key or text follows: a short fixed pause
                        wait_before, env.fixed_wait_s = env.fixed_wait_s, QUICK_WAIT_S
                        try:
                            obs = env.act(a)
                        finally:
                            env.fixed_wait_s = wait_before
                        timing["quick"] = timing.get("quick", 0) + 1
                    else:
                        obs = env.act(a)
                    png = obs.screenshot
                    for k in ("act_s", "settle_s", "obs_s"):
                        timing[k] += env.last.get(k, 0.0)
                    res.actions.append(a.describe())
                    ran.append(a)
                    s.ran(png)
            except Stopped as e:
                res.stopped, res.error = True, f"stopped by the person: {e}"
            except Denied as e:
                s.refused(f"{'not possible' if isinstance(e, Unavailable) else 'not allowed'}: {e}")
                obs = env.observe()
                png = obs.screenshot
            told(timing, ran)
        res.error = res.error or s.error
        if not res.error:
            res.done, res.failed = s.status == "done", s.status == "fail"
            res.actions.append("done" if res.done else "fail")
            res.answer = s.answer
        if not ended:
            res.status = ("stopped" if res.stopped else "max_steps" if res.error and res.error == s.error else
                          "error" if res.error else "done" if res.done else "failed")
    finally:
        res.wall_s = time.monotonic() - t0
    return res
