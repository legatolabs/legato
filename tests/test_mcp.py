"""The MCP server's tools on a fake desktop: open, act (returns the settled screen), screenshot, shell, close."""
import asyncio
import struct
import sys
import time
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
        assert names == {"desktop_open", "desktop_act", "desktop_screenshot", "desktop_shell", "desktop_run_task",
                         "desktop_close"}
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
    assert launch_command("win32", url="https://a.b") == 'start "" "https://a.b"'


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
        assert "starting it failed" in r.content[0].text and "not found" in r.content[0].text
        r = await build_server(lambda res: (None, legato.attach(screenshot=lambda: png(64, 40),
                                                                execute=lambda a: None))).call_tool(
            "desktop_open", {"width": 64, "height": 40, "url": "https://a.b"})
        assert "no shell" in r.content[0].text
    asyncio.run(go())


# ------------------------------------------------------------ this Mac: the preset, the model's settings, the person
from test_harness import server  # noqa: E402, F401


def a_mac(confirm=None, ran=None):
    """legato.local() on a fake Mac: fake screen, mouse and shell, so nothing here touches this computer."""
    from legato.local import LocalEnv
    from test_local import Gui, Screen
    e = LocalEnv(fixed_wait_s=0, gui=Gui(), grabber=Screen(), confirm=confirm)
    e.mac = True
    e.shell = lambda cmd, timeout_s=30.0: (ran if ran is not None else []).append(cmd) or "hi\n[exit 0]"
    return e


KEYS = ("LEGATO_MODEL", "LEGATO_BASE_URL", "LEGATO_API_KEY", "OPENROUTER_API_KEY", "LEGATO_CONFIRM")


@pytest.fixture()
def no_keys(monkeypatch):
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def test_model_settings_come_from_the_call_then_the_server():
    from legato.mcp_server import model_settings
    assert model_settings(environ={}) == ("qwen/qwen3.8-27b", "https://openrouter.ai/api/v1", "")
    assert model_settings(environ={"OPENROUTER_API_KEY": "or"})[2] == "or"
    env = {"LEGATO_MODEL": "m1", "LEGATO_BASE_URL": "https://api.example/v1", "LEGATO_API_KEY": "lk",
           "OPENROUTER_API_KEY": "or", "MY_KEY": "mine"}
    assert model_settings(environ=env) == ("m1", "https://api.example/v1", "lk")
    assert model_settings("m2", environ=env) == ("m2", "https://api.example/v1", "lk")
    # the server's key goes only to the server's endpoint; OpenRouter's key only to OpenRouter
    assert model_settings(base_url="https://other.example/v1", environ=env)[2] == ""
    assert model_settings(base_url="https://openrouter.ai/api/v1", environ=env)[2] == "or"
    assert model_settings(base_url="https://other.example/v1", api_key_env="MY_KEY", environ=env)[2] == "mine"
    assert model_settings(api_key_env="UNSET", environ=env)[2] == ""


def test_run_task_runs_the_mac_apps_setup_on_this_mac(server, no_keys):  # noqa: F811
    no_keys.setenv("LEGATO_BASE_URL", server["url"])
    no_keys.setenv("LEGATO_API_KEY", "sk-secret-123")
    no_keys.setenv("LEGATO_MODEL", "some/model")
    srv = build_server(lambda res: (None, a_mac()))
    assert "drive with desktop_act" not in srv.instructions

    async def go():
        r = await srv.call_tool("desktop_run_task", {"task": "open Notes"})      # no desktop_open needed
        text = r.content[0].text
        assert kinds(r) == ["text", "image"] and text.startswith("desktop_id: ")
        assert "some/model with the Mac app's setup" in text and "done in" in text
        body = server["bodies"][-1]
        assert body["model"] == "some/model" and [t["function"]["name"] for t in body["tools"]] == ["plan"]
        assert "Calendar" in body["messages"][0]["content"]                    # the app's AppleScript limits
        did = text.split()[1]
        r = await srv.call_tool("desktop_run_task", {"task": "t", "desktop_id": did, "plain": True})
        assert "with the plain loop" in r.content[0].text
        assert [t["function"]["name"] for t in server["bodies"][-1]["tools"]] == ["computer"]
        return [c.text for c in r.content if c.type == "text"] + [text]
    texts = asyncio.run(go())
    assert not any("sk-secret-123" in t for t in texts)                        # the key is never shown

    srv = build_server(lambda res: (None, legato.attach(screenshot=lambda: png(*res), execute=lambda a: None,
                                                        fixed_wait_s=0.0)))
    r = asyncio.run(srv.call_tool("desktop_run_task", {"task": "t"}))
    assert "with the plain loop" in r.content[0].text                          # e2b, docker, a sandbox: unchanged
    assert [t["function"]["name"] for t in server["bodies"][-1]["tools"]] == ["computer"]


def test_run_task_without_a_key_says_where_to_set_it(no_keys):
    srv = build_server(lambda res: (None, a_mac()))
    assert "drive with desktop_act" in srv.instructions
    with pytest.raises(Exception, match="LEGATO_API_KEY"):
        asyncio.run(srv.call_tool("desktop_run_task", {"task": "t"}))


def test_risky_actions_ask_the_person_and_the_client_cannot_answer():
    asked, ran = [], []
    srv = build_server(lambda res: (None, a_mac(confirm=lambda q: asked.append(q) or False, ran=ran)))

    async def go():
        for t in await srv.list_tools():                # no argument lets a model allow anything
            schema = getattr(t, "input_schema", None) or getattr(t, "inputSchema", None) or {}
            props = set(schema.get("properties", {}))
            assert props, t.name
            assert not props & {"allow", "confirm", "yes", "approve", "approved", "force", "unsafe"}, t.name
        did = (await srv.call_tool("desktop_open", {})).content[0].text.split()[1]
        with pytest.raises(Exception, match="did not allow"):
            await srv.call_tool("desktop_act", {"desktop_id": did, "action": "key", "keys": "cmd+q"})
        r = await srv.call_tool("desktop_shell", {"desktop_id": did, "command": "rm -rf ~/Documents"})
        assert r.content[0].text.startswith("not run:") and ran == []
        r = await srv.call_tool("desktop_shell", {"desktop_id": did, "command": "ls ~/Desktop"})
        assert r.content[0].text == "hi\n[exit 0]" and ran == ["ls ~/Desktop"]
    asyncio.run(go())
    assert len(asked) == 2 and "quits the app" in asked[0] and "deletes files" in asked[1]


def test_this_computer_takes_one_call_at_a_time():
    import threading
    started, seen = threading.Event(), []

    def confirm(q):                                     # the person reads the dialog for a while, then says no
        started.set()
        time.sleep(0.5)
        seen.extend(e.gui.calls)                        # what ran while the dialog was open
        return False
    e = a_mac(confirm=confirm)
    srv = build_server(lambda res: (None, e))

    async def go():
        did = (await srv.call_tool("desktop_open", {})).content[0].text.split()[1]
        risky = asyncio.ensure_future(srv.call_tool("desktop_act", {"desktop_id": did, "action": "key",
                                                                    "keys": "cmd+q"}))
        while not started.is_set():
            await asyncio.sleep(0.01)
        click = asyncio.ensure_future(srv.call_tool("desktop_act", {"desktop_id": did, "action": "click",
                                                                    "x": 9, "y": 9}))
        await click
        with pytest.raises(Exception, match="did not allow"):
            await risky
    asyncio.run(go())
    assert seen == []                                   # the click waited: it could not press the dialog's Allow
    assert e.gui.calls == [("click", (9, 9))]


def test_local_asks_in_a_macos_dialog(no_keys):
    from legato import safety
    from legato.mcp_server import local_confirm, local_factory
    no_keys.setattr(sys, "platform", "darwin")
    assert local_confirm() is safety.dialog
    no_keys.setenv("LEGATO_CONFIRM", "off")
    assert local_confirm() is None
    no_keys.delenv("LEGATO_CONFIRM")
    no_keys.setattr(sys, "platform", "linux")
    assert local_confirm() is None                      # elsewhere: as before
    got = {}
    no_keys.setattr(sys.modules["legato.local"], "local", lambda **kw: got.update(kw) or "env")
    assert local_factory(safety.dialog)((1280, 800)) == (None, "env")
    assert got == {"confirm": safety.dialog, "wait": "cells"}


def test_drive_mode_leaves_run_task_out(no_keys):
    import legato.mcp_server as m
    srv = build_server(lambda res: (None, a_mac()), run_task=False)
    names = {t.name for t in asyncio.run(srv.list_tools())}
    assert "desktop_run_task" not in names and {"desktop_open", "desktop_act", "desktop_shell"} <= names
    assert "desktop_run_task" not in srv.instructions
    got = []

    class Srv:
        def run(self):
            pass
    no_keys.setattr(m, "build_server", lambda factory, about="", record=None, run_task=True:
                    got.append((about, run_task)) or Srv())
    no_keys.setattr(sys, "platform", "darwin")
    m.main(["--local"])                                 # no key: nothing steers to desktop_run_task
    no_keys.setenv("OPENROUTER_API_KEY", "k")
    m.main(["--local"])
    m.main(["--local", "--drive"])
    (no_key, _), (a_about, a_task), (b_about, b_task) = got
    assert "hand the whole task" not in no_key
    assert a_task and "hand the whole task to desktop_run_task" in a_about and "macOS dialog" in a_about
    assert not b_task and "desktop_run_task" not in b_about and "macOS dialog" in b_about
