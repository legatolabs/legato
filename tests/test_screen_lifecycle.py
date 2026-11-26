"""Resource ownership when a screen is opened through Python, the CLI or MCP."""
import asyncio
import ctypes
import importlib
import sys
from types import SimpleNamespace

import pytest

import legato
from legato import agent, providers
from legato.mcp_server import build_server
from test_local import Gui, Screen

L = importlib.import_module("legato.local")


class Capture(Screen):
    def __init__(self):
        self.closes = 0

    def close(self):
        self.closes += 1


def test_owned_capture_released_on_close_or_failed_open(monkeypatch):
    captures = []

    def grabber():
        result = Capture()
        captures.append(result)
        return result

    monkeypatch.setattr(L, "_need", lambda name: SimpleNamespace(mss=grabber))
    env = L.LocalEnv(gui=Gui())
    env.close()
    env.close()
    assert captures[0].closes == 1

    class MissingSize(Gui):
        def size(self):
            raise RuntimeError("screen unavailable")

    with pytest.raises(RuntimeError, match="screen unavailable"):
        L.LocalEnv(gui=MissingSize())
    assert captures[1].closes == 1

    supplied = Capture()
    L.LocalEnv(gui=Gui(), grabber=supplied).close()
    assert supplied.closes == 0


def test_shared_console_restored_once_by_last_owner(monkeypatch):
    calls = []
    user = SimpleNamespace(IsWindowVisible=lambda hwnd: True, IsIconic=lambda hwnd: bool(calls),
                           ShowWindow=lambda hwnd, mode: calls.append(mode))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=user, kernel32=SimpleNamespace(
        GetConsoleWindow=lambda: 19)), raising=False)
    monkeypatch.setattr(L, "_need", lambda name: Gui())
    one, two = [L.LocalEnv(grabber=Capture(), confirm=None) for _ in range(2)]
    assert calls == [6]
    one.show_terminal()
    one.close()
    assert calls == [6]
    two.close()
    two.close()
    assert calls == [6, 9]
    # The console was already minimized by someone else.
    L.LocalEnv(grabber=Capture(), confirm=None).close()
    assert calls == [6, 9]


def test_owned_screen_closed_on_sdk_error_supplied_env_kept(monkeypatch):
    monkeypatch.setattr(L, "_need", lambda name: SimpleNamespace(mss=lambda: capture))
    capture = Capture()
    env = L.LocalEnv(gui=Gui())
    monkeypatch.setattr(L, "local", lambda **kw: env)

    def fail(*args, **kwargs):
        raise ValueError("model setup failed")

    monkeypatch.setattr(agent, "agent_for", fail)
    with pytest.raises(ValueError, match="model setup failed"):
        legato.run("task", desktop=env)
    assert capture.closes == 0
    with pytest.raises(ValueError, match="model setup failed"):
        legato.run("task", desktop="screen")
    assert capture.closes == 1


def test_mcp_releases_open_failed_closed_and_abandoned_screens(monkeypatch):
    captures = []

    def local(**options):
        capture = Capture()
        captures.append(capture)
        monkeypatch.setattr(L, "_need", lambda name: SimpleNamespace(mss=lambda: capture))
        env = L.LocalEnv(gui=Gui(), **options)
        if len(captures) == 1:
            def fail():
                raise RuntimeError("capture lost")
            env._shot = fail
        return env

    monkeypatch.setattr(L, "local", local)
    srv = build_server(lambda res: providers.local(*res))

    async def go():
        with pytest.raises(Exception, match="capture lost"):
            await srv.call_tool("desktop_open", {})
        assert captures[0].closes == 1
        r = await srv.call_tool("desktop_open", {})
        did = r.content[0].text.split()[1]
        await srv.call_tool("desktop_close", {"desktop_id": did})
        await srv.call_tool("desktop_close", {"desktop_id": did})
        assert captures[1].closes == 1
        await srv.call_tool("desktop_open", {})

    asyncio.run(go())
    srv.close_left()
    srv.close_left()
    assert [capture.closes for capture in captures] == [1, 1, 1]


def test_cli_closes_its_screen_on_model_setup_error(monkeypatch):
    from legato.__main__ import main

    capture = Capture()
    monkeypatch.setattr(L, "_need", lambda name: SimpleNamespace(mss=lambda: capture))
    monkeypatch.setattr(L, "local", lambda **kw: L.LocalEnv(gui=Gui(), **kw))

    def fail(*args, **kwargs):
        raise ValueError("model setup failed")

    monkeypatch.setattr(agent, "agent_for", fail)
    assert main(["run", "--screen", "--model", "local", "--base-url", "http://127.0.0.1:9998/v1", "task"]) == 2
    assert capture.closes == 1


def test_mcp_record_failure_during_close_still_releases_handle(tmp_path):
    env = legato.attach(screenshot=lambda: b"tiny", execute=lambda action: None)
    releases = []
    record = tmp_path / "record"
    srv = build_server(lambda res: (lambda: releases.append(True), env), image_format="png", record=str(record))

    async def go():
        r = await srv.call_tool("desktop_open", {})
        did = r.content[0].text.split()[1]
        # Replace the log with a directory so recording "close" fails.
        log = record / did / "log.jsonl"
        log.unlink()
        log.mkdir()
        with pytest.raises(Exception, match="desktop_close failed"):
            await srv.call_tool("desktop_close", {"desktop_id": did})
        assert releases == [True]
        await srv.call_tool("desktop_close", {"desktop_id": did})
        assert releases == [True]

    asyncio.run(go())
