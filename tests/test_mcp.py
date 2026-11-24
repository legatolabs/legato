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
        assert names == {"desktop_open", "desktop_act", "desktop_screenshot", "desktop_read", "desktop_shell",
                         "desktop_run_task", "desktop_close"}
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
