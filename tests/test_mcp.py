"""The MCP server's tools on a fake desktop: open, act (returns the settled screen), screenshot, close."""
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
        assert names == {"desktop_open", "desktop_act", "desktop_screenshot", "desktop_run_task", "desktop_close"}
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
