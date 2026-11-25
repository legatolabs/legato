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
            step = st["script"].pop(0) if st["script"] else {"content": "", "tool_calls": [tc({"action": "done"})]}
            time.sleep(step.get("delay", st["delay"]))           # a step's own delay, else the server's
            if "http" in step:                    # an error reply: {"http": 400, "body": {...}}
                out = json.dumps(step["body"]).encode()
                self.send_response(step["http"])
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)
                return
            out = json.dumps({"choices": [{"message": {"content": step.get("content", ""),
                                                       **({"tool_calls": step["tool_calls"]} if step.get("tool_calls") else {})}}],
                              "usage": {"prompt_tokens": 10, "completion_tokens": 3, **step.get("usage", {})}}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.handle_error = lambda *a: None   # a reply to a client that gave up (a timeout test): no traceback in later tests
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
        assert [x["action"] for x in r["steps"][0]["actions"]] == ["pyautogui.click(320, 200)", "pyautogui.hotkey('ctrl', 's')"]
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
    assert acts == [["pyautogui.hotkey('ctrl', 'o')"], ["pyautogui.click(320, 200)", "pyautogui.press('enter')"], ["DONE"]]
    assert [st["model"]["calls"] for st in r["steps"]] == [1, 1, 1]
    notes = [st["harness"] for st in r["steps"]]
    assert notes[-1]["planner_calls"] == 2 and notes[-1]["pointer_calls"] == 1 and notes[-1]["end"] == "done"
    assert [m["model"] for m in server["bodies"]] == ["big", "small", "big"]
    assert "planner" in notes[0]["trace"][0] and "point" in notes[1]["trace"][0]


def test_an_action_left_open_before_the_models_closing_tags_is_read():
    """Qwen3.8-27B sometimes writes the call as text and closes its tags, not the object."""
    from legato.harness import _text_calls
    said = 'Click the Save button. {"action": "left_click", "coordinate": [806, 155] </parameter> </function> </tool_call>'
    assert _text_calls(said) == [("computer", {"action": "left_click", "coordinate": [806, 155]})]
    drag = ('I will drag it. {"action": "left_click_drag", "coordinate": [289, 541] </parameter> <parameter=end_coordinate> '
            '[620, 541] </parameter> </function>')
    assert _text_calls(drag) == [("computer", {"action": "left_click_drag", "coordinate": [289, 541]})]
    assert _text_calls('Now I will click Export. {"action": "left_click", "coordinate":') == []      # cut off: nothing to run
    assert _text_calls('{"action": "key", "keys": "ctrl+s"} then {"action": "type", "text": "a" </parameter>') == [
        ("computer", {"action": "key", "keys": "ctrl+s"}), ("computer", {"action": "type", "text": "a"})]


# ------------------------------------------------------------ how a run ends: status, deadline, cancel, desktop errors
CLICK = {"tool_calls": [tc({"action": "left_click", "coordinate": [500, 500]})]}


def test_every_ending_has_a_status(server):
    server["script"] = [CLICK, {"tool_calls": [tc({"action": "done", "text": "42"})]}]
    res = legato.Harness("m", server["url"]).run("t", fake_env([]))
    assert (res.status, res.done, res.answer) == ("done", True, "42")
    server["script"] = [{"tool_calls": [tc({"action": "fail"})]}]
    res = legato.Harness("m", server["url"]).run("t", fake_env([]))
    assert (res.status, res.failed, res.error) == ("failed", True, "the model called fail and gave no reason")
    server["script"] = [CLICK] * 3
    res = legato.Harness("m", server["url"]).run("t", fake_env([]), max_steps=2)
    assert (res.status, res.done, res.failed, res.steps) == ("max_steps", False, False, 2)
    assert res.error == "max_steps (2) ran out before the model called done" and res.report().startswith("out of steps in")
    server["script"] = [{"tool_calls": [tc({"action": "fail", "text": "there is no such file"})]}]
    r2 = legato.Harness("m", server["url"]).run("t", fake_env([]))         # why, in error for every status but done
    assert (r2.status, r2.answer, r2.error) == ("failed", "there is no such file", "there is no such file")
    assert r2.report().startswith("failed in")
    assert res.report().startswith("out of steps")
    server["script"] = [{"http": 401, "body": {"error": {"message": "No auth credentials found", "code": 401}}}]
    res = legato.Harness("m", server["url"], retries=0).run("t", fake_env([]))
    assert (res.status, res.failed) == ("error", False) and res.error.startswith("HTTP 401: No auth credentials found.")


def test_harness_run_and_legato_run_default_to_40_steps():
    import inspect
    from legato.delegate import run_delegated
    from legato.point import run_pointed
    for fn in (legato.Harness.run, legato.run, run_pointed, run_delegated):
        assert inspect.signature(fn).parameters["max_steps"].default == 40, fn


def test_usage_is_a_typed_dict_and_a_plain_dict():
    from typing import get_type_hints
    from legato.harness import Usage
    res = legato.RunResult("t")
    assert type(res.usage) is dict and res.usage == {"prompt": 0, "completion": 0, "cached": 0, "cost_usd": 0.0,
                                                     "complete": True}
    assert get_type_hints(legato.RunResult)["usage"] is Usage and set(Usage.__annotations__) == set(res.usage)
    assert legato.Usage is Usage and set(legato.RunStatus.__args__) == {"done", "failed", "max_steps", "timeout", "max_cost",
                                                                       "cancelled", "stopped", "error"}


def test_timeout_ends_the_run_between_steps_with_what_was_done(server):
    server["script"], server["delay"] = [CLICK] * 20, 0.3
    log = []
    t = time.perf_counter()
    res = legato.Harness("m", server["url"]).run("t", fake_env(log), timeout_s=1.0)
    assert res.status == "timeout" and not res.done and not res.failed and "time limit (1 s)" in res.error
    assert 1 <= res.steps < 20 and len(log) >= 1 and res.actions[0].startswith("click")
    assert res.usage["prompt"] >= 10 * res.steps and time.perf_counter() - t < 2.5


def test_timeout_caps_a_model_call_at_the_time_left(server):
    server["script"], server["delay"] = [CLICK], 3.0      # one slow call; the step timeout alone would wait for it
    t = time.perf_counter()
    h = legato.Harness("m", server["url"], step_timeout_s=60.0, retries=2)
    res = h.run("t", fake_env([]), timeout_s=0.5)
    assert res.status == "timeout" and res.steps == 0 and time.perf_counter() - t < 1.8
    assert h.client.deadline is None                        # the next run gets no leftover deadline


def test_cancel_ends_the_run_before_the_next_model_call(server):
    server["script"] = [CLICK] * 5
    stop = threading.Event()
    log = []
    res = legato.Harness("m", server["url"]).run("t", fake_env(log), cancel=stop,
                                                  on_step=lambda turn, acts, timing: turn == 1 and stop.set())
    assert (res.status, res.error, res.steps, len(log)) == ("cancelled", "cancelled", 2, 2)
    assert len(server["bodies"]) == 2 and res.usage["prompt"] == 20 and len(res.actions) == 2
    stop.clear()
    stop.set()                                              # set before it starts: nothing runs at all
    res = legato.Harness("m", server["url"]).run("t", fake_env([]), cancel=stop)
    assert res.status == "cancelled" and res.steps == 0 and len(server["bodies"]) == 2


def test_cancel_from_another_thread(server):
    server["script"], server["delay"] = [CLICK] * 50, 0.05
    stop = threading.Event()
    threading.Timer(0.4, stop.set).start()
    res = legato.Harness("m", server["url"]).run("t", fake_env([]), cancel=stop)
    assert res.status == "cancelled" and 1 <= res.steps < 50


def flaky_env(log: list, fail_at: int):
    """A desktop whose fail_at-th action raises, as a window that can no longer be captured does."""
    png = fake_env([]).observe().screenshot

    def execute(a):
        log.append(a)
        if len(log) == fail_at:
            raise RuntimeError("the window could not be captured")
    return legato.attach(screenshot=lambda: png, execute=execute)


def test_a_desktop_error_mid_run_is_returned_with_the_usage_so_far(server):
    server["script"] = [CLICK, {"tool_calls": [tc({"action": "type", "text": "a"}), tc({"action": "key", "keys": "x"})]}]
    log = []
    res = legato.Harness("m", server["url"]).run("t", flaky_env(log, 2), raise_desktop_errors=False)
    assert res.status == "error" and not res.done and not res.failed
    assert res.error == "the desktop failed: the window could not be captured"
    assert res.steps == 2 and res.usage["prompt"] == 20 and res.actions == ["click (500, 250)"]
    assert res.report().startswith("the desktop failed")


def test_a_desktop_error_still_raises_out_of_harness_run_by_default(server):
    """Callers of Harness.run catch the exception to mark a run as crashed."""
    server["script"] = [CLICK, CLICK]
    with pytest.raises(RuntimeError, match="could not be captured"):
        legato.Harness("m", server["url"]).run("t", flaky_env([], 2))


def test_an_exception_from_on_step_is_raised_not_returned(server):
    class Mine(Exception):
        pass

    def on_step(*a):
        raise Mine("the caller's own")
    server["script"] = [CLICK]
    with pytest.raises(Mine):
        legato.Harness("m", server["url"]).run("t", fake_env([]), on_step=on_step, raise_desktop_errors=False)


def test_legato_run_returns_desktop_errors_and_takes_a_deadline(server, monkeypatch):
    import legato.agent
    server["script"] = [CLICK, CLICK]
    res = legato.run("t", flaky_env([], 2), model="m", base_url=server["url"], api_key="")
    assert res.status == "error" and res.task == "t" and res.usage["prompt"] == 20
    server["script"], server["delay"] = [CLICK] * 20, 0.2
    res = legato.run("t", fake_env([]), model="m", base_url=server["url"], api_key="", timeout_s=0.5)
    assert res.status == "timeout" and res.steps >= 1

    def no_docker(*a, **k):
        raise RuntimeError("Docker is not running")
    monkeypatch.setattr(legato.agent, "open_desktop", no_docker)
    res = legato.run("t", "docker", model="m", base_url=server["url"], api_key="")
    assert (res.status, res.error) == ("error", "cannot start the desktop: Docker is not running")


def test_provider_error_bodies_are_cleaned(server):
    """OpenRouter's 400 carries the account's user_id: the error keeps the status and the message only."""
    server["script"] = [{"http": 400, "body": {"error": {"message": "qwen/no-such-model-xyz is not a valid model ID",
                                                         "code": 400}, "user_id": "user_3HscRKwEPQm2iykBVKsF6DLA36Q"}}]
    res = legato.Harness("qwen/no-such-model-xyz", server["url"]).run("t", fake_env([]))
    assert res.error == "HTTP 400: qwen/no-such-model-xyz is not a valid model ID" and res.status == "error"


def test_a_desktop_that_fails_mid_rollout_is_still_retried_by_rl(server, tmp_path):
    """legato rl drives Harness.begin/decide/feed, not Harness.run: a desktop failure is still an env error, retried."""
    from legato.rl import DesktopConfig, HarnessConfig, PolicyConfig, collect
    from legato.rl.trajectory import read_rollouts
    server["script"] = [CLICK, CLICK, {"tool_calls": [tc({"action": "done"})]}] * 3
    s = collect([{"task_id": "t0", "reward": 1.0}], PolicyConfig(server["url"], "m", logprobs=False, token_ids=False),
                tmp_path, desktops=1, group=1, env_retries=1, desktop=DesktopConfig(backend="fake", fake={"fail_act_every": 2}),
                harness=HarnessConfig(name="legato"), start_stagger_s=0.0)
    rs = read_rollouts(tmp_path)
    assert [r["status"] for r in rs] == ["retried", "ok"] and s["env_errors"] == 1
    assert rs[0]["error"]["kind"] == "env" and "the action failed" in rs[0]["error"]["message"]


# ------------------------------------------------------------ round two: whole-run deadline, typed errors, usage, keys
def test_legato_run_timeout_covers_opening_the_desktop(server, monkeypatch):
    import legato.agent
    server["script"], server["delay"] = [CLICK] * 20, 0.2
    seen = {}

    def slow_open(desktop, **kw):                 # a desktop that takes 0.6 s of the 1 s to open
        seen.update(kw)
        time.sleep(0.6)
        return fake_env([]), (lambda: None), "slow"
    monkeypatch.setattr(legato.agent, "open_desktop", slow_open)
    t = time.perf_counter()
    res = legato.run("t", "docker", model="m", base_url=server["url"], api_key="", timeout_s=1.0)
    assert res.status == "timeout" and res.error == "the run's time limit (1 s) ran out"
    assert 0.9 < seen["timeout_s"] <= 1.0 and time.perf_counter() - t < 1.6

    def too_slow(desktop, **kw):
        time.sleep(kw["timeout_s"])
        raise legato.DesktopStartError("the docker desktop did not start within 0.3 s")
    monkeypatch.setattr(legato.agent, "open_desktop", too_slow)
    res = legato.run("t", "docker", model="m", base_url=server["url"], api_key="", timeout_s=0.3)
    assert res.status == "timeout" and "ran out while the desktop was opening" in res.error


def test_background_launch_is_cut_at_the_time_left(monkeypatch):
    import legato.agent
    import legato.background as bg
    got = {}
    monkeypatch.setattr(bg.sys, "platform", "darwin")
    monkeypatch.setattr(legato.agent.sys, "platform", "darwin")
    monkeypatch.setattr(bg, "background", lambda name, confirm=None, launch_s=30.0: got.update(launch_s=launch_s))
    legato.agent.open_desktop("background", app="Notes", timeout_s=4.0)
    assert got["launch_s"] == 4.0
    legato.agent.open_desktop("background", app="Notes")
    assert got["launch_s"] == 30.0


def test_docker_start_is_cut_at_its_timeout(monkeypatch):
    import subprocess
    from legato import providers
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/docker")

    def hangs(args, timeout=None, **kw):          # a docker daemon that never answers
        time.sleep(min(timeout or 5.0, 5.0))
        raise subprocess.TimeoutExpired(args, timeout)
    monkeypatch.setattr(providers.subprocess, "run", hangs)
    t = time.perf_counter()
    with pytest.raises(legato.DesktopStartError, match="did not start within 0.3 s"):
        providers.start(providers.docker, 800, 600, timeout_s=0.3)
    assert time.perf_counter() - t < 1.0
    calls = []
    assert providers.start(lambda w, h: calls.append((w, h)) or (None, "env"), 8, 6, timeout_s=1.0) == (None, "env")
    assert calls == [(8, 6)]                       # your own function: called as it always was


def test_desktop_errors_are_typed_and_still_runtime_errors(monkeypatch):
    import legato.background as bg
    from legato import providers
    assert issubclass(legato.DesktopStartError, legato.DesktopError) and issubclass(legato.DesktopError, RuntimeError)
    monkeypatch.setattr(bg.sys, "platform", "linux")
    with pytest.raises(legato.DesktopStartError, match="macOS only"):
        bg.BackgroundEnv("TextEdit")

    class NoSuchApp:
        returncode, stderr = 1, "Unable to find application named 'NoSuchAppXyz'"
    app = bg.MacApp.__new__(bg.MacApp)
    app.name, app.flag = "NoSuchAppXyz", "-a"
    monkeypatch.setattr(bg.subprocess, "run", lambda *a, **k: NoSuchApp())
    with pytest.raises(legato.DesktopStartError, match="could not be opened"):
        app._launch(5)
    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(legato.DesktopStartError, match="needs Docker"):
        providers.ensure_image("img")


def test_a_desktop_name_that_is_none_is_worded_for_python(monkeypatch):
    with pytest.raises(ValueError) as e:
        legato.run("t", "nonsense", model="m", base_url="http://x/v1", api_key="")
    assert str(e.value).startswith("desktop='nonsense' is not a desktop: use \"background\", \"screen\"")
    assert "--sandbox" not in str(e.value)


def test_usage_is_marked_incomplete_when_a_call_is_cut_off(server):
    server["script"] = [dict(CLICK, delay=3.0)]
    res = legato.Harness("m", server["url"]).run("t", fake_env([]), timeout_s=0.5)
    assert res.status == "timeout" and res.usage["complete"] is False
    server["script"] = [dict(CLICK, delay=1.0), CLICK, {"tool_calls": [tc({"action": "done"})]}]
    res = legato.Harness("m", server["url"], step_timeout_s=0.3, retries=1).run("t", fake_env([]))
    assert res.done and res.usage["complete"] is False and res.usage["prompt"] == 20   # the cut attempt is not counted
    server["script"] = [CLICK, {"tool_calls": [tc({"action": "done"})]}]
    assert legato.Harness("m", server["url"]).run("t", fake_env([])).usage["complete"] is True


def test_a_refused_key_says_which_key_and_how_to_fix_it(server, monkeypatch, own_config):
    from legato import config
    refused = {"http": 401, "body": {"error": {"message": "User not found.", "code": 401}, "user_id": "user_x"}}
    server["script"] = [refused]
    res = legato.Harness("m", server["url"], "sk-secret-1").run("t", fake_env([]))
    assert res.error == (f"HTTP 401: User not found. The api_key passed to legato.Harness was refused by "
                         f"{server['url']}: check the key you passed.")
    config.save(config.Config(model="m", base_url=server["url"], api_key_env="MY_KEY"))
    monkeypatch.setenv("MY_KEY", "sk-secret-2")
    server["script"] = [refused]
    res = legato.Harness().run("t", fake_env([]))
    assert "The key in $MY_KEY was refused" in res.error and "export a working one as MY_KEY" in res.error
    server["script"] = [refused]
    res = legato.run("t", fake_env([]))
    assert "The key in $MY_KEY was refused" in res.error and "sk-secret" not in res.error
    server["script"] = [{"http": 403, "body": {"error": {"message": "Forbidden"}}}]
    res = legato.Harness("m", server["url"], "").run("t", fake_env([]))
    assert res.error.startswith("HTTP 403: Forbidden. No API key was sent to")


def test_unattended_legato_run_refuses_risky_actions_unless_told(monkeypatch):
    import sys
    import legato.agent
    from legato import safety
    got = []

    def opened(desktop, **kw):
        got.append(kw["confirm"])
        raise legato.DesktopStartError("stop here")
    monkeypatch.setattr(legato.agent, "open_desktop", opened)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.delenv("LEGATO_CONFIRM", raising=False)
    run = lambda **kw: legato.run("t", "screen", model="m", base_url="http://x/v1", api_key="", **kw)  # noqa: E731
    run()
    run(confirm="auto")
    run(confirm=None)
    run(confirm=safety.ask)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    run()
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setenv("LEGATO_CONFIRM", "off")
    run()
    assert got == [safety.deny, safety.deny, None, safety.ask, safety.ask, None]
    assert safety.default_confirm() is None                 # legato.local's "auto" is unchanged: nothing held back


def test_a_done_without_the_answer_gets_one_more_turn(server):
    """GPT-6.1 Terra ended 20 of 20 Safari questions with done and no text, a plan's done often before the page had
    loaded. With ask_answer, that done gets one more turn with the screen and a note; the
    answer then given is the result's. A second empty done ends the run done, and a run without ask_answer is as before."""
    from legato.agent import asks_for_information
    server["script"] = [{"tool_calls": [tc({"action": "left_click", "coordinate": [500, 500]}), tc({"action": "done"})]},
                        {"tool_calls": [tc({"action": "done", "text": "8,849 m"})]}]
    res = legato.Harness("m", server["url"], ask_answer=True).run("how tall is Everest?", fake_env([]))
    assert (res.status, res.done, res.answer, res.steps) == ("done", True, "8,849 m", 2)
    assert "You called done without the answer" in json.dumps(server["bodies"][1])
    server["bodies"].clear()
    server["script"] = [{"tool_calls": [tc({"action": "done"})]}, {"tool_calls": [tc({"action": "done"})]}]
    res = legato.Harness("m", server["url"], ask_answer=True).run("what is open?", fake_env([]))
    assert (res.status, res.done, res.answer, res.steps) == ("done", True, "", 2)
    server["bodies"].clear()
    server["script"] = [{"tool_calls": [tc({"action": "done"})]}]
    res = legato.Harness("m", server["url"], ask_answer=True).run("what is open?", fake_env([]), max_steps=1)
    assert (res.status, res.done, res.steps) == ("done", True, 1)          # no turn left to ask in
    server["script"] = [{"tool_calls": [tc({"action": "done"})]}]
    res = legato.Harness("m", server["url"]).run("type hello", fake_env([]))
    assert (res.status, res.done, res.steps) == ("done", True, 1)
    server["script"] = [{"tool_calls": [tc({"action": "done"})]}, CLICK, CLICK]
    res = legato.Harness("m", server["url"], ask_answer=True).run("what is open?", fake_env([]), max_steps=3)
    assert (res.status, res.done) == ("max_steps", False)    # it went on acting: the first done no longer counts
    assert asks_for_information("Tell me the top headline") and asks_for_information("How many tabs are open?")
    assert not asks_for_information("Open TextEdit and type hello") and not asks_for_information("Reload the page")


# ------------------------------------------------------------ a model without tool calls; checking a model
NO_TOOLS = {"http": 404, "body": {"error": {"message": 'No endpoints found that support tool use. Try disabling "computer".'}}}
NO_IMAGES = {"http": 404, "body": {"error": {"message": "No endpoints found that support image input"}}}


def test_a_model_without_tool_calls_is_asked_for_json_instead(server):
    from legato.harness import no_tool_calls
    assert no_tool_calls('HTTP 404: No endpoints found that support tool use. Try disabling "computer".')
    assert no_tool_calls('HTTP 400: "auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser to be set')
    assert no_tool_calls("HTTP 400: registry.ollama.ai/library/llava:latest does not support tools")
    assert not no_tool_calls("HTTP 401: this key does not support tools") and not no_tool_calls("HTTP 500: overloaded")
    server["script"] = [NO_TOOLS, {"content": '{"action": "left_click", "coordinate": [10, 20]}'},
                        {"content": '{"action": "done"}'}]
    log = []
    h = legato.Harness("m", server["url"])
    res = h.run("t", fake_env(log))
    assert res.done and [a.type for a in log] == ["click"] and h.native_tools is False
    first, second, third = server["bodies"]
    assert "tools" in first and "tools" not in second and "tools" not in third        # refused once, never sent again
    assert "JSON object" in second["messages"][0]["content"] and "JSON object" not in first["messages"][0]["content"]


def test_check_model_tries_what_a_run_needs(server):
    from legato import config
    server["script"] = [{"tool_calls": [tc({"action": "left_click", "coordinate": [751, 248]})]}]
    r = config.check_model("m", server["url"])
    assert (r["ok"], r["images"], r["tool_calls"], r["coords"], r["coords_default"]) == (True, True, "native", "norm1000", "norm1000")
    body = server["bodies"][0]                      # one call, with the screenshot and the tool, as a run sends them
    assert len(server["bodies"]) == 1 and body["tools"][0]["function"]["name"] == "computer"
    assert any(p.get("type") == "image_url" for p in body["messages"][1]["content"])
    assert any("clicks in the 0-1000 grid" in line for line in config.check_lines(r))

    server["script"] = [NO_TOOLS, {"content": '{"action": "left_click", "coordinate": [955, 205]}'}]
    r = config.check_model("m", server["url"])
    assert (r["ok"], r["tool_calls"], r["coords"]) == (True, "json", "pixels")
    lines = "\n".join(config.check_lines(r))
    assert "it takes no tool calls here" in lines and "--coords pixels" in lines

    server["script"] = [{"content": "I would click the red square."}, {"content": '{"action": "left_click", "coordinate": [750, 250]}'}]
    assert config.check_model("m", server["url"])["tool_calls"] == "json"       # words first, JSON when asked for it

    server["script"] = [{"tool_calls": [tc({"action": "left_click", "coordinate": [100, 700]})]}]
    r = config.check_model("m", server["url"])
    assert r["ok"] and r["coords"] is None and r["images"] is None and "missed the test square" in r["fix"]


def test_check_model_says_why_a_model_cannot_be_used(server, monkeypatch):
    from legato import config
    server["script"] = [NO_IMAGES, {"content": "ok"}]
    r = config.check_model("m", server["url"])
    assert (r["ok"], r["answers"], r["images"]) == (False, True, False)
    assert r["fix"] == "this model does not accept images; Legato needs a vision model"
    monkeypatch.setitem(config.VISION_MODELS, "openrouter", ("a/one", "b/two"))
    monkeypatch.setitem(config.PROVIDERS, "openrouter", config.PROVIDERS["openrouter"]._replace(base_url=server["url"]))
    server["script"] = [NO_IMAGES, {"content": "ok"}]
    assert config.check_model("m", server["url"])["fix"].endswith("for example a/one or b/two")

    server["script"] = [{"http": 401, "body": {"error": {"message": "No auth credentials found"}}}]
    n = len(server["bodies"])
    r = config.check_model("m", server["url"])
    assert (r["ok"], r["answers"], r["fix"]) == (False, False, "check the key") and len(server["bodies"]) == n + 1
    assert config.check_lines(r)[0].startswith("no  answers: HTTP 401")

    server["script"] = [{"content": "Sorry, I cannot help with that."}, {"content": "No."}]
    r = config.check_model("m", server["url"])
    assert r["ok"] is False and r["answers"] and "no action Legato can run" in r["error"]


def test_the_report_is_ascii_where_stdout_is_not_utf8(monkeypatch):
    res = legato.harness.RunResult("t", status="done", wall_s=1.0)
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(io.BytesIO(), encoding="cp1252"))
    line = res.report()
    assert line.isascii() and "model 0.0 s (0%) - actions" in line
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(io.BytesIO(), encoding="utf-8"))
    assert " · actions" in res.report()


def test_a_question_gets_its_answer_by_default_and_other_tasks_are_as_before(server):
    """Harness.run with nothing set: a task whose words ask for information is sent with the line asking for the
    answer in done's text, and an empty done gets the one more turn. A task that asks nothing gets neither, and
    ask_answer=False turns both off. (GPT-6.1 Terra and Claude Sonnet 5.5 returned done with answer '' for "Look at
    the taskbar clock and tell me the date it shows" through Harness.run.)"""
    from legato.harness import ANSWER_NOTE
    server["script"] = [{"tool_calls": [tc({"action": "done"})]}, {"tool_calls": [tc({"action": "done", "text": "24 Nov"})]}]
    res = legato.Harness("m", server["url"]).run("Look at the clock and tell me the date", fake_env([]))
    assert (res.status, res.answer, res.steps, res.task) == ("done", "24 Nov", 2, "Look at the clock and tell me the date")
    assert ANSWER_NOTE.strip() in json.dumps(server["bodies"][0]).replace("\\n", "\n")
    assert "You called done without the answer" in json.dumps(server["bodies"][1])
    server["bodies"].clear()
    server["script"] = [{"tool_calls": [tc({"action": "done"})]}]
    res = legato.Harness("m", server["url"]).run("Open Notepad and type hello", fake_env([]))
    assert (res.status, res.steps) == ("done", 1) and "asks for information" not in json.dumps(server["bodies"][0])
    server["bodies"].clear()
    server["script"] = [{"tool_calls": [tc({"action": "done"})]}]
    res = legato.Harness("m", server["url"], ask_answer=False).run("what is open?", fake_env([]))
    assert (res.status, res.steps) == ("done", 1) and "asks for information" not in json.dumps(server["bodies"][0])
    server["script"] = [{"tool_calls": [tc({"action": "done"})]}, {"tool_calls": [tc({"action": "done", "text": "x"})]}]
    res = legato.Harness("m", server["url"]).run("what is open?" + ANSWER_NOTE, fake_env([]))     # as legato.run sends it
    assert res.answer == "x" and json.dumps(server["bodies"][-1]).count("finish with done") == 1


def test_max_cost_usd_ends_the_run_between_turns(server):
    """The run ends with status max_cost once the cost the provider reports reaches the limit; a provider that
    reports none never reaches it."""
    server["script"] = [dict(CLICK, usage={"cost": 0.02}) for _ in range(5)]
    res = legato.Harness("m", server["url"]).run("click on", fake_env([]), max_cost_usd=0.05)
    assert (res.status, res.done, res.steps) == ("max_cost", False, 3) and len(res.actions) == 3
    assert res.error == "the run's cost limit ($0.05) was reached: $0.0600 spent" and res.error in res.report()
    server["script"] = [CLICK, CLICK]
    res = legato.Harness("m", server["url"]).run("click on", fake_env([]), max_cost_usd=0.05, max_steps=3)
    assert (res.status, res.steps) == ("done", 3)


def test_on_step_gets_the_running_usage_and_every_run_has_a_trace_and_can_keep_its_screens(server, tmp_path):
    server["script"] = [dict(CLICK, usage={"cost": 0.01}),
                        {"content": "all set", "tool_calls": [tc({"action": "done", "text": "ok"})], "usage": {"cost": 0.01}}]
    seen = []
    res = legato.Harness("m", server["url"]).run("click it", fake_env([]), record_dir=tmp_path / "shots",
                                                 on_step=lambda turn, actions, timing: seen.append(
                                                     (turn, [a.type for a in actions], timing["usage"]["prompt"],
                                                      round(timing["usage"]["cost_usd"], 2))))
    assert seen == [(0, ["click"], 10, 0.01), (1, [], 20, 0.02)]
    assert [(e["turn"], e["actions"], e["done"]) for e in res.trace] == [(0, ["click (500, 250)"], False), (1, [], True)]
    assert "all set" in res.trace[1]["say"] and res.trace[0]["s"] >= 0
    names = sorted(p.name for p in (tmp_path / "shots").iterdir())
    assert names == ["start.png", "turn-001.png", "turn-002.png"]
    assert [Path(e["screenshot"]).name for e in res.trace] == names[1:]
    assert all(isinstance(v, (str, int, float, bool, list)) for e in res.trace for v in e.values())    # paths, not images
    res = legato.Harness("m", server["url"]).run("click it", fake_env([]))
    assert len(res.trace) == 1 and "screenshot" not in res.trace[0]
