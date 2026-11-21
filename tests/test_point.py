"""legato.point: the planner names actions, the harness's model only points; scripted fake models, a fake desktop."""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import legato  # noqa: E402
from legato.delegate import Planner  # noqa: E402
from legato.rl.fake import tiny_png  # noqa: E402


def rec(calls: list | None = None, content: str = "") -> dict:
    return {"content": content, "tool_calls": calls, "finish_reason": "tool_calls" if calls else "stop",
            "usage": {"prompt": 100, "completion": 10, "cached": 0, "cost": 0.001}, "s": 0.01}


def call(name: str, args: dict) -> dict:
    return {"type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def act(status: str, *actions: dict) -> dict:
    return rec([call("act", {"status": status, "actions": list(actions)})])


def at(x: float, y: float) -> dict:
    return rec([call("point", {"found": True, "coordinate": [x, y]})])


class Scripted:
    def __init__(self, script: list):
        self.script, self.sent = list(script), []

    def chat(self, messages, **kw) -> dict:
        self.sent.append({"messages": messages, **kw})
        assert self.script, "the model was called more often than the test expects"
        return self.script.pop(0)


def desktop(log: list):
    png = tiny_png(1, w=8, h=8)
    png = png[:16] + struct.pack(">II", 1000, 500) + png[24:]
    return legato.attach(screenshot=lambda: png, execute=lambda a: log.append(a))


def harness(planner_script: list, pointer_script: list, **kw):
    p = Planner("big", "http://x/v1", client=Scripted(planner_script), style="point", **kw)
    h = legato.Harness("small", "http://x/v1", planner=p)
    h.client = Scripted(pointer_script)
    return h, p


def test_keys_cost_no_call_and_clicks_one_pointer_call_each():
    h, p = harness([act("continue", {"click": "the title of slide 4", "button": "double"}, {"key": "ctrl+a"},
                        {"type": "Hello"}, {"click": "the Save button in the toolbar"}), act("done")],
                   [at(500, 500), at(100, 200)])
    log = []
    res = h.run("retitle", desktop(log))
    assert res.done and res.steps == 2 and res.planner["calls"] == 2        # four actions: 2 pointer + 2 planner calls
    assert [(a.type, a.x, a.y) for a in log if a.type.endswith("click")] == [("double_click", 500, 250), ("click", 100, 100)]
    assert [(a.type, a.keys or a.text) for a in log if a.type in ("key", "type")] == [("key", "ctrl+a"), ("type", "Hello")]
    asked = h.client.sent[0]["messages"]
    assert asked[1]["content"][0]["text"] == "Find: the title of slide 4" and len(asked) == 2
    again = p.client.sent[1]["messages"][1]["content"][0]["text"]
    assert '1. double click (500, 250) on "the title of slide 4"' in again and "4. click (100, 100)" in again
    assert res.planner["by"] == {"start": 1, "next": 1} and res.actions[-1] == "done"


def test_a_thing_that_is_not_there_stops_the_batch_and_the_planner_sees_the_screen():
    h, p = harness([act("continue", {"click": "the Export item in the File menu"}, {"key": "Return"}),
                    act("continue", {"key": "ctrl+shift+e"}), act("done")],
                   [rec([call("point", {"found": False})])])
    log = []
    res = h.run("export", desktop(log))
    assert res.done and [a.keys for a in log] == ["ctrl+shift+e"]            # Return was not sent after the miss
    assert res.planner["by"] == {"start": 1, "missing": 1, "next": 1}
    assert 'could NOT find "the Export item in the File menu"' in p.client.sent[1]["messages"][1]["content"][0]["text"]


def test_drag_scroll_hold_and_the_call_limit():
    h, p = harness([act("continue", {"drag": "the video icon on the desktop", "to": "the VLC window"},
                        {"scroll": "the file list", "direction": "down", "amount": 5},
                        {"click": "slide 24", "hold": "shift"}), act("fail")],
                   [at(900, 900), at(500, 500), at(300, 300), rec(content='{"found": true, "coordinate": [80, 800]}')])
    log = []
    res = h.run("t", desktop(log))
    assert res.failed and [a.type for a in log] == ["drag", "scroll", "click"]
    assert (log[0].x, log[0].y, log[0].x2, log[0].y2) == (900, 450, 500, 250) and log[1].amount == 5
    assert log[2].keys == "shift" and (log[2].x, log[2].y) == (80, 400)      # the pointer may answer in text
    h, p = harness([act("continue", {"click": "a"})] * 3, [at(1, 1)] * 3)
    res = h.run("t", desktop([]), max_steps=4)
    assert not res.done and "call limit" in res.error and res.steps + res.planner["calls"] == 4


def test_text_and_caret_keys_before_more_keys_do_not_wait_for_the_screen():
    from legato import point
    assert point.inert({"type": "Total"}) and point.inert({"key": "Ctrl+A"}) and not point.inert({"type": "x\n"})
    assert not point.inert({"key": "ctrl+s"}) and not point.inert({"key": "Return"}) and not point.inert({"click": "x"})
    h, p = harness([act("continue", {"click": "the title"}, {"key": "ctrl+a"}, {"type": "Hello"}, {"key": "Return"},
                        {"type": "World"}), act("done")], [at(500, 500)])
    log, env, waits = [], None, []
    env = desktop(log)
    real = env.act

    def spy(a):
        waits.append((a.type, env.fixed_wait_s))
        return real(a)
    env.act = spy
    res = h.run("t", env)
    assert res.done and len(log) == 5 and env.fixed_wait_s is None
    # ctrl+a and "Hello" are inert with a key or text after them; Return may open something; the last one is the last
    assert waits == [("click", None), ("key", 0.1), ("type", 0.1), ("key", None), ("type", None)]
    assert sum(t.get("quick", 0) for t in res.timings) == 2


def real_png(w: int, h: int, boxes: tuple = ()) -> bytes:
    import io

    from PIL import Image, ImageDraw
    im = Image.new("RGB", (w, h), (240, 240, 240))
    d = ImageDraw.Draw(im)
    for box in boxes:
        d.rectangle(box, fill=(20, 20, 20))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def test_a_coordinate_from_the_planner_is_used_without_asking_the_pointer():
    h, p = harness([act("continue", {"click": "the Bold button in the toolbar", "at": [300, 200]}, {"key": "ctrl+s"}),
                    act("done")], [])
    log = []
    res = h.run("bold", desktop(log))
    assert res.done and res.steps == 0 and res.planner["calls"] == 2         # no pointer call at all
    assert [(a.type, a.x, a.y) for a in log if a.type == "click"] == [("click", 300, 200)]
    assert {"aimed": "the Bold button in the toolbar", "at": [300.0, 200.0]} in res.trace       # screenshot pixels
    assert "The screenshot is 1000x500 pixels" in p.client.sent[0]["messages"][0]["content"]


def test_a_coordinate_from_the_planner_is_dropped_when_the_screen_changed_there():
    before, after = real_png(400, 200), real_png(400, 200, ((150, 50, 250, 150),))    # a dialog opens over the middle
    shots, log = [before], []
    env = legato.attach(screenshot=lambda: shots[-1], execute=lambda a: (log.append(a), shots.append(after)))
    h, p = harness([act("continue", {"key": "ctrl+o"}, {"click": "the Name field", "at": [200, 100]},
                        {"click": "the Help menu", "at": [380, 10]}, {"click": "the clock", "at": [900, 10]}),
                    act("done")], [at(520, 600), at(990, 50)])
    res = h.run("open", env)
    clicks = [(a.x, a.y) for a in log if a.type == "click"]
    assert res.done and res.steps == 2          # the pointer was asked for the first, and for the one off the screen
    assert clicks == [(208, 120), (380, 10), (396, 10)]     # the pointer's, the planner's (untouched), the pointer's
    assert {"aimed": "the Name field", "at": None, "why": "the screen changed there"} in res.trace
    assert {"aimed": "the clock", "at": None, "why": "off the screen"} in res.trace
    assert legato.point.moved(before, after, 380, 10) is None and legato.point.moved(before, after, 200, 100)
