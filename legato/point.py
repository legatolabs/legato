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
- status "done" when the screen shows the task is complete and saved.
After a list has run you may be shown screens from during it, each labelled with the action it followed, before \
the screen as it is now. Check on them that every action did what you meant (the right thing was selected, the \
right dialog opened) before you go on or say done; redo what went wrong.
- status "fail" when the task cannot be done on this computer.
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
        "actions": {"type": "array", "items": {"type": "object", "properties": {
            "click": {"type": "string"}, "button": {"type": "string", "enum": ["left", "double", "right", "triple"]},
            "hold": {"type": "string"}, "key": {"type": "string"}, "type": {"type": "string"},
            "scroll": {"type": "string"}, "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
            "amount": {"type": "number"}, "drag": {"type": "string"}, "to": {"type": "string"},
            "at": {"type": "array", "items": {"type": "number"}},
            "to_at": {"type": "array", "items": {"type": "number"}},
            "wait": {"type": "number"}}}}},
        "required": ["status"]}}}

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
SHELL_OUT = 1500                   # characters of a command's output kept in the list the planner reads


PROGRESS = {"type": "string", "description": "one line: what is done so far and what is left"}
PROGRESS_NOTE = ("\nEvery call also carries `progress`: one line, what is done so far, what you have checked and "
                 "what is left. Your next call sees only the task, the list of actions run with these lines, and the "
                 "screen, so your progress lines are how you keep your place: do not check again what a line says is "
                 "checked.")


def act_tool(progress: bool = False, shell: tuple = (), notes: bool = False) -> dict:
    """The planner's `act` tool (progress: a required one-line `progress`, as Harness(progress_note=True) asks of
    a plan; shell: the names among "shell" and "applescript" its actions may carry; notes: a `notes` text it keeps)."""
    if not progress and not shell and not notes:
        return ACT_TOOL
    f = dict(ACT_TOOL["function"])
    f["parameters"] = par = dict(f["parameters"])
    if shell:
        items = par["properties"]["actions"]["items"]
        items = {**items, "properties": {**items["properties"], **{k: SHELL_PROPS[k] for k in shell}}}
        par["properties"] = {**par["properties"], "actions": {**par["properties"]["actions"], "items": items}}
    if notes:
        par["properties"] = {**par["properties"], "notes": NOTES}
    if progress:
        par["properties"] = {**par["properties"], "progress": PROGRESS}
        par["required"] = ["status", "progress"]
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
        self.status, self.error = "continue", None
        self.why = ("start", "Nothing has been run yet.")
        self.seen = png                         # the screen the planner's latest call looked at
        self.pointer_calls = 0
        self.planner = {"calls": 0, "by": {}, "s": 0.0, "prompt": 0, "completion": 0, "cost_usd": 0.0}
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
            system += SHELL_NOTE.format(shell=p.shell_name, osa=OSA_NOTE.format() if "applescript" in self.shell else "")
        if p.notes:
            system += NOTES_NOTE
        msgs = [{"role": "system", "content": system + (PROGRESS_NOTE if h.progress_note else "")},
                {"role": "user", "content": content}]
        rec = p.client.chat(msgs, max_tokens=p.max_tokens, temperature=p.temperature,
                            tools=[act_tool(h.progress_note, self.shell, p.notes)],
                            tool_choice={"type": "function", "function": {"name": "act"}})
        self.calls.append(({"messages": msgs, "max_tokens": p.max_tokens, "temperature": p.temperature}, rec))
        a = _args(rec)
        status = str(a.get("status") or "").lower()
        acts = [x for x in a.get("actions") or [] if isinstance(x, dict) and
                any(k in x for k in ("click", "key", "type", "scroll", "drag", "wait") + self.shell)][: p.max_steps]
        if status not in ("continue", "done", "fail"):
            status = "continue" if acts else "fail"
        if status == "continue" and not acts:
            status = "fail"
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
        pl["cost_usd"] += u.get("cost") or 0.0
        self.trace.append({"planner": key, "status": status, "s": round(dt, 2), "frames": [n for n, _ in shown],
                           "steps": [[json.dumps(x, ensure_ascii=False)[:150], ""] for x in acts]})
        self.frames.clear()
        self.status, self.pending = status, (acts if status == "continue" else [])
        self.why = ("next", "Those actions ran. This is the screen now.")

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
        self.calls, self.quick = [], []
        while not self.ended and not self.running:
            if not self.pending:
                if self._spent():
                    break
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


def run_pointed(h: "Harness", task: str, env: "Env", max_steps: int = 30,
                on_step: Callable | None = None) -> "RunResult":
    """Harness.run with a planner whose style is "point" (see the module docstring). max_steps counts every model
    call, the planner's and the pointer's."""
    from legato.harness import RunResult
    res, t0 = RunResult(task), time.monotonic()
    obs = env.observe()
    png = obs.screenshot
    s = Pointing(h, task, png, max_calls=max_steps)
    res.planner, res.trace = s.planner, s.trace

    def account() -> float:
        """Books the model calls the latest next() made; returns the pointer's seconds among them."""
        model_s = 0.0
        for _req, rec in s.calls:
            res.usage["cost_usd"] += (rec.get("usage") or {}).get("cost") or 0.0
        n = s.planner["calls"] - len([t for t in res.timings if "planner" in t])
        for _ in range(n):
            res.timings.append({"planner_s": s.planner["s"] / max(1, s.planner["calls"]), "planner": "call"})
        for k in ("prompt", "completion", "cached"):
            res.usage[k] = s.pointer[k]
        model_s, res.steps = s.pointer["s"] - account.seen, s.pointer_calls
        account.seen = s.pointer["s"]
        return model_s
    account.seen = 0.0

    try:
        while not res.stopped:
            try:
                actions = s.next(png)
            except PolicyError as e:
                res.error = (f"planner: {str(e)[:280]}" if not s.planner["calls"] or not s.running else str(e)[:300])
                account()
                break
            timing = {"model_s": account(), "act_s": 0.0, "settle_s": 0.0, "obs_s": 0.0}
            if not actions:
                break
            try:
                for a, quick in zip(actions, list(s.quick)):
                    if isinstance(a, ToolCall):     # a command: its output goes into the list the planner reads
                        t1 = time.monotonic()
                        out = h.call_tool(a)
                        timing["act_s"] += time.monotonic() - t1
                        timing["shell"] = timing.get("shell", 0) + 1
                        obs = env.observe()
                        png = obs.screenshot
                        res.actions.append(a.describe())
                        s.ran(png, out)
                        continue
                    if quick:      # it only edits the focused field and a key or text follows: a short fixed pause
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
                    s.ran(png)
            except Stopped as e:
                res.stopped, res.error = True, f"stopped by the person: {e}"
            except Denied as e:
                s.refused(f"{'not possible' if isinstance(e, Unavailable) else 'not allowed'}: {e}")
                obs = env.observe()
                png = obs.screenshot
            res.timings.append(timing)
            if on_step:
                on_step(s.planner["calls"] - 1, [], timing)
        res.error = res.error or s.error
        if not res.error:
            res.done, res.failed = s.status == "done", s.status == "fail"
            res.actions.append("done" if res.done else "fail")
    finally:
        res.wall_s = time.monotonic() - t0
    return res
