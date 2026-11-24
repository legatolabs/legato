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
    assert res.done and res.steps == 4 and res.planner["calls"] == 2        # four actions: 2 pointer + 2 planner calls
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
    assert not res.done and "call limit" in res.error and res.steps == 4 and res.planner["calls"] == 2


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
    assert res.done and res.steps == 2 and res.planner["calls"] == 2         # no pointer call at all
    assert [(a.type, a.x, a.y) for a in log if a.type == "click"] == [("click", 300, 200)]
    assert {"aimed": "the Bold button in the toolbar", "at": [300.0, 200.0]} in res.trace       # screenshot pixels
    assert "The screen now is 1000x500 pixels" in p.client.sent[0]["messages"][0]["content"]


def test_a_coordinate_from_the_planner_is_dropped_when_the_screen_changed_there():
    before, after = real_png(400, 200), real_png(400, 200, ((150, 50, 250, 150),))    # a dialog opens over the middle
    shots, log = [before], []
    env = legato.attach(screenshot=lambda: shots[-1], execute=lambda a: (log.append(a), shots.append(after)))
    h, p = harness([act("continue", {"key": "ctrl+o"}, {"click": "the Name field", "at": [200, 100]},
                        {"click": "the Help menu", "at": [380, 10]}, {"click": "the clock", "at": [900, 10]}),
                    act("done")], [at(520, 600), at(990, 50)])
    res = h.run("open", env)
    clicks = [(a.x, a.y) for a in log if a.type == "click"]
    assert res.done and res.steps - res.planner["calls"] == 2          # the pointer was asked for the first, and for the one off the screen
    assert clicks == [(208, 120), (380, 10), (396, 10)]     # the pointer's, the planner's (untouched), the pointer's
    assert {"aimed": "the Name field", "at": None, "why": "the screen changed there"} in res.trace
    assert {"aimed": "the clock", "at": None, "why": "off the screen"} in res.trace
    assert legato.point.moved(before, after, 380, 10) is None and legato.point.moved(before, after, 200, 100)


def test_the_planner_is_shown_screens_from_during_the_batch():
    from legato.point import pick_frames
    shots, log = [real_png(400, 200)], []

    def execute(a):
        log.append(a)
        shots.append(real_png(400, 200, ((10 * len(log), 10, 10 * len(log) + 8, 18),)))      # every action changes it
    env = legato.attach(screenshot=lambda: shots[-1], execute=execute)
    h, p = harness([act("continue", {"key": "ctrl+o"}, {"key": "Return"}, {"key": "ctrl+w"}), act("done")], [])
    res = h.run("open", env)
    assert res.done
    second = p.client.sent[1]["messages"][1]["content"]
    texts = [c["text"] for c in second if c["type"] == "text"]
    assert texts[1:] == [f"The screen after action {n} (smaller):" for n in (1, 2)] + ["The screen now:"]
    assert sum(c["type"] == "image_url" for c in second) == 3 and len(p.client.sent[0]["messages"][1]["content"]) == 2
    assert res.trace[-1]["frames"] == [1, 2]
    f = [(i, bytes([i])) for i in range(1, 9)]
    assert [n for n, _ in pick_frames(f)] == [2, 4, 6]                       # three, spread, never the last
    assert pick_frames([(1, b"a"), (2, b"a"), (3, b"b")]) == [(2, b"a")]     # a screen the next one repeats is left out


def test_progress_lines_are_kept_in_the_list_the_planner_reads_next():
    first = rec([call("act", {"status": "continue", "actions": [{"key": "ctrl+f"}],
                              "progress": "Column B holds the totals; nothing saved yet."})])
    p = Planner("big", "http://x/v1", client=Scripted([first, act("done")]), style="point")
    h = legato.Harness("small", "http://x/v1", planner=p, progress_note=True)     # Legato's own option, as in plans
    h.client = Scripted([])
    res = h.run("find", desktop([]))
    sent = p.client.sent
    assert res.done and "1. (progress: Column B holds the totals; nothing saved yet.)\n2. key ctrl+f" in \
        sent[1]["messages"][1]["content"][0]["text"]
    assert "progress" in sent[0]["tools"][0]["function"]["parameters"]["required"]
    assert "your progress lines are how you keep your place" in sent[0]["messages"][0]["content"]
    h2, p2 = harness([first, act("done")], [])                                    # without it: no field, no line
    assert h2.run("find", desktop([])).done and "progress" not in p2.client.sent[1]["messages"][1]["content"][0]["text"]
    assert "progress" not in p2.client.sent[0]["tools"][0]["function"]["parameters"]["properties"]


def test_a_step_at_a_time_gives_the_actions_up_to_the_next_thing_to_find():
    from legato.point import Pointing
    h, p = harness([act("continue", {"click": "the Name field", "at": [200, 100]}, {"key": "ctrl+a"}, {"type": "Ada"},
                        {"click": "the OK button in the dialog"}, {"key": "Return"}), act("done")], [at(500, 500)])
    png = desktop([]).observe().screenshot
    s = Pointing(h, "rename", png, max_calls=10)
    first = s.next(png)
    assert [a.type for a in first] == ["click", "key", "type"] and s.quick == [False, True, False]
    assert len(s.calls) == 1 and h.client.sent == []           # one planner call; its own coordinate needed no pointer
    for _ in first:
        s.ran(png)
    second = s.next(png)                                      # the button is looked for on the screen as it is now
    assert [(a.type, a.x, a.y) for a in second[:1]] == [("click", 500, 250)] and second[1].keys == "Return"
    assert len(s.calls) == 1 and len(h.client.sent) == 1
    for _ in second:
        s.ran(png)
    assert s.next(png) == [] and s.status == "done" and s.planner["calls"] == 2 and s.pointer_calls == 1
    assert s.history[3].endswith('on "the OK button in the dialog"') and len(s.history) == 5


def test_a_pointer_without_tool_calls_answers_in_text():
    from legato.point import _point_args
    p = Planner("big", "http://x/v1", client=Scripted([act("continue", {"click": "the Save button"}), act("done")]),
                style="point")
    h = legato.Harness("tiny", "http://x/v1", planner=p, native_tools=False)
    h.client = Scripted([rec(content='{"found": true, "coordinate": [500, 500]}')])
    log = []
    res = h.run("save", desktop(log))
    sent = h.client.sent[0]
    assert res.done and [(a.x, a.y) for a in log] == [(500, 250)]
    assert "tools" not in sent and "Answer with JSON only" in sent["messages"][0]["content"]
    assert _point_args(rec(content="click(start_box='(412,96)')")) == {"found": True, "coordinate": [412.0, 96.0]}
    assert _point_args(rec(content="<point>412 96</point>"))["coordinate"] == [412.0, 96.0]
    assert _point_args(rec(content="It is not on the screen.")) == {"found": False} and _point_args(rec(content="?")) == {}


def test_a_planner_whose_harness_has_a_shell_names_commands_and_reads_their_output():
    ran = []
    sh = legato.tools.Tool("bash", "a shell", {"type": "object", "properties": {"command": {"type": "string"}}},
                           lambda a: (ran.append(a["command"]), "3 files\n[exit 0]")[1])
    p = Planner("big", "http://x/v1", style="point",
                client=Scripted([act("continue", {"shell": "ls | wc -l"}, {"key": "ctrl+s"}), act("done")]))
    h = legato.Harness("small", "http://x/v1", planner=p, tools=[sh])
    h.client = Scripted([])
    log = []
    res = h.run("count", desktop(log))
    assert res.done and ran == ["ls | wc -l"] and res.steps == 2 and res.planner["calls"] == 2
    assert [a.keys for a in log if a.type == "key"] == ["ctrl+s"]
    first, again = p.client.sent[0], p.client.sent[1]["messages"][1]["content"][0]["text"]
    assert '{"shell": "<command>"}' in first["messages"][0]["content"] and "applescript" not in first["messages"][0]["content"]
    assert "shell" in first["tools"][0]["function"]["parameters"]["properties"]["actions"]["items"]["properties"]
    assert "1. shell: ls | wc -l\n   -> 3 files\n[exit 0]" in again and "2. key ctrl+s" in again


def test_a_planner_without_a_shell_is_not_told_of_one():
    h, p = harness([act("done")], [])
    h.run("x", desktop([]))
    assert "shell" not in p.client.sent[0]["messages"][0]["content"].lower().replace("shift", "")
    assert "shell" not in json.dumps(p.client.sent[0]["tools"])


def test_notes_come_back_at_every_call():
    def noted(status, notes, *actions):
        return rec([call("act", {"status": status, "actions": list(actions), "notes": notes})])
    p = Planner("big", "http://x/v1", style="point", notes=True,
                client=Scripted([noted("continue", "rows 1-3 read: A=1 B=2", {"key": "down"}),
                                 act("continue", {"key": "down"}), act("done")]))
    h = legato.Harness("small", "http://x/v1", planner=p)
    h.client = Scripted([])
    res = h.run("compare", desktop([]))
    assert res.done and res.planner["calls"] == 3
    assert "notes" in p.client.sent[0]["tools"][0]["function"]["parameters"]["properties"]
    assert "Your notes" not in p.client.sent[0]["messages"][1]["content"][0]["text"]
    for later in p.client.sent[1:]:                       # a call without new notes keeps the earlier ones
        assert "Your notes:\nrows 1-3 read: A=1 B=2" in later["messages"][1]["content"][0]["text"]


def test_statuses_in_the_pointing_style():
    import threading
    h, _ = harness([act("continue", {"key": "ctrl+s"}), act("done")], [])
    assert h.run("t", desktop([])).status == "done"
    h, _ = harness([act("fail")], [])
    assert h.run("t", desktop([])).status == "failed"
    h, _ = harness([act("continue", {"click": "a"})] * 3, [at(1, 1)] * 3)
    res = h.run("t", desktop([]), max_steps=4)
    assert res.status == "max_steps" and "call limit" in res.error
    stop = threading.Event()
    h, _ = harness([act("continue", {"key": "a"})] * 3, [])
    res = h.run("t", desktop([]), cancel=stop, on_step=lambda *a: stop.set())
    assert (res.status, res.planner["calls"], res.actions) == ("cancelled", 1, ["key a"])

    def broken(a):
        raise RuntimeError("the window could not be captured")
    png = desktop([]).observe().screenshot
    h, _ = harness([act("continue", {"key": "a"})], [])
    res = h.run("t", legato.attach(screenshot=lambda: png, execute=broken), raise_desktop_errors=False)
    assert (res.status, res.error) == ("error", "the desktop failed: the window could not be captured")
    assert res.usage["cost_usd"] == 0.001


def test_on_windows_the_shell_tool_is_powershell_and_the_planner_is_told_so(monkeypatch):
    import subprocess
    import legato.tools as tools
    ran = {}

    class Done:
        returncode = 0

        def communicate(self, timeout=None):
            return b"3 files\r\n", None
    monkeypatch.setattr(tools.sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: (ran.update(cmd=cmd, kw=kw), Done())[1])
    sh = legato.tools.bash()
    assert sh.runs == "PowerShell" and "PowerShell" in sh.description and sh.name == "bash"
    p = Planner("big", "http://x/v1", style="point",
                client=Scripted([act("continue", {"shell": "Get-ChildItem | Measure-Object"}), act("done")]))
    h = legato.Harness("small", "http://x/v1", planner=p, tools=[sh])
    h.client = Scripted([])
    res = h.run("count", desktop([]))
    assert res.done and ran["cmd"] == ["powershell", "-NoProfile", "-NonInteractive", "-Command", "Get-ChildItem | Measure-Object"]
    assert "shell" not in ran["kw"] and "start_new_session" not in ran["kw"]
    assert "runs in PowerShell on this computer" in p.client.sent[0]["messages"][0]["content"]
    assert "-> 3 files\r\n[exit 0]" in p.client.sent[1]["messages"][1]["content"][0]["text"]


def test_elsewhere_the_shell_tool_is_bash():
    assert legato.tools.bash().runs == "bash" and "PowerShell" not in legato.tools.bash().description


def test_planner_calls_tokens_cost_and_seconds_are_in_the_totals_and_on_step_sees_what_ran():
    """The docs' two-model example printed "done in 9.4 s, 0 model turns, 7 actions", usage prompt 0, "model 0.0 s"
    and on_step "STEP 1 []" six times: only the pointer was counted. Every model call counts now."""
    h, p = harness([act("continue", {"key": "ctrl+a"}, {"click": "the Save button"}), act("done")], [at(500, 500)])
    seen = []
    res = h.run("save", desktop([]), on_step=lambda turn, actions, timing: seen.append(
        (turn, [a.describe() for a in actions], timing["usage"]["prompt"])))
    assert res.done and res.steps == 3 and res.planner["calls"] == 2
    assert res.usage["prompt"] == 300 and res.usage["completion"] == 30 and abs(res.usage["cost_usd"] - 0.003) < 1e-9
    assert res.planner["prompt"] == 200 and abs(res.planner["cost_usd"] - 0.002) < 1e-9       # the planner's share
    assert seen == [(0, ["key ctrl+a"], 100), (1, ["click (500, 250)"], 200), (2, [], 300)]
    assert sum(t["model_s"] for t in res.timings) > 0 and "3 model turns" in res.report()
    assert abs(sum(t.get("planner_s", 0) for t in res.timings) - res.planner["s"]) < 1e-6


def test_the_planners_notes_are_in_the_result_and_the_trace():
    h, p = harness([rec([call("act", {"status": "continue", "actions": [{"key": "ctrl+a"}], "notes": "total is 42"})]),
                    rec([call("act", {"status": "done", "notes": "total is 42; saved"})])], [])
    res = h.run("save", desktop([]))
    assert res.planner["notes"] == "total is 42; saved"
    assert [e["notes"] for e in res.trace if "planner" in e] == ["total is 42", "total is 42; saved"]


def test_the_planner_gives_the_answer_and_is_asked_once_more_for_one_it_left_out(tmp_path):
    h, p = harness([rec([call("act", {"status": "done", "answer": "24 November"})])], [])
    res = h.run("tell me the date on the clock", desktop([]))
    assert (res.status, res.answer, res.steps) == ("done", "24 November", 1)
    h, p = harness([act("done"), rec([call("act", {"status": "done", "answer": "24 November"})])], [])
    res = h.run("tell me the date on the clock", desktop([]), record_dir=tmp_path)
    assert (res.status, res.answer, res.planner["by"]) == ("done", "24 November", {"start": 1, "answer": 1})
    assert "You said done without an answer" in p.client.sent[1]["messages"][1]["content"][0]["text"]
    assert (tmp_path / "start.png").exists() and (tmp_path / "turn-001.png").exists()
    h, p = harness([act("done"), act("done")], [])
    res = h.run("what is open?", desktop([]))
    assert (res.status, res.answer, res.planner["calls"]) == ("done", "", 2)        # asked once, never twice
    h, p = harness([act("done")], [])
    res = h.run("type hello", desktop([]))
    assert (res.status, res.planner["calls"]) == ("done", 1)                          # no question: no extra call


def test_max_cost_usd_ends_a_planner_run():
    h, p = harness([act("continue", {"key": "a"}), act("continue", {"key": "b"}), act("continue", {"key": "c"})], [])
    res = h.run("press keys", desktop([]), max_cost_usd=0.002)
    assert (res.status, res.actions, res.planner["calls"]) == ("max_cost", ["key a", "key b"], 2)
