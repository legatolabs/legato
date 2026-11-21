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
called again, and only the planner ends the task.

Model calls a task = planner calls + pointer calls. Nothing is spent on reporting a step or checking it.
"""
from __future__ import annotations

import base64
import json
import time
from typing import TYPE_CHECKING, Callable

from legato.rl.policy import PolicyError
from legato.safety import Denied, Stopped, Unavailable

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
screen as it is then. The screenshot is {w}x{h} pixels; "at" is [x, y] in its pixels, from the top-left corner.
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
            "wait": {"type": "number"}}}},
        "note": {"type": "string", "description": "one or two lines for your own next call, which sees only the "
                 "task, the list of actions run (with these notes) and the screen: what you have found out, what is "
                 "finished, what is left, what did not work"}},
        "required": ["status"]}}}

POINTER_SYSTEM = """You find things on a screenshot of a computer desktop ({w}x{h} pixels). You are given a \
description of one thing. Call the `point` tool with the coordinate of its centre, or with found false if it is not \
on the screen. {coords}"""

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


def run_pointed(h: "Harness", task: str, env: "Env", max_steps: int = 30,
                on_step: Callable | None = None) -> "RunResult":
    """Harness.run with a planner whose style is "point" (see the module docstring). max_steps counts every model
    call, the planner's and the pointer's."""
    from legato.actions import parse as parse_action
    from legato.harness import COORDS, RunResult
    p = h.planner
    res, t0 = RunResult(task), time.monotonic()
    res.planner = {"calls": 0, "by": {}, "s": 0.0, "prompt": 0, "completion": 0, "cost_usd": 0.0}
    res.trace = []
    obs = env.observe()
    png = obs.screenshot
    ep = h.begin(task, png)                 # for the sizes: the pointer is shown the screen at the model's size
    history: list[str] = []                 # every action run, with what came of it
    calls = 0
    seen = png                              # the screen the planner's latest call looked at

    def ask_planner(key: str, why: str) -> tuple[str, list]:
        nonlocal seen
        t = time.monotonic()
        seen = png
        ran = "\n".join(f"{i + 1}. {x}" for i, x in enumerate(history[-40:])) or "nothing yet"
        # the planner sees the screenshot at full size and answers in its pixels, whatever the pointer's convention
        msgs = [{"role": "system", "content": PLANNER_SYSTEM.format(max_actions=p.max_steps, w=ep.w, h=ep.h)},
                {"role": "user", "content": [
                    {"type": "text", "text": f"Task: {task}\n\nRun so far:\n{ran}\n\n{why}"},
                    {"type": "image_url", "image_url": {"url": h._image_url(base64.b64encode(png).decode())}}]}]
        rec = p.client.chat(msgs, max_tokens=p.max_tokens, temperature=p.temperature, tools=[ACT_TOOL],
                            tool_choice={"type": "function", "function": {"name": "act"}})
        a = _args(rec)
        status = str(a.get("status") or "").lower()
        acts = [x for x in a.get("actions") or [] if isinstance(x, dict) and
                any(k in x for k in ("click", "key", "type", "scroll", "drag", "wait"))][: p.max_steps]
        if status not in ("continue", "done", "fail"):
            status = "continue" if acts else "fail"
        if status == "continue" and not acts:
            status = "fail"
        note = " ".join(str(a.get("note") or "").split())[:400]
        if note:
            history.append(f"(your note: {note})")
        u = rec.get("usage") or {}
        pl = res.planner
        pl["calls"] += 1
        pl["by"][key] = pl["by"].get(key, 0) + 1
        pl["s"] += time.monotonic() - t
        pl["prompt"] += u.get("prompt") or 0
        pl["completion"] += u.get("completion") or 0
        pl["cost_usd"] += u.get("cost") or 0.0
        res.usage["cost_usd"] += u.get("cost") or 0.0
        res.timings.append({"planner_s": time.monotonic() - t, "planner": key})
        res.trace.append({"planner": key, "status": status, "s": round(time.monotonic() - t, 2),
                          "steps": [[json.dumps(x, ensure_ascii=False)[:150], ""] for x in acts]})
        return status, acts

    def point(target: str) -> tuple[list | None, float]:
        """The pointer's coordinate for `target` on the current screen (in the harness's coords), or None."""
        nonlocal calls
        msgs = [{"role": "system", "content": POINTER_SYSTEM.format(w=ep.mw, h=ep.mh, coords=COORDS[h.coords])},
                {"role": "user", "content": [
                    {"type": "text", "text": f"Find: {target}"},
                    {"type": "image_url", "image_url": {"url": h._image_url(h._png(ep, png))}}]}]
        rec = h._chat(msgs, max_tokens=min(h.max_tokens or 300, 300), temperature=h.temperature, tools=[POINT_TOOL],
                      tool_choice="auto")
        calls += 1
        res.steps += 1
        u = rec.get("usage") or {}
        for k in ("prompt", "completion", "cached"):
            res.usage[k] += u.get(k) or 0
        res.usage["cost_usd"] += u.get("cost") or 0.0
        if getattr(h, "on_usage", None):
            h.on_usage(u)
        a = _args(rec)
        c = a.get("coordinate")
        ok = a.get("found") is not False and isinstance(c, (list, tuple)) and len(c) >= 2
        return ([float(c[0]), float(c[1])] if ok else None), rec["s"]

    def run_one(x: dict, timing: dict, quick: bool = False) -> tuple[str, bool]:
        def where(at, target: str) -> list | None:
            """The planner's own coordinate if it gave one and the screen there is as it saw it, else the pointer's."""
            if isinstance(at, (list, tuple)) and len(at) >= 2 and all(isinstance(v, (int, float)) for v in at[:2]):
                x, y = float(at[0]), float(at[1])                    # pixels of the full screenshot
                why = "off the screen" if not (0 <= x < ep.w and 0 <= y < ep.h) else moved(seen, png, x, y)
                if why is None:
                    res.trace.append({"aimed": target[:120], "at": [x, y]})
                    k = (1000 / ep.w, 1000 / ep.h) if h.coords == "norm1000" else (ep.mw / ep.w, ep.mh / ep.h)
                    return [x * k[0], y * k[1]]                      # in the harness's convention, as the pointer's
                res.trace.append({"aimed": target[:120], "at": None, "why": why})
            c, s = point(target)
            timing["model_s"] += s
            res.trace.append({"point": target[:120], "at": c, "s": round(s, 2)})
            return c

        """Run one planner action. Returns (what happened, whether the batch goes on). quick: the action only edits
        the focused field and a key or text follows it, so the screen is not waited for (a short fixed pause)."""
        nonlocal png, obs
        if "key" in x or "type" in x or "wait" in x:
            args = ({"action": "key", "keys": str(x["key"])} if "key" in x else
                    {"action": "type", "text": str(x["type"])} if "type" in x else
                    {"action": "wait", "amount": x.get("wait") or 1})
            what = None
        else:
            target = str(x.get("click") or x.get("drag") or x.get("scroll") or "")
            c = where(x.get("at"), target)
            if c is None:
                return f'could NOT find "{target}" on the screen; the actions after it were not run', False
            what = target
            if "click" in x:
                args = {"action": BUTTONS.get(str(x.get("button") or "left").lower(), "left_click"), "coordinate": c}
                if x.get("hold"):
                    args["keys"] = str(x["hold"])
            elif "scroll" in x:
                args = {"action": "scroll", "coordinate": c, "direction": str(x.get("direction") or "down"),
                        "amount": x.get("amount") or 3}
            else:
                c2 = where(x.get("to_at"), str(x.get("to") or ""))
                if c2 is None:
                    return f'could NOT find "{x.get("to")}" to drop on; the actions after it were not run', False
                args = {"action": "left_click_drag", "coordinate": c, "end_coordinate": c2}
        a, why = h._parse(args, ep) if "coordinate" in args else parse_action(args, ep.w, ep.h)
        if a is None:
            return f"invalid action {json.dumps(x)[:80]}: {why}", False
        if quick:
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
        return (f'{a.describe()} on "{what}"' if what else a.describe()), True

    try:
        try:
            status, acts = ask_planner("start", "Nothing has been run yet.")
        except PolicyError as e:
            res.error = f"planner: {str(e)[:280]}"
            return res
        while status == "continue":
            if calls + res.planner["calls"] >= max_steps:
                res.error = f"model call limit ({max_steps})"
                break
            timing = {"model_s": 0.0, "act_s": 0.0, "settle_s": 0.0, "obs_s": 0.0}
            key, why = "next", "Those actions ran. This is the screen now."
            try:
                for i, x in enumerate(acts):
                    nxt = acts[i + 1] if i + 1 < len(acts) else None
                    quick = inert(x) and nxt is not None and ("key" in nxt or "type" in nxt)
                    said, go_on = run_one(x, timing, quick)
                    history.append(said)
                    if not go_on:
                        key, why = "missing", f"The last batch stopped: {said}. This is the screen now."
                        break
            except Stopped as e:
                res.stopped, res.error = True, f"stopped by the person: {e}"
            except Denied as e:
                history.append(f"{'not possible' if isinstance(e, Unavailable) else 'not allowed'}: {e}")
                key, why = "denied", "The last action was not allowed and was not run. This is the screen now."
                obs = env.observe()
                png = obs.screenshot
            except PolicyError as e:
                res.error = str(e)[:300]
                break
            res.timings.append(timing)
            if on_step:
                on_step(res.planner["calls"] - 1, [], timing)
            if res.stopped:
                break
            if calls + res.planner["calls"] >= max_steps:
                res.error = f"model call limit ({max_steps})"
                break
            try:
                status, acts = ask_planner(key, why)
            except PolicyError as e:
                res.error = f"planner: {str(e)[:280]}"
                break
        if not res.error:
            res.done, res.failed = status == "done", status == "fail"
            res.actions.append("done" if res.done else "fail")
    finally:
        res.wall_s = time.monotonic() - t0
    return res
