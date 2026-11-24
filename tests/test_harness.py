"""legato.Harness against a stub OpenAI-compatible server and a fake desktop."""
from __future__ import annotations

import io
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import legato  # noqa: E402
from legato.harness import _calls, _parse_pixels  # noqa: E402
from legato.rl.fake import tiny_png  # noqa: E402


def tc(args: dict) -> dict:
    return {"type": "function", "function": {"name": "computer", "arguments": json.dumps(args)}}


@pytest.fixture()
def server():
    st = {"script": [], "bodies": [], "delay": 0.0}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            st["bodies"].append(body)
            time.sleep(st["delay"])
            step = st["script"].pop(0) if st["script"] else {"content": "", "tool_calls": [tc({"action": "done"})]}
            out = json.dumps({"choices": [{"message": {"content": step.get("content", ""),
                                                       **({"tool_calls": step["tool_calls"]} if step.get("tool_calls") else {})}}],
                              "usage": {"prompt_tokens": 10, "completion_tokens": 3}}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    st["url"] = f"http://127.0.0.1:{srv.server_address[1]}/v1"
    yield st
    srv.shutdown()


def fake_env(log: list, w: int = 1000, h: int = 500):
    png = tiny_png(1, w=8, h=8)
    import struct
    png = png[:16] + struct.pack(">II", w, h) + png[24:]      # claim a w x h screen in the IHDR header
    return legato.attach(screenshot=lambda: png, execute=lambda a: log.append(a))


def test_batched_turn_then_done(server):
    server["script"] = [
        {"tool_calls": [tc({"action": "left_click", "coordinate": [500, 500]}), tc({"action": "type", "text": "42"}),
                        tc({"action": "key", "keys": "Return"})]},
        {"content": "", "tool_calls": [tc({"action": "done"})]}]
    log = []
    res = legato.Harness("m", server["url"]).run("enter 42", fake_env(log))
    assert res.done and res.steps == 2 and [a.type for a in log] == ["click", "type", "key"]
    assert (log[0].x, log[0].y) == (500, 250) and log[1].text == "42" and log[2].keys == "Return"
    body = server["bodies"][0]
    assert body["tools"][0]["function"]["name"] == "computer" and body["messages"][1]["content"][0]["text"] == "Task: enter 42"
    assert "done in" in res.report() and "model" in res.report()


def test_text_json_fallback_and_batch_cap(server):
    server["script"] = [{"content": 'Click then type. {"action": "left_click", "coordinate": [10, 20]} '
                                    '{"action": "type", "text": "a"} {"action": "type", "text": "b"}'},
                        {"content": '{"action": "done"}'}]
    log = []
    res = legato.Harness("m", server["url"], native_tools=False, max_batch=2).run("t", fake_env(log))
    assert res.done and [a.type for a in log] == ["click", "type"] and "tools" not in server["bodies"][0]


def test_history_drops_screenshots_in_chunks_keeping_the_prefix():
    h = legato.Harness("m", "http://x/v1", image_max=2, fold=2)
    turns = []
    prefixes = []
    for i in range(6):
        turns.append({"role": "user", "png_b64": "AAAA"})
        msgs = h._messages("task", 100, 100, turns)
        imgs = sum(1 for m in msgs if m["role"] == "user" and any(p.get("type") == "image_url" for p in m["content"]))
        assert imgs <= 3                                   # at most image_max + fold - 1 kept
        prefixes.append(json.dumps(msgs[:-1]))
        turns.append({"role": "assistant", "text": f"step {i}"})
    # between chunk drops the earlier messages are byte-identical, so a prefix cache keeps hitting
    assert prefixes[3].startswith(prefixes[2][:-1]) and prefixes[5].startswith(prefixes[4][:-1])


def test_jpeg_screenshots_keep_the_size_and_cut_the_bytes():
    from PIL import Image
    import base64
    buf = io.BytesIO()
    Image.effect_noise((320, 200), 40).convert("RGB").save(buf, format="PNG")
    png = base64.b64encode(buf.getvalue()).decode()
    turns = [{"role": "user", "png_b64": png}]
    url = lambda h: [p for m in h._messages("task", 320, 200, turns) if m["role"] == "user"  # noqa: E731
                     for p in m["content"] if p.get("type") == "image_url"][0]["image_url"]["url"]
    assert url(legato.Harness("m", "http://x/v1", image_format="png")).startswith("data:image/png;base64,")
    h = legato.Harness("m", "http://x/v1")                                                    # JPEG by default
    first = url(h)
    assert first.startswith("data:image/jpeg;base64,") and url(h) == first                    # encoded once, stable
    jpg = base64.b64decode(first.split(",", 1)[1])
    assert Image.open(io.BytesIO(jpg)).size == (320, 200) and len(jpg) < len(buf.getvalue())
    assert url(legato.Harness("m", "http://x/v1")).startswith("data:image/") and \
        legato.Harness("m", "http://x/v1")._image_url("bm90IGFuIGltYWdl") == "data:image/png;base64,bm90IGFuIGltYWdl"  # unreadable: as it came
    with pytest.raises(ValueError):
        legato.Harness("m", "http://x/v1", image_format="webp")


def test_pixel_coordinates():
    a, _ = _parse_pixels({"action": "left_click", "coordinate": [960, 540]}, 1920, 1080)
    assert (a.x, a.y) == (960, 540)


def test_calls_parsing_variants():
    assert _calls({"tool_calls": [tc({"action": "wait"})]}) == [{"action": "wait"}]
    assert _calls({"content": '<tool_call>{"name": "computer", "arguments": {"action": "key", "keys": "ctrl+s"}}</tool_call>'}) \
        == [{"action": "key", "keys": "ctrl+s"}]


def test_step_timeout_ends_the_run(server):
    server["delay"] = 1.0
    t = time.perf_counter()
    res = legato.Harness("m", server["url"], step_timeout_s=0.2, retries=0).run("t", fake_env([]))
    assert res.error and "timeout" in res.error and time.perf_counter() - t < 3


def test_reference_profile_screenshot_per_action_keep_all_and_end_on_text(server):
    server["script"] = [
        {"content": "I'll click and type.", "tool_calls": [tc({"action": "left_click", "coordinate": [683, 384]}),
                                                           tc({"action": "type", "text": "x"})]},
        {"tool_calls": [tc({"action": "screenshot"})]},
        {"content": "The task is complete."}]
    log = []
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (1920, 1080), (40, 40, 40)).save(buf, format="PNG")
    png = buf.getvalue()
    env = legato.attach(screenshot=lambda: png, execute=lambda a: log.append(a))
    res = legato.Harness.reference("anthropic/claude-x", server["url"]).run("t", env)
    assert res.done and res.steps == 3 and [a.type for a in log] == ["click", "type"]
    assert (log[0].x, log[0].y) == (960, 540)              # pixels of the 1366x768 screenshot, mapped onto 1920x1080
    third = server["bodies"][2]["messages"]
    imgs = [p for m in third if m["role"] == "user" for p in m["content"] if p.get("type") == "image_url"]
    assert len(imgs) == 4                                  # first + one per action + the screenshot action, none dropped
    assert server["bodies"][2]["cache_control"] == {"type": "ephemeral"}
    assert all("cache_control" not in p for m in third if isinstance(m["content"], list) for p in m["content"])
    assert "screenshot" in server["bodies"][0]["tools"][0]["function"]["parameters"]["properties"]["action"]["enum"]
    assert third[0]["content"].startswith("<SYSTEM_CAPABILITY>")


def test_legato_profile_one_screenshot_per_batch(server):
    server["script"] = [{"tool_calls": [tc({"action": "left_click", "coordinate": [500, 500]}),
                                        tc({"action": "type", "text": "a"})]},
                        {"content": "all done"},
                        {"tool_calls": [tc({"action": "done"})]}]
    res = legato.Harness("qwen/q", server["url"]).run("t", fake_env([]))
    assert res.done and res.steps == 3                     # text alone does not end the legato loop
    second = server["bodies"][1]["messages"]
    assert sum(1 for m in second if m["role"] == "user" for p in m["content"] if p.get("type") == "image_url") == 2
    assert "cache_control" not in server["bodies"][1]       # no cache marker for non-Claude models


def test_to_pyautogui():
    from legato.actions import Action
    from legato.rl.collect import to_pyautogui
    assert to_pyautogui(Action("click", 10, 20)) == "pyautogui.click(10, 20)"
    assert to_pyautogui(Action("key", keys="ctrl+S")) == "pyautogui.hotkey('ctrl', 's')"
    assert to_pyautogui(Action("key", keys="Return")) == "pyautogui.press('enter')"
    assert to_pyautogui(Action("type", text="a'b\n")) == 'pyautogui.write("a\'b\\n")'
    assert to_pyautogui(Action("scroll", 5, 6, direction="down", amount=2)) == "pyautogui.scroll(-2, x=5, y=6)"
    assert to_pyautogui(Action("done")) == "DONE" and to_pyautogui(None) == "pass"


def test_collect_with_reference_and_legato_harnesses(server, tmp_path):
    from legato.rl import DesktopConfig, HarnessConfig, PolicyConfig, collect
    from legato.rl.trajectory import read_rollouts
    tasks = [{"task_id": "t0", "domain": "office", "reward": 1.0}]
    for name in ("legato", "reference"):
        server["script"] = [{"tool_calls": [tc({"action": "left_click", "coordinate": [500, 500]}),
                                            tc({"action": "key", "keys": "ctrl+s"})]},
                            {"tool_calls": [tc({"action": "done"})]}]
        collect(tasks, PolicyConfig(server["url"], "m", version=name, logprobs=False, token_ids=False), tmp_path / name,
                desktops=1, group=1, desktop=DesktopConfig(backend="fake"), harness=HarnessConfig(name=name),
                start_stagger_s=0.0)
        r = read_rollouts(tmp_path / name)[0]
        assert r["status"] == "ok" and r["reward"] == 1.0 and r["n_steps"] == 2
        assert [x["action"] for x in r["steps"][0]["actions"]] == ["pyautogui.click(4, 4)", "pyautogui.hotkey('ctrl', 's')"]
        assert r["steps"][1]["actions"][-1]["action"] == "DONE"


def test_h2h_report(server, tmp_path):
    from legato.rl import DesktopConfig, HarnessConfig, PolicyConfig, collect
    from legato.rl.bench import h2h
    tasks = [{"task_id": f"t{i}", "domain": "office", "reward": 1.0} for i in range(3)]
    for name, act_s in (("reference", 0.05), ("legato", 0.0)):
        collect(tasks, PolicyConfig(server["url"], "m", version=name, logprobs=False, token_ids=False), tmp_path / name,
                desktops=1, group=1, desktop=DesktopConfig(backend="fake", fake={"act_s": act_s}),
                harness=HarnessConfig(name=name), start_stagger_s=0.0)
    res = h2h.compare({"reference": tmp_path / "reference", "legato": tmp_path / "legato"}, tasks, "reference")
    assert res["arms"]["legato"]["passed"] == 3 and res["arms"]["legato"]["vs_ref"]["tasks"] == 3
    md = h2h.report_md(res, {"title": "T", "arms": {"legato": "x"}})
    assert "| legato | 3 | 3 | 100% |" in md and "Against **reference**" in md


def test_cut_off_reply_gets_a_note_and_the_run_continues(server):
    server["script"] = [{"content": ""}, {"tool_calls": [tc({"action": "done"})]}]
    # the stub has no finish_reason; patch the client to report a length cut-off on the first call
    h = legato.Harness("qwen/q", server["url"])
    real = h.client.chat
    n = {"i": 0}

    def chat(*a, **k):
        rec = real(*a, **k)
        n["i"] += 1
        if n["i"] == 1:
            rec["finish_reason"] = "length"
        return rec
    h.client.chat = chat
    res = h.run("t", fake_env([]))
    assert res.done and res.steps == 2 and server["bodies"][0]["max_tokens"] == 4096
    texts = [p.get("text", "") for m in server["bodies"][1]["messages"] if m["role"] == "user" for p in m["content"]]
    assert any("token limit" in t for t in texts)


# ------------------------------------------------------------ tools beside computer
def named(name: str, args: dict) -> dict:
    return {"type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def recorder(log: list, name: str = "note", fail: bool = False) -> legato.Tool:
    def handler(args):
        if fail:
            raise RuntimeError("disk full")
        log.append(("tool", args["text"]))
        return f"noted {args['text']}"
    return legato.Tool(name, "Write a note.", {"type": "object", "properties": {"text": {"type": "string"}}}, handler)


def counting_env(log: list, shots: list):
    png = fake_env([])._shot
    return legato.attach(screenshot=lambda: shots.append(1) or png(), execute=lambda a: log.append(a.type))


def test_tools_run_in_order_and_results_reach_the_next_turn(server):
    server["script"] = [
        {"tool_calls": [tc({"action": "left_click", "coordinate": [500, 500]}), named("note", {"text": "x"}),
                        tc({"action": "type", "text": "42"})]},
        {"tool_calls": [tc({"action": "done"})]}]
    log = []
    res = legato.Harness("m", server["url"], tools=[recorder(log)]).run("t", counting_env(log, []))
    assert res.done and log == ["click", ("tool", "x"), "type"]
    first, second = server["bodies"]
    assert [t["function"]["name"] for t in first["tools"]] == ["computer", "note"]
    assert "Besides `computer` you have: `note` (Write a note.)" in first["messages"][0]["content"]
    last = second["messages"][-1]["content"]
    assert last[0] == {"type": "text", "text": "note result:\nnoted x"} and last[1]["type"] == "image_url"
    assert '"tool": "note"' in second["messages"][-2]["content"]
    assert 'note({"text": "x"})' in res.actions and "tools" in res.report()


def test_no_tools_sends_the_same_request_as_before(server):
    legato.Harness("m", server["url"]).run("t", fake_env([]))
    body = server["bodies"][0]
    assert body["tools"] == [legato.computer_tool(legato.harness.CLAUDE_STYLE_ACTIONS)]
    assert "Besides" not in body["messages"][0]["content"]


def test_a_raising_tool_is_reported_and_a_tool_only_turn_gets_a_fresh_screenshot(server):
    server["script"] = [{"tool_calls": [named("note", {"text": "x"})]}, {"tool_calls": [tc({"action": "done"})]}]
    shots = []
    res = legato.Harness("m", server["url"], tools=[recorder([], fail=True)]).run("t", counting_env([], shots))
    assert res.done and res.error is None
    last = server["bodies"][1]["messages"][-1]["content"]
    assert last[0]["text"] == "note result:\nerror: RuntimeError: disk full" and last[1]["type"] == "image_url"
    assert len(shots) == 2                                 # the first screenshot, then one after the tool ran


def test_an_unknown_tool_stops_the_batch(server):
    server["script"] = [{"tool_calls": [named("str_replace_editor", {"path": "x"}), tc({"action": "wait"})]},
                        {"tool_calls": [tc({"action": "done"})]}]
    log = []
    res = legato.Harness("m", server["url"], tools=[recorder(log)]).run("t", counting_env(log, []))
    assert res.done and log == []
    assert "(unknown tool: str_replace_editor)" in server["bodies"][1]["messages"][-2]["content"]


def test_tools_without_native_tool_calls(server):
    server["script"] = [{"content": 'Noting. {"name": "note", "arguments": {"text": "y"}}'}, {"content": '{"action": "done"}'}]
    log = []
    res = legato.Harness("m", server["url"], native_tools=False, tools=[recorder(log)]).run("t", fake_env([]))
    assert res.done and log == [("tool", "y")]
    system = server["bodies"][0]["messages"][0]["content"]
    assert '{"name": "<tool>", "arguments": {...}}' in system and '- note: Write a note. Parameters: {"type"' in system


def test_calls_written_in_the_shape_of_past_calls_are_read(server):
    # the history writes a tool call as {"tool": "note", ...}; models copy that shape, for done too
    server["script"] = [{"content": 'Noting. {"tool": "note", "text": "y"}'},
                        {"content": 'The note is written; the task is complete.\n{"tool": "done"}'}]
    log = []
    res = legato.Harness("m", server["url"], tools=[recorder(log)]).run("t", counting_env(log, []))
    assert res.done and res.steps == 2 and log == [("tool", "y")]


def test_the_tool_shape_maps_to_actions_and_known_tools_only():
    from legato.harness import _named_calls
    assert _named_calls({"content": '{"tool": "done"}'}) == [("computer", {"action": "done"})]
    assert _named_calls({"content": '{"tool": "FAIL"}'}) == [("computer", {"action": "fail"})]
    assert _named_calls({"content": '{"tool": "left_click", "coordinate": [1, 2]}'}) == \
        [("computer", {"action": "left_click", "coordinate": [1, 2]})]
    assert _named_calls({"content": '{"tool": "computer", "action": "wait"}'}) == \
        [("computer", {"tool": "computer", "action": "wait"})]
    assert _named_calls({"content": '{"tool": "bash", "command": "ls"}'}, ("bash",)) == [("bash", {"command": "ls"})]
    assert _named_calls({"content": '{"tool": "bash", "arguments": {"command": "ls"}}'}, ("bash",)) == \
        [("bash", {"command": "ls"})]
    assert _named_calls({"content": '{"tool": "bash", "command": "ls"}'}) == []        # not a tool here
    assert _named_calls({"content": '{"tool": "music", "song": "x"}'}, ("bash",)) == []
    said_and_made = {"content": '{"tool": "bash", "command": "ls"}',
                     "tool_calls": [named("bash", {"command": "ls"})]}
    assert _named_calls(said_and_made, ("bash",)) == [("bash", {"command": "ls"})]     # once


def test_tool_names_must_be_free():
    with pytest.raises(ValueError):
        legato.Harness("m", "http://x/v1", tools=[recorder([], name="computer")])
    with pytest.raises(ValueError):
        legato.Harness("m", "http://x/v1", tools=[recorder([]), recorder([])])


def test_written_actions_run_before_native_tool_calls(server):
    server["script"] = [{"content": 'Click first. {"action": "left_click", "coordinate": [500, 500]}',
                         "tool_calls": [named("note", {"text": "z"})]},
                        {"tool_calls": [tc({"action": "done"})]}]
    log = []
    res = legato.Harness("m", server["url"], tools=[recorder(log)]).run("t", counting_env(log, []))
    assert res.done and log == ["click", ("tool", "z")]


def _noise_png(w=320, h=200) -> str:
    from PIL import Image
    import base64
    buf = io.BytesIO()
    Image.effect_noise((w, h), 40).convert("RGB").save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def test_change_log_keeps_only_the_newest_turns_text():
    h = legato.Harness("m", "http://x/v1", plan=True, change_log=True, image_max=1, change_log_turns=2)
    turns = []
    for i in range(4):
        turns += [{"role": "user", "png_b64": "AAAA", "change": f"change {i}"}, {"role": "assistant", "text": "x"}]
    texts = [p["text"] for m in h._messages("task", 100, 100, turns[:-1]) if m["role"] == "user"
             for p in m["content"] if p.get("type") == "text"]
    assert "change 3" in texts and "change 2" in texts and "change 1" not in texts and "change 0" not in texts
    assert sum("left out" in t for t in texts) == 2


def test_history_scale_shrinks_older_screenshots_only():
    import base64
    from PIL import Image
    h = legato.Harness("m", "http://x/v1", image_max=3, history_scale=0.5, image_format="jpeg")
    turns = [{"role": "user", "png_b64": _noise_png()}, {"role": "assistant", "text": "a"},
             {"role": "user", "png_b64": _noise_png()}]
    urls = [p["image_url"]["url"] for m in h._messages("task", 320, 200, turns) if m["role"] == "user"
            for p in m["content"] if p.get("type") == "image_url"]
    sizes = [Image.open(io.BytesIO(base64.b64decode(u.split(",", 1)[1]))).size for u in urls]
    assert sizes == [(160, 100), (320, 200)] and all(u.startswith("data:image/jpeg") for u in urls)


def test_progress_note_is_asked_for_and_kept_in_the_history():
    h = legato.Harness("m", "http://x/v1", plan=True, progress_note=True)
    msgs = h._messages("task", 100, 100, [{"role": "user", "png_b64": "AAAA"}])
    assert "progress" in msgs[0]["content"]
    rec = {"content": "Opening Notes.", "tool_calls": [{"function": {"name": "plan", "arguments": json.dumps(
        {"progress": "Notes open; next: paste the text", "steps": [{"action": "wait"}]})}}]}
    from legato.harness import Episode
    plan = h._decide_plan(rec, msgs, Episode("task", 100, 100, 100, 100))
    assert "Progress: Notes open; next: paste the text" in plan.reply


def test_collect_with_a_planner_and_a_pointer(server, tmp_path, monkeypatch):
    from legato.rl import DesktopConfig, HarnessConfig, PolicyConfig, collect
    from legato.rl.trajectory import read_rollouts

    def named(name: str, args: dict) -> dict:
        return {"type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
    server["script"] = [
        {"tool_calls": [named("act", {"status": "continue", "actions": [
            {"key": "ctrl+o"}, {"click": "the Open button in the dialog"}, {"key": "Return"}]})]},     # the planner
        {"tool_calls": [named("point", {"found": True, "coordinate": [500, 500]})]},                    # the pointer
        {"tool_calls": [named("act", {"status": "done"})]}]                                             # the planner
    monkeypatch.setenv("PLANNER_KEY", "k")
    opts = {"coords": "norm1000", "max_calls": 12,
            "planner": {"model": "big", "base_url": server["url"], "api_key_env": "PLANNER_KEY", "style": "point"}}
    collect([{"task_id": "t0", "domain": "office", "reward": 1.0}],
            PolicyConfig(server["url"], "small", version="planned", logprobs=False, token_ids=False), tmp_path,
            desktops=1, group=1, max_steps=40, desktop=DesktopConfig(backend="fake"),
            harness=HarnessConfig(name="legato", options=opts), start_stagger_s=0.0)
    r = read_rollouts(tmp_path)[0]
    assert r["status"] == "ok" and r["reward"] == 1.0 and r["n_steps"] == 3
    acts = [[x["action"] for x in st["actions"]] for st in r["steps"]]
    assert acts == [["pyautogui.hotkey('ctrl', 'o')"], ["pyautogui.click(4, 4)", "pyautogui.press('enter')"], ["DONE"]]
    assert [st["model"]["calls"] for st in r["steps"]] == [1, 1, 1]
    notes = [st["harness"] for st in r["steps"]]
    assert notes[-1]["planner_calls"] == 2 and notes[-1]["pointer_calls"] == 1 and notes[-1]["end"] == "done"
    assert [m["model"] for m in server["bodies"]] == ["big", "small", "big"]
    assert "planner" in notes[0]["trace"][0] and "point" in notes[1]["trace"][0]


def test_an_action_left_open_before_the_models_closing_tags_is_read():
    """Qwen3.8-27B sometimes writes the call as text and closes its tags, not the object (legato-research EXP-0075)."""
    from legato.harness import _text_calls
    said = 'Click the Save button. {"action": "left_click", "coordinate": [806, 155] </parameter> </function> </tool_call>'
    assert _text_calls(said) == [("computer", {"action": "left_click", "coordinate": [806, 155]})]
    drag = ('I will drag it. {"action": "left_click_drag", "coordinate": [289, 541] </parameter> <parameter=end_coordinate> '
            '[620, 541] </parameter> </function>')
    assert _text_calls(drag) == [("computer", {"action": "left_click_drag", "coordinate": [289, 541]})]
    assert _text_calls('Now I will click Export. {"action": "left_click", "coordinate":') == []      # cut off: nothing to run
    assert _text_calls('{"action": "key", "keys": "ctrl+s"} then {"action": "type", "text": "a" </parameter>') == [
        ("computer", {"action": "key", "keys": "ctrl+s"}), ("computer", {"action": "type", "text": "a"})]
