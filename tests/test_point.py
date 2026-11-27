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


def test_a_planner_turn_with_notes_and_no_actions_is_asked_again_not_taken_as_fail():
    """Qwen3.8-27B spends its first call writing notes: status continue, no actions."""
    def noted(text: str) -> dict:
        return rec([call("act", {"status": "continue", "notes": text})])
    log: list = []
    h, p = harness([noted("three invoices: a, b, c"), act("continue", {"key": "ctrl+s"}), act("done")], [])
    res = h.run("t", desktop(log))
    assert (res.status, res.planner["calls"], res.actions) == ("done", 3, ["key ctrl+s", "done"])
    again = p.client.sent[1]["messages"][1]["content"][0]["text"]
    assert "had no actions" in again and "three invoices: a, b, c" in again      # told why, and its notes are kept
    h, _ = harness([noted("n")] * 3, [])                                         # three in a row: it ends, and says why
    res = h.run("t", desktop([]))
    assert (res.status, res.planner["calls"]) == ("failed", 3) and "no actions" in res.error


def test_a_fail_with_no_reason_or_no_status_is_asked_again():
    """Seen from Qwen3.8-27B as the planner on Windows: "fail" with nothing but notes naming the next step, or no status."""
    for first in ({"status": "fail", "notes": "clear the file name, then type greek.txt"}, {"notes": "open notepad next"}):
        h, p = harness([rec([call("act", first)]), act("continue", {"key": "ctrl+s"}), act("done")], [])
        res = h.run("t", desktop([]))
        assert (res.status, res.planner["calls"], res.actions) == ("done", 3, ["key ctrl+s", "done"]), first
    h, _ = harness([rec([call("act", {"status": "fail", "answer": "the app is not installed"})])], [])
    res = h.run("t", desktop([]))                                               # a fail that says why still ends the run
    assert (res.status, res.error) == ("failed", "the app is not installed")
    h, _ = harness([rec([call("act", {"status": "fail", "notes": "stuck on the save box"})])] * 3, [])
    res = h.run("t", desktop([]))
    assert res.status == "failed" and "stuck on the save box" in res.error      # three in a row: its notes say where


def test_loosely_written_actions_are_still_read():
    """Seen from Qwen3.8-27B through OpenRouter: "actions.1" holding one object, or "actions" as an object."""
    for args in ({"status": "continue", "actions.1": {"key": "ctrl+s"}}, {"status": "continue", "actions": {"key": "ctrl+s"}},
                 {"status": "continue", "action": [{"key": "ctrl+s"}]}):
        h, _ = harness([rec([call("act", args)]), act("done")], [])
        res = h.run("t", desktop([]))
        assert (res.status, res.actions) == ("done", ["key ctrl+s", "done"]), args
    from legato.point import ACT_TOOL, act_tool
    par = ACT_TOOL["function"]["parameters"]
    assert list(par["properties"])[:2] == ["status", "actions"] and par["required"] == ["status", "actions"]
    assert act_tool(progress=True)["function"]["parameters"]["required"] == ["status", "actions", "progress"]


def test_a_batchs_timing_says_which_action_each_wait_followed():
    h, p = harness([act("continue", {"click": "the address bar", "at": [100, 20]}, {"key": "ctrl+a"}, {"type": "example.com"},
                        {"key": "Return"}), act("done")], [])
    res = h.run("open the page", desktop([]))
    per = res.timings[0]["actions"]
    assert [x["a"].split()[0] for x in per] == ["click", "key", "type", "key"]
    assert [bool(x.get("quick")) for x in per] == [False, True, True, False]      # text and caret keys before more keys
    assert all(set(x) >= {"act_s", "settle_s", "obs_s"} for x in per)
    for k in ("act_s", "settle_s", "obs_s"):                                       # the turn's sums are the actions'
        assert abs(sum(x[k] for x in per) - res.timings[0][k]) < 1e-9
    assert "act_s" in res.report() or res.report()                                 # the report still adds up


def _watched_desktop(log, screen):
    """A desktop whose wait can be watched: a frame id and its PNG, both from `screen["v"]`."""
    pngs = {"a": real_png(40, 30), "b": real_png(40, 30, boxes=((5, 5, 20, 20),))}
    env = legato.attach(screenshot=lambda: pngs[screen["v"]], execute=lambda a: (log.append(a), screen.update(t=__import__("time").monotonic())))
    env._digest = lambda: screen["v"]
    env._png = lambda: pngs[screen["v"]]
    return env, pngs


def test_the_planners_next_call_starts_during_the_wait_and_is_kept_when_the_screen_holds():
    """Planner(speculate=True): after a batch's last action the next planner call starts on the first still frame;
    the screen settles on it, so that reply is the next call's and no second request is made."""
    h, p = harness([act("continue", {"key": "ctrl+s"}), act("done")], [], speculate=True)
    log, screen = [], {"v": "a"}
    env, _ = _watched_desktop(log, screen)
    res = h.run("save", env)
    assert res.done and [a.keys for a in log] == ["ctrl+s"]
    assert len(p.client.sent) == 2                                    # two planner requests in all, none repeated
    ahead = res.planner["ahead"]
    assert (ahead["started"], ahead["used"], ahead["dropped"]) == (1, 1, 0) and ahead["saved_s"] > 0
    per = res.timings[0]["actions"][0]
    assert (per["need"], per["how"]) == ("planner ahead", "held")


def test_a_guess_made_on_a_frame_the_screen_then_leaves_is_dropped_and_the_call_made_again():
    import time as _time
    h, p = harness([act("continue", {"key": "Return"}), act("fail"), act("done")], [], speculate=True)
    log, screen = [], {"v": "a"}
    env, pngs = _watched_desktop(log, screen)
    digest = env._digest

    def moving():                                   # the page arrives 0.3 s after the key: the first still frame is stale
        if screen.get("t") and _time.monotonic() - screen["t"] > 0.3:
            screen["v"] = "b"
        return digest()
    env._digest = moving
    res = h.run("open the page", env)
    assert res.done and len(log) == 1               # the dropped guess said fail; the real call, on the new screen, done
    ahead = res.planner["ahead"]
    assert (ahead["started"], ahead["used"], ahead["dropped"]) == (1, 0, 1)
    assert len(p.client.sent) == 3 and res.timings[0]["actions"][0]["how"] == "changed"
    last = lambda i: [c["image_url"]["url"] for c in p.client.sent[i]["messages"][1]["content"] if c.get("type") == "image_url"][-1]
    assert last(1) != last(2)                       # the guess saw the old screen; the call that counts, the settled one


def test_without_speculate_nothing_is_started_early():
    h, p = harness([act("continue", {"key": "ctrl+s"}), act("done")], [])
    log, screen = [], {"v": "a"}
    env, _ = _watched_desktop(log, screen)
    res = h.run("save", env)
    assert res.done and res.planner["ahead"]["started"] == 0 and len(p.client.sent) == 2


def fin(status: str, finish: dict, *actions: dict, answer: str = "") -> dict:
    return rec([call("act", {"status": status, "actions": list(actions), "finish": finish, "answer": answer})])


def test_a_list_that_says_how_to_check_it_finished_ends_the_task_without_another_call(tmp_path):
    """Planner(finish=True): the planner's last list carries a check; it holds after the last action, so the task is
    done with one planner call, and the answer is the one given with the list."""
    out = tmp_path / "hn.txt"
    h, p = harness([fin("continue", {"file": str(out), "contains": "points"}, {"key": "ctrl+s"}, answer="saved")], [],
                   finish=True)
    log = []
    png = real_png(40, 30)
    env = legato.attach(screenshot=lambda: png, execute=lambda a: (log.append(a), out.write_text("Story: 12 points")))
    res = h.run("save the list", env)
    assert res.done and res.answer == "saved" and len(log) == 1 and len(p.client.sent) == 1
    assert res.timings[0]["finish"] == "held" and res.trace[-1]["planner"] == "finish"
    assert '"finish"' in p.client.sent[0]["messages"][0]["content"]
    assert "finish" in p.client.sent[0]["tools"][0]["function"]["parameters"]["properties"]


def test_a_finish_check_that_does_not_hold_shows_the_planner_the_screen_as_usual(tmp_path, monkeypatch):
    import legato.point as lp
    monkeypatch.setattr(lp, "FINISH_WAIT_S", 0.05)
    out = tmp_path / "never.txt"
    h, p = harness([fin("continue", {"file": str(out)}, {"key": "ctrl+s"}), act("done")], [], finish=True)
    log = []
    res = h.run("save", desktop(log))
    assert res.done and len(log) == 1 and len(p.client.sent) == 2 and res.timings[0]["finish"] == "missed"
    assert "finish check did not hold" in p.client.sent[1]["messages"][1]["content"][0]["text"]


def test_a_list_cut_short_finishes_nothing_and_the_option_is_off_by_default(tmp_path):
    out = tmp_path / "there.txt"
    out.write_text("x")
    h, p = harness([fin("continue", {"file": str(out)}, {"click": "a button that is not there"}, {"key": "ctrl+s"}),
                    act("done")], [rec([call("point", {"found": False})])], finish=True)
    log = []
    res = h.run("save", desktop(log))
    assert res.done and log == [] and len(p.client.sent) == 2          # the check would have held; it was not made
    h, p = harness([fin("continue", {"file": str(out)}, {"key": "ctrl+s"}), act("done")], [])
    res = h.run("save", desktop([]))
    assert len(p.client.sent) == 2 and '"finish"' not in p.client.sent[0]["messages"][0]["content"]
