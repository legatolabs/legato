"""Harness(plan=True): one call carries steps that run in order until one cannot go on."""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import legato  # noqa: E402
from legato import plan as plans  # noqa: E402
from legato.tools import Tool  # noqa: E402
from test_harness import fake_env, server  # noqa: E402,F401


def call(name: str, args: dict) -> dict:
    return {"type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def shell(log: list, fail_on: str = "") -> Tool:
    def handler(a):
        log.append(a["command"])
        return ("boom\n[exit 1]" if fail_on and fail_on in a["command"] else "ok\n[exit 0]")
    return Tool("bash", "a shell", {"type": "object", "properties": {"command": {"type": "string"}}}, handler)


def labels(monkeypatch, where: dict):
    seen = []

    def find(png, text, near=None):
        seen.append((text, near))
        return where.get(text)
    monkeypatch.setattr(plans, "find_text", find)
    return seen


def test_a_plan_runs_shell_a_click_by_name_a_wait_and_finishes_in_one_call(server, monkeypatch):
    found = labels(monkeypatch, {"Morning Mix": (300, 120), "Pause": (500, 400)})
    ran, acts = [], []
    server["script"] = [{"tool_calls": [call("plan", {"steps": [
        {"action": "shell", "command": "open -a Music"},
        {"action": "click_text", "text": "Morning Mix", "double": True},
        {"action": "wait_for", "text": "Pause", "timeout_s": 3},
        {"action": "done"}]})]}]
    res = legato.Harness("m", server["url"], tools=[shell(ran)], plan=True).run("play my morning mix", fake_env(acts))
    assert res.done and res.steps == 1 and ran == ["open -a Music"]
    assert [(a.type, a.x, a.y) for a in acts] == [("double_click", 300, 120)]
    assert [t for t, _ in found] == ["Morning Mix", "Pause"]
    body = server["bodies"][0]
    assert [t["function"]["name"] for t in body["tools"]] == ["plan"]
    assert "click_text" in body["messages"][0]["content"] and "shell (command)" in body["messages"][0]["content"]
    assert res.timings[0]["plan_steps"] == 3 and res.timings[0]["plan_ran"] == 3


def test_a_label_that_is_not_there_stops_the_plan_and_the_model_sees_why(server, monkeypatch):
    labels(monkeypatch, {})
    acts = []
    server["script"] = [{"tool_calls": [call("plan", {"steps": [
        {"action": "left_click", "coordinate": [100, 100]}, {"action": "click_text", "text": "Save As…"},
        {"action": "type", "text": "report"}, {"action": "done"}]})]},
        {"tool_calls": [call("plan", {"steps": [{"action": "fail"}]})]}]
    res = legato.Harness("m", server["url"], plan=True).run("t", fake_env(acts))
    assert [a.type for a in acts] == ["click"] and not res.done and res.failed and res.steps == 2
    seen = json.dumps(server["bodies"][1]["messages"][-1])
    assert "ran 1 of 3 steps" in seen and 'no \\"Save As\\u2026\\" on the screen' in seen


def test_a_failed_command_stops_the_plan_and_done_after_it_does_not_count(server, monkeypatch):
    labels(monkeypatch, {})
    ran = []
    server["script"] = [{"tool_calls": [call("plan", {"steps": [
        {"action": "shell", "command": "make it"}, {"action": "shell", "command": "never"}, {"action": "done"}]})]},
        {"tool_calls": [call("plan", {"steps": [{"action": "shell", "command": "fixed"}, {"action": "done"}]})]}]
    res = legato.Harness("m", server["url"], tools=[shell(ran, fail_on="make")], plan=True).run("t", fake_env([]))
    assert ran == ["make it", "fixed"] and res.done and res.steps == 2
    assert "boom" in json.dumps(server["bodies"][1]["messages"][-1])


def test_done_right_after_a_plain_action_is_not_taken_but_done_alone_is(server, monkeypatch):
    labels(monkeypatch, {})
    server["script"] = [{"tool_calls": [call("plan", {"steps": [{"action": "key", "keys": "cmd+s"}, {"action": "done"}]})]},
                        {"tool_calls": [call("plan", {"steps": [{"action": "done"}]})]}]
    res = legato.Harness("m", server["url"], plan=True).run("t", fake_env([]))
    assert res.done and res.steps == 2
    assert "done was not taken" in json.dumps(server["bodies"][1]["messages"][-1])


def test_plans_written_in_the_text_and_plain_calls_are_steps_too(server, monkeypatch):
    labels(monkeypatch, {"OK": (10, 10)})
    ran, acts = [], []
    server["script"] = [{"content": 'Doing it. {"steps": [{"action": "click_text", "text": "OK"}, '
                                    '{"action": "shell", "command": "ls"}]}'},
                        {"tool_calls": [call("computer", {"action": "key", "keys": "Return"}),
                                        call("bash", {"command": "pwd"})]},
                        {"content": '{"name": "plan", "arguments": {"steps": [{"action": "done"}]}}'}]
    res = legato.Harness("m", server["url"], tools=[shell(ran)], plan=True).run("t", fake_env(acts))
    assert res.done and res.steps == 3 and ran == ["ls", "pwd"]
    assert [a.type for a in acts] == ["click", "key"]


def test_find_text_prefers_the_whole_label_then_the_nearest(monkeypatch):
    lines = [("Save As…  ⇧⌘S", lambda sub: (200, 50) if sub else (230, 50)), ("Save", lambda sub: (40, 300)),
             ("Save", lambda sub: (600, 300)), ("Unsaved changes", lambda sub: (5, 5))]
    monkeypatch.setattr(plans, "read_lines", lambda png: lines)
    assert plans.find_text(b"", "Save as...") == (200, 50)
    assert plans.find_text(b"", "save") == (40, 300)                      # whole line first, then reading order
    assert plans.find_text(b"", "Save", near=(590, 310)) == (600, 300)
    assert plans.find_text(b"", "changes") == (5, 5) and plans.find_text(b"", "change") is None
    assert plans.find_text(b"", "Open") is None


def test_a_planned_click_whose_place_changed_is_caught():
    from PIL import Image
    def png(color_box=None):
        im = Image.new("RGB", (200, 200), "white")
        if color_box:
            im.paste("black", color_box)
        b = io.BytesIO()
        im.save(b, "PNG")
        return b.getvalue()
    a, b = png(), png((90, 90, 130, 130))
    assert plans.target_moved(a, b, 100, 100) and not plans.target_moved(a, b, 10, 10)
    assert not plans.target_moved(a, a, 100, 100)


def test_tool_failed_reads_exit_codes_and_errors():
    assert plans.tool_failed("boom\n[exit 2]") and plans.tool_failed("error: TimeoutError")
    assert not plans.tool_failed("ok\n[exit 0]") and not plans.tool_failed("[exit 1] in a log\nfine\n[exit 0]")


def test_the_ending_fix_notes_one_reply_without_an_action_and_ends_on_the_second(server):
    server["script"] = [{"content": "The task is complete."}, {"content": "The task is already complete."}]
    res = legato.Harness("m", server["url"], end_fix=True).run("t", fake_env([]))
    assert res.done and res.steps == 2 and res.timings[-1].get("ended") == "no action twice"
    assert "If the task is complete, call done" in json.dumps(server["bodies"][1]["messages"][-1])


def test_the_ending_fix_resets_when_the_model_acts_and_is_off_by_default(server):
    tc = lambda a: {"type": "function", "function": {"name": "computer", "arguments": json.dumps(a)}}  # noqa: E731
    server["script"] = [{"content": "Hmm."}, {"tool_calls": [tc({"action": "key", "keys": "Return"})]},
                        {"content": "Done now."}, {"tool_calls": [tc({"action": "done"})]}]
    res = legato.Harness("m", server["url"], end_fix=True).run("t", fake_env([]))
    assert res.done and res.steps == 4 and "ended" not in res.timings[-1]
    server["bodies"].clear()
    server["script"] = [{"content": "complete"}, {"content": "complete"}, {"content": "complete"}]
    res = legato.Harness("m", server["url"]).run("t", fake_env([]), max_steps=3)
    assert not res.done and res.steps == 3


def test_a_labelled_click_goes_to_its_label_and_falls_back_to_its_coordinate(server, monkeypatch):
    labels(monkeypatch, {"Calendar": (210, 30)})
    acts = []
    server["script"] = [{"tool_calls": [call("plan", {"steps": [
        {"action": "left_click", "coordinate": [100, 100], "text": "Calendar"},
        {"action": "left_click", "coordinate": [500, 500], "text": "Nowhere"},
        {"action": "left_click", "coordinate": [10, 10], "text": "shift"}]})]},
        {"tool_calls": [call("plan", {"steps": [{"action": "fail"}]})]}]
    res = legato.Harness("m", server["url"], plan=True).run("t", fake_env(acts))
    assert [(a.type, a.x, a.y) for a in acts] == [("click", 210, 30), ("click", 500, 250), ("click", 10, 5)]
    assert acts[2].keys == "shift" and res.timings[0]["label_found"] == 1 and res.timings[0]["label_missed"] == 1


def test_an_applescript_step_runs_the_checked_tool_and_a_failed_one_stops_the_plan(server, monkeypatch):
    labels(monkeypatch, {})
    seen = []

    def handler(a):
        seen.append(a)
        return "did not compile, so nothing ran:\nboom" if "bad" in a["script"] else "ran (exit 0). Result:\nok"
    tool = Tool("applescript", "checked", {"type": "object", "properties": {"script": {"type": "string"}}}, handler)
    server["script"] = [{"tool_calls": [call("plan", {"steps": [
        {"action": "applescript", "script": "bad", "check": "c"}, {"action": "key", "keys": "Return"}]})]},
        {"tool_calls": [call("plan", {"steps": [{"action": "applescript", "script": "good"}, {"action": "done"}]})]}]
    acts = []
    res = legato.Harness("m", server["url"], tools=[tool], plan=True).run("t", fake_env(acts))
    assert res.done and res.steps == 2 and acts == [] and seen == [{"script": "bad", "check": "c"}, {"script": "good"}]
    assert "boom" in json.dumps(server["bodies"][1]["messages"][-1])


def test_tools_called_by_their_own_name_in_plan_mode_are_steps_not_silence(server, monkeypatch):
    labels(monkeypatch, {})
    seen = []
    tool = Tool("applescript", "checked", {"type": "object", "properties": {"script": {"type": "string"}}},
                lambda a: seen.append(a) or "ran (exit 0). Result:\nok")
    server["script"] = [{"tool_calls": [call("applescript", {"script": "s1"})]},
                        {"tool_calls": [call("applescript", {"script": "s2", "check": "c"})]},
                        {"tool_calls": [call("plan", {"steps": [{"action": "done"}]})]}]
    res = legato.Harness("m", server["url"], tools=[tool], plan=True, end_fix=True).run("t", fake_env([]))
    assert res.done and res.steps == 3 and seen == [{"script": "s1"}, {"script": "s2", "check": "c"}]
    assert not any(t.get("ended") for t in res.timings)


def test_an_unreadable_tool_call_is_not_counted_as_saying_it_is_done(server, monkeypatch):
    labels(monkeypatch, {})
    server["script"] = [{"tool_calls": [call("mystery", {"x": 1})]}, {"tool_calls": [call("mystery", {"x": 2})]},
                        {"tool_calls": [call("plan", {"steps": [{"action": "fail"}]})]}]
    res = legato.Harness("m", server["url"], plan=True, end_fix=True).run("t", fake_env([]))
    assert res.failed and not res.done and res.steps == 3


def test_the_change_log_says_what_text_came_and_went():
    before = [("Inbox", 10, 10), ("Sam", 20, 40), ("Draft", 20, 60)]
    now = [("Inbox", 10, 10), ("Sam", 20, 45), ("goodnight", 300, 500)]
    text = plans.describe_changes(before, now)
    assert 'appeared: "goodnight" (300, 500)' in text and 'went: "Draft" (20, 60)' in text
    assert "unchanged: 2 lines" in text and '"Sam"' not in text          # moved, not new
    assert plans.describe_changes(before, before).endswith("unchanged.")


def test_change_log_sends_one_screenshot_and_the_text_instead(server, monkeypatch):
    shots = iter([[("Messages", 100, 20)], [("Messages", 100, 20), ("Sam", 200, 300)],
                  [("Messages", 100, 20), ("Sam", 200, 300), ("goodnight", 400, 450)]] * 3)
    monkeypatch.setattr(plans, "screen_lines", lambda png: next(shots))
    labels(monkeypatch, {})
    server["script"] = [{"tool_calls": [call("plan", {"steps": [{"action": "key", "keys": "cmd+n"}]})]},
                        {"tool_calls": [call("plan", {"steps": [{"action": "type", "text": "goodnight"}]})]},
                        {"tool_calls": [call("plan", {"steps": [{"action": "done"}]})]}]
    res = legato.Harness("m", server["url"], plan=True, change_log=True, image_max=1).run("t", fake_env([]))
    assert res.done and res.steps == 3
    third = server["bodies"][2]["messages"]
    images = [p for m in third if isinstance(m["content"], list) for p in m["content"] if p["type"] == "image_url"]
    texts = [p["text"] for m in third if isinstance(m["content"], list) for p in m["content"] if p["type"] == "text"]
    assert len(images) == 1                                                  # only the newest screenshot
    assert any('appeared: "Sam" (200, 600)' in t for t in texts)          # norm1000 on the fake 1000x500 screen
    assert any('appeared: "goodnight"' in t for t in texts)


def test_a_stalled_call_is_sent_again_and_the_first_reply_wins(monkeypatch):
    import threading
    import time as _t
    h = legato.Harness("m", "http://x/v1", hedge_after_s=0.2)
    calls, gate = [], threading.Event()

    def chat(*a, **kw):
        calls.append(1)
        if len(calls) == 1:
            gate.wait(5)                     # the first request stalls
            return {"content": "slow", "s": 5.0}
        return {"content": "fast", "s": 0.05}
    monkeypatch.setattr(h.client, "chat", chat)
    t = _t.monotonic()
    rec = h._chat([])
    gate.set()
    assert rec["content"] == "fast" and rec["hedged"] and _t.monotonic() - t < 1.0 and 0.2 <= rec["s"] < 1.0
    h.hedge_after_s = None
    assert h._chat([])["content"] == "fast"


def test_the_first_labelled_click_can_skip_reading_the_screen(server, monkeypatch):
    seen = labels(monkeypatch, {"OK": (10, 10)})
    acts = []
    server["script"] = [{"tool_calls": [call("plan", {"steps": [
        {"action": "left_click", "coordinate": [500, 500], "text": "Save"},
        {"action": "left_click", "coordinate": [100, 100], "text": "OK"}, {"action": "done"}]})]},
        {"tool_calls": [call("plan", {"steps": [{"action": "done"}]})]}]
    res = legato.Harness("m", server["url"], plan=True, plan_trust_first_click=True).run("t", fake_env(acts))
    assert [(a.x, a.y) for a in acts] == [(500, 250), (10, 10)] and [t for t, _ in seen] == ["OK"]
    assert res.timings[0]["label_trusted"] == 1


def test_ocr_cache_reads_a_still_screen_once(monkeypatch):
    reads = []
    monkeypatch.setattr(plans, "_read_lines", lambda png: reads.append(png) or [("OK", lambda sub: (1, 2))])
    monkeypatch.setattr(plans, "_READ", {})
    monkeypatch.setattr(plans, "OCR_CACHE", True)
    assert plans.find_text(b"a", "OK") == (1, 2) and plans.find_text(b"a", "OK") == (1, 2)
    plans.find_text(b"b", "OK")
    assert reads == [b"a", b"b"]
    monkeypatch.setattr(plans, "OCR_CACHE", False)
    plans.find_text(b"a", "OK")
    assert reads == [b"a", b"b", b"a"]


def test_ocr_near_reads_the_box_around_the_click_first(monkeypatch):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (1000, 800), "white").save(buf, "PNG")
    sizes = []

    def read(png):
        size = Image.open(io.BytesIO(png)).size
        sizes.append(size)
        return [("OK", lambda sub: (5, 7))] if size == (640, 280) else []
    monkeypatch.setattr(plans, "_read_lines", read)
    monkeypatch.setattr(plans, "OCR_CACHE", False)
    assert plans.find_text_near(buf.getvalue(), "OK", (500, 400)) == (5 + 180, 7 + 260)
    assert plans.find_text_near(buf.getvalue(), "OK", (10, 10)) is None      # the box at a corner is 330x150
    assert sizes == [(640, 280), (330, 150)]


def test_ocr_near_falls_back_to_the_whole_screen(server, monkeypatch):
    seen = labels(monkeypatch, {"Cancel": (40, 40)})
    near = []
    monkeypatch.setattr(plans, "find_text_near",
                        lambda png, text, xy: near.append((text, xy)) or {"OK": (12, 34)}.get(text))
    acts = []
    server["script"] = [{"tool_calls": [call("plan", {"steps": [
        {"action": "left_click", "coordinate": [500, 500], "text": "OK"},
        {"action": "left_click", "coordinate": [100, 100], "text": "Cancel"}, {"action": "done"}]})]},
        {"tool_calls": [call("plan", {"steps": [{"action": "done"}]})]}]
    res = legato.Harness("m", server["url"], plan=True, ocr_near=True).run("t", fake_env(acts))
    assert [(a.x, a.y) for a in acts] == [(12, 34), (40, 40)]
    assert [t for t, _ in near] == ["OK", "Cancel"] and [t for t, _ in seen] == ["Cancel"]
    assert res.timings[0]["found_near"] == 1


def test_strict_done_needs_a_check_that_prints(server, monkeypatch):
    labels(monkeypatch, {"Saved": (10, 10)})
    for steps, taken in [([{"action": "shell", "command": "open -a Reminders"}, {"action": "done"}], False),
                         ([{"action": "shell", "command": "defaults read x"}, {"action": "done"}], True),
                         ([{"action": "wait_for", "text": "Saved"}, {"action": "done"}], False)]:
        out = {"open -a Reminders": "[exit 0]", "defaults read x": "1\n[exit 0]"}
        tool = Tool("bash", "shell", {"type": "object"}, lambda a: out[a["command"]])
        server["script"] = [{"tool_calls": [call("plan", {"steps": steps})]},
                            {"tool_calls": [call("plan", {"steps": [{"action": "done"}]})]}]
        res = legato.Harness("m", server["url"], plan=True, tools=[tool], strict_done=True).run("t", fake_env([]))
        assert res.done and res.steps == (1 if taken else 2), (steps, res.steps)
    sys_text = server["bodies"][-1]["messages"][0]["content"]
    assert "a wait_for does not count" in sys_text


def test_printed():
    assert not plans.printed("[exit 0]") and plans.printed("1\n[exit 0]")
    assert not plans.printed("ran (exit 0). Result:\n(nothing)")
    assert plans.printed("ran (exit 0). Result:\n(nothing)\n\ncheck (exit 0):\nDark")


def test_the_rapidocr_reader_places_labels_and_matches_words_it_ran_together(monkeypatch):
    class Fake:                                   # RapidOCR's answer: [box (4 points), text, score], elapsed
        def __call__(self, png):
            return [[[[100, 100], [300, 100], [300, 120], [100, 120]], "File Edit View", "0.9"],
                    [[[600, 400], [740, 400], [740, 420], [600, 420]], "Documentsaved", "0.9"]], 0.1
    monkeypatch.setattr(plans, "_RAPID", [Fake()])
    lines = plans._read_lines_rapid(b"")
    assert [l for l, _ in lines] == ["File Edit View", "Documentsaved"]
    assert lines[0][1](None) == (200, 110)
    x, y = lines[0][1]("edit")
    assert 180 < x < 220 and y == 110                     # "Edit" sits in the middle of "File Edit View"
    monkeypatch.setattr(plans, "read_lines", lambda png: lines)
    assert plans.find_text(b"", "Document saved") == (670, 410)
    assert plans.find_text(b"", "View")[0] > 260


def test_a_plan_with_no_shell_and_no_text_reader_is_promised_neither_and_runs_on_the_screen(server, monkeypatch):
    """Windows, Harness(plan=True) with no tools: the prompt offered shell, click_text and wait_for, Qwen planned
    them, none ran, and the run ended "out of steps ... 20 model turns, 0 actions". The prompt and the tool now offer
    only what runs here; a labelled click goes to its coordinate; a step that still can't run is said to the model."""
    monkeypatch.setattr(plans, "can_read", lambda: False)
    server["script"] = [
        {"tool_calls": [call("plan", {"steps": [{"action": "left_click", "coordinate": [500, 500], "text": "File"},
                                                {"action": "type", "text": "alpha"},
                                                {"action": "shell", "command": "notepad"}, {"action": "done"}]})]},
        {"tool_calls": [call("plan", {"steps": [{"action": "wait_for", "text": "alpha"}]})]},
        {"tool_calls": [call("plan", {"steps": [{"action": "done"}]})]}]
    acts = []
    res = legato.Harness("m", server["url"], plan=True).run("t", fake_env(acts))
    assert (res.status, res.steps) == ("done", 3) and [a.type for a in acts] == ["click", "type"]
    body = server["bodies"][0]
    system = body["messages"][0]["content"]
    for gone in ("shell", "applescript", "click_text", "wait_for", "label"):
        assert gone not in system and gone not in json.dumps(body["tools"]), gone
    assert "done counts only in a plan of its own" in system
    told = json.dumps(server["bodies"][1]["messages"][-1])
    assert "Step 3 of your plan was not run" in told and "there is no shell step in this run" in told
    assert "no text reader" in json.dumps(server["bodies"][2]["messages"][-1])


def test_the_plan_prompt_names_the_shell_that_runs_and_is_unchanged_with_everything():
    assert plans.fit_system(plans.PLAN_SYSTEM, "bash", True, True) == plans.PLAN_SYSTEM
    for part in (plans._LABEL_CLICKS, plans._STOPS, plans._DONE_RULE, plans._EXAMPLE):
        assert part in plans.PLAN_SYSTEM                      # what fit_system rewrites is still in the prompt
    win = plans.fit_system(plans.PLAN_SYSTEM, "PowerShell", False, False)
    assert "runs a PowerShell command" in win and "open -a" not in win and "applescript" not in win
    assert "(a shell command that exits 0)" in win and "wait_for" not in win
    win.format(w=1, h=1, coords="", batch=3)                  # still a format string
    assert plans.step_actions("", False, False) == ["left_click", "right_click", "double_click", "triple_click",
                                                    "left_click_drag", "type", "key", "scroll", "wait", "done", "fail"]
    assert plans.plan_tool(12) == plans.plan_tool(12, actions=plans.step_actions())
