"""The MCP server's tools on a fake desktop: open, act (returns the settled screen), screenshot, shell, close."""
import asyncio
import struct
import sys
import zlib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
pytest.importorskip("mcp")
import legato  # noqa: E402
from legato.mcp_server import build_server  # noqa: E402


def png(w=64, h=40):
    raw = b"".join(b"\x00" + b"\xff\xff\xff" * w for _ in range(h))
    ch = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)  # noqa: E731
    return (b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + ch(b"IDAT", zlib.compress(raw)) + ch(b"IEND", b""))


def kinds(result):
    return [c.type for c in result.content]


def test_open_act_screenshot_close():
    done, killed = [], []

    class Box:
        def kill(self):
            killed.append(True)

    def factory(res):
        return Box(), legato.attach(screenshot=lambda: png(*res), execute=lambda a: done.append(a), fixed_wait_s=0.0)

    srv = build_server(factory)

    async def go():
        names = {t.name for t in await srv.list_tools()}
        assert names == {"desktop_open", "desktop_act", "desktop_screenshot", "desktop_read", "desktop_wait",
                         "desktop_shell", "desktop_run_task", "desktop_close"}
        r = await srv.call_tool("desktop_open", {"width": 64, "height": 40})
        assert kinds(r) == ["text", "image"]
        did = r.content[0].text.split()[1]
        r = await srv.call_tool("desktop_act", {"desktop_id": did, "action": "left_click", "x": 10, "y": 20})
        assert kinds(r) == ["text", "image"] and "click (10, 20)" in r.content[0].text
        r = await srv.call_tool("desktop_act", {"desktop_id": did, "action": "key", "keys": "ctrl+s"})
        assert done[-1].keys == "ctrl+s"
        with pytest.raises(Exception, match="unknown action"):
            await srv.call_tool("desktop_act", {"desktop_id": did, "action": "fly"})
        assert kinds(await srv.call_tool("desktop_screenshot", {"desktop_id": did})) == ["image"]
        r = await srv.call_tool("desktop_close", {"desktop_id": did})
        assert "closed" in r.content[0].text and killed == [True]
    asyncio.run(go())
    assert [(a.type, a.x, a.y) for a in done[:1]] == [("click", 10, 20)]


def test_record(tmp_path):
    import json

    def factory(res):
        return None, legato.attach(screenshot=lambda: png(*res), execute=lambda a: None, fixed_wait_s=0.0)

    srv = build_server(factory, record=str(tmp_path))

    async def go():
        r = await srv.call_tool("desktop_open", {"width": 64, "height": 40})
        did = r.content[0].text.split()[1]
        await srv.call_tool("desktop_act", {"desktop_id": did, "action": "click", "x": 1, "y": 2})
        await srv.call_tool("desktop_close", {"desktop_id": did})
        return did
    did = asyncio.run(go())
    log = [json.loads(line) for line in (tmp_path / did / "log.jsonl").read_text().splitlines()]
    assert [e["event"] for e in log] == ["open", "click", "close"]
    assert (tmp_path / did / log[1]["screenshot"]).read_bytes().startswith(b"\x89PNG")


def test_shell(tmp_path):
    import json

    sent = []

    def with_shell(res):
        env = legato.attach(screenshot=lambda: png(*res), execute=lambda a: None, fixed_wait_s=0.0)
        env.shell = lambda cmd, timeout_s: sent.append((cmd, timeout_s)) or "hi\n[exit 0]\n"
        return None, env

    def no_shell(res):
        return None, legato.attach(screenshot=lambda: png(*res), execute=lambda a: None, fixed_wait_s=0.0)

    async def go():
        srv = build_server(with_shell, record=str(tmp_path))
        did = (await srv.call_tool("desktop_open", {"width": 64, "height": 40})).content[0].text.split()[1]
        r = await srv.call_tool("desktop_shell", {"desktop_id": did, "command": "echo hi", "timeout_s": 9999})
        assert r.content[0].text == "hi\n[exit 0]\n"
        assert sent == [("echo hi", 300.0)]
        log = [json.loads(line) for line in (tmp_path / did / "log.jsonl").read_text().splitlines()]
        assert log[-1]["event"] == "shell" and log[-1]["command"] == "echo hi"
        srv = build_server(no_shell)
        did = (await srv.call_tool("desktop_open", {"width": 64, "height": 40})).content[0].text.split()[1]
        with pytest.raises(Exception, match="no shell"):
            await srv.call_tool("desktop_shell", {"desktop_id": did, "command": "ls"})
    asyncio.run(go())


def test_launch_command():
    from legato.mcp_server import launch_command
    assert launch_command("darwin", app="TextEdit") == "open -a TextEdit"
    assert launch_command("darwin", url="https://a.b/c d") == "open 'https://a.b/c d'"
    assert launch_command("linux", app="xterm") == "nohup xterm >/dev/null 2>&1 &"
    assert launch_command("linux", url="http://h:3000").startswith("(xdg-open http://h:3000 || firefox-esr")
    assert launch_command("win32", url="https://a.b") == "Start-Process 'https://a.b'"
    assert launch_command("win32", app="notepad") == "Start-Process notepad"


def test_open_with_app_waits_for_its_window():
    frames = {"n": 0}
    sent = []

    def screen():                       # blank until the app is started, then two frames while it draws
        if sent:
            frames["n"] += 1
        return png(64, 40) if not sent else png(64, 41) if frames["n"] < 3 else png(64, 42)

    def factory(res):
        env = legato.attach(screenshot=screen, execute=lambda a: None, fixed_wait_s=0.0)
        env.shell = lambda cmd, timeout_s: sent.append(cmd) or "[exit 0]\n"
        return None, env

    def failing(res):
        env = legato.attach(screenshot=lambda: png(64, 40), execute=lambda a: None, fixed_wait_s=0.0)
        env.shell = lambda cmd, timeout_s: "nohup: xcalc: not found\n[exit 127]\n"
        return None, env

    async def go():
        r = await build_server(factory).call_tool("desktop_open", {"width": 64, "height": 40, "app": "xterm"})
        assert sent == ["nohup xterm >/dev/null 2>&1 &"]
        assert "started xterm" in r.content[0].text and r.content[0].text.split()[2] == "(64x42)"
        r = await build_server(failing).call_tool("desktop_open", {"width": 64, "height": 40, "app": "xcalc"})
        assert "NOT started" in r.content[0].text and "not found" in r.content[0].text
        r = await build_server(lambda res: (None, legato.attach(screenshot=lambda: png(64, 40),
                                                                execute=lambda a: None))).call_tool(
            "desktop_open", {"width": 64, "height": 40, "url": "https://a.b"})
        assert "no shell" in r.content[0].text
    asyncio.run(go())


def test_screenshots_are_jpeg_unless_png_is_asked():
    import base64

    from legato.mcp_server import to_image

    def factory(res):
        return None, legato.attach(screenshot=lambda: png(*res), execute=lambda a: None, fixed_wait_s=0.0)

    async def first_image(srv):
        r = await srv.call_tool("desktop_open", {"width": 64, "height": 40})
        return r.content[1]

    def mime(c):                                        # mcp 2 says mime_type, mcp 1 mimeType
        return getattr(c, "mime_type", None) or c.mimeType

    jpg = asyncio.run(first_image(build_server(factory)))
    assert mime(jpg) == "image/jpeg" and base64.b64decode(jpg.data).startswith(b"\xff\xd8")
    assert mime(asyncio.run(first_image(build_server(factory, image_format="png")))) == "image/png"
    assert to_image(b"not an image")._mime_type == "image/png"   # sent as it came
    with pytest.raises(ValueError):
        build_server(factory, image_format="gif")


def test_fixed_wait_flag(monkeypatch):
    from legato import mcp_server, providers
    built = {}
    env = legato.attach(screenshot=lambda: png(), execute=lambda a: None)
    monkeypatch.setattr(providers, "load", lambda spec: lambda w, h: (None, env))
    monkeypatch.setattr(providers, "warm", lambda spec: None)
    monkeypatch.setattr(mcp_server, "build_server", lambda factory, **kw: built.update(factory=factory, **kw) or
                        type("S", (), {"run": lambda self: None})())
    mcp_server.main(["--sandbox", "local", "--fixed-wait", "2", "--image-format", "png"])
    assert built["factory"]((64, 40))[1].fixed_wait_s == 2.0 and built["image_format"] == "png"
    local_mod = __import__("importlib").import_module("legato.local")   # legato.local is also a function
    monkeypatch.setattr(local_mod, "local", lambda **kw: built.update(local_kw=kw) or env)
    mcp_server.main(["--local", "--wait-scope", "app"])
    built["factory"]((64, 40))
    assert built["local_kw"] == {"wait_scope": "app"}


def test_a_desktop_failure_reaches_the_client_with_its_reason():
    """The MCP library shows only "Error executing tool" for an unexpected exception: the reason must come through."""
    shots = iter([png()])

    def screenshot():
        try:
            return next(shots)
        except StopIteration:
            raise RuntimeError("the window could not be captured") from None

    srv = build_server(lambda res: (None, legato.attach(screenshot=screenshot, execute=lambda a: None,
                                                        fixed_wait_s=0.0)))

    async def go():
        r = await srv.call_tool("desktop_open", {"width": 64, "height": 40})
        did = r.content[0].text.split()[1]
        with pytest.raises(Exception, match="desktop_screenshot failed: the window could not be captured"):
            await srv.call_tool("desktop_screenshot", {"desktop_id": did})
    asyncio.run(go())


def a_model(monkeypatch):
    """A model set up for desktop_run_task: one on this machine, which needs no key."""
    monkeypatch.setenv("LEGATO_MODEL", "m")
    monkeypatch.setenv("LEGATO_BASE_URL", "http://localhost:1/v1")


def test_run_task_reports_its_status_and_a_desktop_error(monkeypatch):
    """desktop_run_task leads with the run's status; a desktop that fails mid-run comes back as status error, not as
    an exception, even when the final screenshot cannot be taken either."""
    a_model(monkeypatch)
    import json
    import legato.agent
    shots, sent = {"n": 0, "fail_after": 99}, []

    def screenshot():
        shots["n"] += 1
        if shots["n"] > shots["fail_after"]:
            raise RuntimeError("the window could not be captured")
        return png()

    def agent_for(env, **kw):
        h = legato.Harness("m", "http://x/v1", "", coords="pixels", model_size=None)

        def chat(msgs, **k):
            sent.append(msgs)
            call = {"action": "left_click", "coordinate": [5, 5]} if len(sent) % 2 else {"action": "done"}
            return {"content": "", "tool_calls": [{"type": "function", "function": {
                "name": "computer", "arguments": json.dumps(call)}}], "usage": {"prompt": 7}, "s": 0.01}
        h.client.chat = chat
        return h
    monkeypatch.setattr(legato.agent, "agent_for", agent_for)
    srv = build_server(lambda res: (None, legato.attach(screenshot=screenshot, execute=lambda a: None,
                                                        fixed_wait_s=0.0)))

    async def go():
        r = await srv.call_tool("desktop_open", {"width": 64, "height": 40})
        did = r.content[0].text.split()[1]
        r = await srv.call_tool("desktop_run_task", {"desktop_id": did, "task": "click it"})
        assert kinds(r) == ["text", "image"] and r.content[0].text.startswith("status: done\n")
        assert r.structured_content["status"] == "done" and r.structured_content["steps"] == 2 and not r.is_error
        assert set(r.structured_content) == {"status", "answer", "seconds", "steps", "error", "cost_usd"}
        shots["fail_after"] = shots["n"] + 1               # the first screenshot works, the one after the click fails
        r = await srv.call_tool("desktop_run_task", {"desktop_id": did, "task": "click it"})
        assert kinds(r) == ["text"] and r.content[0].text.startswith(
            "status: error\nthe desktop failed: the window could not be captured")
        assert r.is_error and r.structured_content["status"] == "error" and "captured" in r.structured_content["error"]
    asyncio.run(go())


def test_run_task_passes_its_deadline_and_says_status_in_its_description(monkeypatch):
    a_model(monkeypatch)
    import legato.agent
    from legato.harness import RunResult
    got = []

    class Agent:
        model = "m"

        def run(self, task, env, **kw):
            got.append(kw)
            return RunResult(task, status="timeout", error="the run's deadline passed")
    monkeypatch.setattr(legato.agent, "agent_for", lambda env, **kw: Agent())
    srv = build_server(lambda res: (None, legato.attach(screenshot=lambda: png(*res), execute=lambda a: None,
                                                        fixed_wait_s=0.0)))

    async def go():
        tool = next(t for t in await srv.list_tools() if t.name == "desktop_run_task")
        assert "status" in tool.description and "timeout_s" in tool.description
        did = (await srv.call_tool("desktop_open", {"width": 64, "height": 40})).content[0].text.split()[1]
        r = await srv.call_tool("desktop_run_task", {"desktop_id": did, "task": "t", "timeout_s": 90})
        assert r.content[0].text.startswith("status: timeout\n") and r.is_error            # a client can branch on it
        assert r.structured_content["status"] == "timeout"
        assert tool.output_schema["required"] == ["status", "answer", "seconds", "steps", "error", "cost_usd"]
        await srv.call_tool("desktop_run_task", {"desktop_id": did, "task": "t"})
        await srv.call_tool("desktop_run_task", {"desktop_id": did, "task": "t", "timeout_s": 0})
    asyncio.run(go())
    assert got[0]["timeout_s"] == 90.0 and got[1]["timeout_s"] is None and got[2]["timeout_s"] == 0


def test_run_task_under_record_keeps_each_step(monkeypatch, tmp_path):
    a_model(monkeypatch)
    import json
    import legato.agent
    sent = []

    def agent_for(env, **kw):
        h = legato.Harness("m", "http://x/v1", "", coords="pixels", model_size=None)

        def chat(msgs, **k):
            sent.append(msgs)
            call = {"action": "left_click", "coordinate": [5, 5]} if len(sent) < 3 else {"action": "done"}
            return {"content": "", "tool_calls": [{"type": "function", "function": {
                "name": "computer", "arguments": json.dumps(call)}}], "usage": {"prompt": 7}, "s": 0.01}
        h.client.chat = chat
        return h
    monkeypatch.setattr(legato.agent, "agent_for", agent_for)
    srv = build_server(lambda res: (None, legato.attach(screenshot=lambda: png(*res), execute=lambda a: None,
                                                        fixed_wait_s=0.0)), record=str(tmp_path))

    async def go():
        did = (await srv.call_tool("desktop_open", {"width": 64, "height": 40})).content[0].text.split()[1]
        await srv.call_tool("desktop_run_task", {"desktop_id": did, "task": "click it twice"})
        return did
    did = asyncio.run(go())
    log = [json.loads(line) for line in (tmp_path / did / "log.jsonl").read_text().splitlines()]
    steps = [e for e in log if e["event"] == "run_step"]
    assert [e["event"] for e in log] == ["open", "run_step", "run_step", "run_step", "run_task"]
    assert steps[0]["actions"] == ["click (5, 5)"] and all(e["screenshot"] for e in steps[:2])
    assert (tmp_path / did / steps[0]["screenshot"]).read_bytes().startswith(b"\x89PNG")


def test_risky_actions_on_this_computer_are_refused_unless_the_server_was_started_with_yes(monkeypatch):
    """An MCP server's stdin is its client's pipe, so "ask in the terminal" asked nobody and ran Cmd+Q, a typed rm,
    and the shell and AppleScript steps of desktop_run_task. They are refused now, and the client is told how to
    allow them; `legato mcp --yes` runs them as before."""
    import asyncio

    from legato import mcp_server, providers, safety
    from legato.local import LocalEnv
    built, real_build = {}, mcp_server.build_server

    class Screen(LocalEnv):
        def __init__(self):
            self.confirm, self.mac, self.mac_cmd = "unset", False, False
    monkeypatch.setattr(providers, "load", lambda spec: lambda w, h: (None, Screen()))
    monkeypatch.setattr(providers, "warm", lambda spec: None)
    monkeypatch.setattr(mcp_server, "build_server", lambda factory, **kw: built.update(factory=factory, **kw) or
                        type("S", (), {"run": lambda self: None})())
    monkeypatch.setattr(safety.sys.stdin, "isatty", lambda: False)
    monkeypatch.delenv("LEGATO_CONFIRM", raising=False)
    mcp_server.main(["--sandbox", "local"])
    assert built["factory"]((64, 40))[1].confirm is safety.deny
    mcp_server.main(["--sandbox", "local", "--yes"])
    assert built["factory"]((64, 40))[1].confirm is None

    def refused(a):
        raise safety.Denied("key cmd+q")
    env = legato.attach(screenshot=lambda: png(), execute=refused, fixed_wait_s=0.0)
    env.confirm = safety.deny
    srv = real_build(lambda res: (None, env))

    async def go():
        r = await srv.call_tool("desktop_open", {"width": 64, "height": 40})
        did = r.content[0].text.split("desktop_id: ")[1].split()[0]
        with pytest.raises(Exception, match="refused here.*legato mcp --yes"):
            await srv.call_tool("desktop_act", {"desktop_id": did, "action": "key", "keys": "cmd+q"})
    asyncio.run(go())


def test_open_gives_its_id_and_real_size_as_structured_content_and_the_server_its_version():
    def factory(res):                                   # a real screen: its size is its own
        env = legato.attach(screenshot=lambda: png(96, 72), execute=lambda a: None, fixed_wait_s=0.0)
        env.shell = lambda cmd, timeout_s: "start : not found\n[exit 1]"
        return None, env

    srv = build_server(factory)
    low = getattr(srv, "_lowlevel_server", None) or srv._mcp_server
    assert low.version == legato.__version__

    async def go():
        tool = next(t for t in await srv.list_tools() if t.name == "desktop_open")
        assert "Windows" in tool.description
        r = await srv.call_tool("desktop_open", {})
        assert r.content[0].text.split()[2] == "(96x72)" and "asked for" not in r.content[0].text
        r2 = await srv.call_tool("desktop_open", {"width": 1280, "height": 800})
        assert "its size is 96x72, not the 1280x800 asked for" in r2.content[0].text
        r3 = await srv.call_tool("desktop_open", {"app": "nope"})
        return r, r3
    r, r3 = asyncio.run(go())
    out = getattr(r, "structuredContent", None) or getattr(r, "structured_content", None)
    if out is None:                                     # mcp 1: text and image only
        return
    did = r.content[0].text.split()[1]
    assert out == {"desktop_id": did, "width": 96, "height": 72, "started": None, "start_error": None}
    bad = getattr(r3, "structuredContent", None) or getattr(r3, "structured_content", None)
    assert (r3.isError if hasattr(r3, "isError") else r3.is_error) and "not found" in bad["start_error"]
    assert bad["desktop_id"] in r3.content[0].text and bad["started"] is None


WINDOW = '#window\tUntitled - Notepad\n0\tEdit\tf\tText Editor\t{}\n[exit 0]'


def test_read_and_wait_on_a_windows_screen(monkeypatch):
    import legato.mcp_server as m
    texts, sent = ["", "", "Count: 2"], []
    frames = {"n": 0}

    def screen():                                       # changes once after the first look, then holds still
        frames["n"] += 1
        return png(64, 40) if frames["n"] < 4 else png(64, 41)

    def factory(res):
        env = legato.attach(screenshot=screen, execute=lambda a: None, fixed_wait_s=0.0)
        env.shell = lambda cmd, timeout_s: sent.append(cmd) or WINDOW.format(texts.pop(0) if len(texts) > 1 else texts[0])
        return None, env

    async def go(srv):
        did = (await srv.call_tool("desktop_open", {})).content[0].text.split()[1]
        with pytest.raises(Exception, match="background mode on a Mac.*Windows screen"):
            await srv.call_tool("desktop_read", {"desktop_id": did})
        monkeypatch.setattr(m, "windows_screen", lambda env: True)
        r = await srv.call_tool("desktop_read", {"desktop_id": did})
        assert r.content[0].text == 'window "Untitled - Notepad"\n- textbox "Text Editor": "" [focused]'
        assert "UIAutomationClient" in sent[0]
        r = await srv.call_tool("desktop_wait", {"desktop_id": did, "text": "count: 2"})
        assert kinds(r) == ["text"] and "is there after" in r.content[0].text and '"Count: 2"' in r.content[0].text
        with pytest.raises(Exception, match="'Saved' was not in the window after 1 s. It reads:\nwindow"):
            await srv.call_tool("desktop_wait", {"desktop_id": did, "text": "Saved", "timeout_s": 1})
        r = await srv.call_tool("desktop_wait", {"desktop_id": did})
        assert kinds(r) == ["text", "image"] and "changed and held still" in r.content[0].text
        r = await srv.call_tool("desktop_wait", {"desktop_id": did, "timeout_s": 1})
        assert "did not change" in r.content[0].text
        descs = {t.name: t.description for t in await srv.list_tools()}
        assert "Windows" in descs["desktop_read"] and "desktop_wait" in descs["desktop_shell"]
    asyncio.run(go(m.build_server(factory)))


def test_run_task_opens_its_own_desktop_reports_cost_and_names_a_missing_key(monkeypatch, tmp_path):
    import legato.agent as agent_mod
    from legato.harness import RunResult
    monkeypatch.setenv("LEGATO_CONFIG", str(tmp_path / "none.json"))
    for k in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "LEGATO_API_KEY", "LEGATO_MODEL"):
        monkeypatch.delenv(k, raising=False)
    closed = []

    class Box:
        def kill(self):
            closed.append(True)

    srv = build_server(lambda res: (Box(), legato.attach(screenshot=lambda: png(*res), execute=lambda a: None,
                                                         fixed_wait_s=0.0)))

    class Agent:
        model, client = "m", object()

        def run(self, task, env, **kw):
            return RunResult("t", done=True, steps=2, wall_s=1.0, status="done", answer="42", usage={"cost_usd": 0.0123})

    async def go():
        with pytest.raises(Exception, match="no model key in this server's environment: add it .* to your MCP "
                                            "client's `env` for this server, or run `legato setup`"):
            await srv.call_tool("desktop_run_task", {"task": "x"})
        with pytest.raises(Exception, match=r"no key for OpenRouter \(some/model\) in this server's environment: add "
                                            "OPENROUTER_API_KEY to your MCP client's `env` for this server, or run "
                                            "`legato setup`"):
            await srv.call_tool("desktop_run_task", {"task": "x", "model": "some/model"})
        assert closed == []                             # no desktop was opened to say so
        monkeypatch.setattr(agent_mod, "agent_for", lambda env, **kw: Agent())
        return await srv.call_tool("desktop_run_task", {"task": "what is 6 x 7?", "model": "some/model",
                                                        "base_url": "http://localhost:1/v1"})
    r = asyncio.run(go())
    assert "answer: 42" in r.content[0].text and closed == [True]
    out = getattr(r, "structuredContent", None) or getattr(r, "structured_content", None)
    if out is not None:
        assert out["cost_usd"] == 0.0123 and out["answer"] == "42"


def test_desktop_shell_on_this_computer_refuses_risky_commands_unless_started_with_yes():
    """desktop_shell ran rm -rf, sudo or `bash -c "..."` unasked under `legato mcp`, while the README says risky
    commands are refused when nobody can answer. Now they are, with the reason; a sandbox's shell still runs them."""
    import asyncio

    from legato import mcp_server, safety
    from legato.local import LocalEnv
    ran = []

    class Here(LocalEnv):
        def __init__(self, confirm):
            self.confirm, self.mac, self.mac_cmd = confirm, False, False
            self.last = {}

        def shell(self, cmd, timeout_s=30.0):
            ran.append(cmd)
            return "ok\n[exit 0]"

        def observe(self):
            return legato.attach(screenshot=lambda: png(), execute=lambda a: None).observe()

    for confirm, runs in ((safety.deny, False), (None, True)):
        ran.clear()
        srv = mcp_server.build_server(lambda res, c=confirm: (None, Here(c)))

        async def go():
            r = await srv.call_tool("desktop_open", {"width": 64, "height": 40})
            did = r.content[0].text.split("desktop_id: ")[1].split()[0]
            await srv.call_tool("desktop_shell", {"desktop_id": did, "command": "ls ~/Desktop"})
            try:
                await srv.call_tool("desktop_shell", {"desktop_id": did, "command": 'bash -c "rm -rf ~/Documents"'})
                return None
            except Exception as e:  # noqa: BLE001
                return str(e)
        err = asyncio.run(go())
        assert ran[0].endswith("ls ~/Desktop") or "ls ~/Desktop" in ran[0]
        if runs:
            assert err is None and len(ran) == 2
        else:
            assert "deletes files" in err and "legato mcp --yes" in err and len(ran) == 1
    assert mcp_server.launch_command("win32", url="https://a.b/’;x") == "Start-Process 'https://a.b/’’;x'"


def test_run_task_runs_a_named_model_with_its_own_providers_key(monkeypatch):
    """desktop_run_task(model="claude-sonnet-5-5") runs on Anthropic's API with the Anthropic key, whatever the saved
    provider; with no Anthropic key it says which key is missing, how to add it, and which keys the server has."""
    import legato.agent as agent_mod
    from legato import config
    from legato.harness import RunResult
    config.save(config.Config(provider="openrouter", model="qwen/qwen3.8-27b", base_url="https://openrouter.ai/api/v1",
                              api_key="sk-or-saved"))
    seen = []

    class Agent:
        model, client = "m", type("C", (), {"key_source": ""})()

        def run(self, task, env, **kw):
            return RunResult(task, done=True, steps=1, wall_s=0.5, status="done", answer="ok")

    def agent_for(env, **kw):
        seen.append(kw)
        return Agent()
    monkeypatch.setattr(agent_mod, "agent_for", agent_for)
    srv = build_server(lambda res: (None, legato.attach(screenshot=lambda: png(*res), execute=lambda a: None,
                                                        fixed_wait_s=0.0)))

    async def go():
        tool = next(t for t in await srv.list_tools() if t.name == "desktop_run_task")
        assert "claude-..." in tool.description and "provider" in tool.input_schema["properties"]
        with pytest.raises(Exception, match=r"no key for Claude \(Anthropic\) \(claude-sonnet-5-5\) in this server's "
                                            r"environment: add ANTHROPIC_API_KEY .* or run `legato setup --add-key "
                                            r"anthropic` to save one. It has a key for: openrouter"):
            await srv.call_tool("desktop_run_task", {"task": "t", "model": "claude-sonnet-5-5"})
        assert seen == []
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")
        await srv.call_tool("desktop_run_task", {"task": "t", "model": "claude-sonnet-5-5"})
        await srv.call_tool("desktop_run_task", {"task": "t", "model": "anthropic/claude-sonnet-5.5"})
        await srv.call_tool("desktop_run_task", {"task": "t"})
        with pytest.raises(Exception, match="unknown provider 'antropic'"):
            await srv.call_tool("desktop_run_task", {"task": "t", "model": "x", "provider": "antropic"})
    asyncio.run(go())
    assert [(k["model"], k["base_url"], k["api_key"]) for k in seen] == [
        ("claude-sonnet-5-5", "https://api.anthropic.com/v1", "sk-ant-env"),
        ("anthropic/claude-sonnet-5.5", "https://openrouter.ai/api/v1", "sk-or-saved"),
        ("qwen/qwen3.8-27b", "https://openrouter.ai/api/v1", "sk-or-saved")]


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_saved_mac_background_never_opens_the_foreground_on_another_os(monkeypatch, platform):
    import sys
    from legato import config, mcp_server
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(config, "load", lambda: config.Config(desktop="background"))
    monkeypatch.setattr(mcp_server, "build_server", lambda *a, **kw: pytest.fail("opened a foreground server"))
    with pytest.raises(SystemExit) as exit_info:
        mcp_server.main([])
    assert exit_info.value.code == 2


def test_desktop_read_and_wait_use_the_attached_reader_without_capturing():
    env = legato.attach(screenshot=lambda: png(), execute=lambda action: None)
    reads = []
    def read(*, max_chars):
        reads.append(max_chars)
        return 'window "Tools"\n- text "Build passed"'[:max_chars]
    env.read = read
    srv = build_server(lambda res: (None, env))

    async def go():
        did = (await srv.call_tool("desktop_open", {})).content[0].text.split()[1]
        env.observe = lambda: pytest.fail("captured the screen during a text query")
        reply = await srv.call_tool("desktop_read", {"desktop_id": did, "max_chars": 21})
        assert reply.content[0].text == 'window "Tools"\n- text'
        reply = await srv.call_tool("desktop_wait", {"desktop_id": did, "text": "passed"})
        assert 'Build passed' in reply.content[0].text
    asyncio.run(go())
    assert reads == [21, 100_000]


def test_desktop_wait_never_matches_a_reader_error():
    from legato.background import BackgroundEnv
    from legato.env import Env
    class Broken(BackgroundEnv):
        def __init__(self):
            Env.__init__(self, lambda: png())
        def read(self, max_chars):
            raise RuntimeError("Ready window is unavailable")
        def close(self):
            pass
        def describe(self):
            return "test"
    srv = build_server(lambda res: (None, Broken()))
    async def go():
        did = (await srv.call_tool("desktop_open", {})).content[0].text.split()[1]
        with pytest.raises(Exception, match="was not in the window"):
            await srv.call_tool("desktop_wait", {"desktop_id": did, "text": "Ready", "timeout_s": 1})
    asyncio.run(go())


@pytest.mark.parametrize("failure", ["capture", "record"])
def test_background_open_failure_releases_the_window_once(failure, tmp_path):
    from legato.background import BackgroundEnv
    from legato.env import Env
    closes = []
    class Window(BackgroundEnv):
        def __init__(self):
            def grab():
                if failure == "capture":
                    raise RuntimeError("window closed")
                return png()
            Env.__init__(self, grab)
        def close(self):
            closes.append(True)
        def describe(self):
            return "Test window"
    record = tmp_path / "not-a-directory"
    record.write_text("file")
    srv = build_server(background=lambda app: Window(), record=str(record))
    async def go():
        with pytest.raises(Exception):
            await srv.call_tool("desktop_open", {"app": "fixture"})
    asyncio.run(go())
    assert closes == [True]
    srv.close_left()
    assert closes == [True]
