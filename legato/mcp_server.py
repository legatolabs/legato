"""Legato as an MCP server: give any agent (Claude Code, Cursor, your own) a desktop with Legato's ready-wait.

    pip install "legatolabs[e2b,mcp]"
    claude mcp add legato -e E2B_API_KEY=... -- legato mcp          # Claude Code, cloud desktops
    claude mcp add legato -- legato mcp --local                      # this computer (pip install "legatolabs[local,mcp]")
    claude mcp add legato -- legato mcp --sandbox my_desktop.py:desktop   # your own sandbox (docs/sandboxes.md)
    # Cursor and others: {"mcpServers": {"legato": {"command": "legato", "args": ["mcp"], "env": {"E2B_API_KEY": "..."}}}}

Tools:
- desktop_open: start an E2B desktop (with --local: this computer's screen) and return its first screenshot.
- desktop_act: click, type, press keys, scroll or drag. The tool returns once the screen has stopped changing, with
  the new screenshot. Coordinates are pixels of the last screenshot.
- desktop_screenshot: the current screen.
- desktop_run_task: hand a whole task to `legato.Harness` with a model of your choice (for example an open model on
  OpenRouter) and get back its report and the final screen.
- desktop_close: stop the desktop.
"""
from __future__ import annotations

import os
import time
import uuid
from typing import Any, Callable

from legato.actions import ALIASES, KINDS, Action, norm_keys
from legato.env import Env

try:                                   # mcp >= 2
    from mcp.server.mcpserver import Image
    from mcp.server.mcpserver import MCPServer as _Server
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:                    # mcp 1.x
    try:
        from mcp.server.fastmcp import FastMCP as _Server
        from mcp.server.fastmcp import Image
        from mcp.server.fastmcp.exceptions import ToolError
    except ImportError as e:           # pragma: no cover
        raise ImportError('the MCP server needs the mcp package: pip install "legatolabs[mcp]"') from e


def e2b_factory(resolution: tuple[int, int]):
    from legato import providers
    return providers.e2b(*resolution)


def build_server(factory: Callable[[tuple[int, int]], tuple[Any, Env]] = e2b_factory, about: str = "") -> _Server:
    """The MCP server. `factory(resolution)` returns (sandbox, env); the default starts an E2B desktop. `about` tells
    the agent what kind of desktop it gets."""
    srv = _Server("legato", instructions=(
        "Desktops for computer use. Open one with desktop_open, act with desktop_act using pixel coordinates of "
        "the latest screenshot, and close it with desktop_close when done. desktop_act returns as soon as the screen "
        "has settled, so there is no need to wait or take extra screenshots." + (" " + about if about else "")))
    desks: dict[str, dict] = {}

    def shot(d: dict) -> Image:
        obs = d["env"].observe()
        d["wh"] = (obs.width, obs.height)
        return Image(data=obs.screenshot, format="png")

    def get(desktop_id: str) -> dict:
        if desktop_id not in desks:
            raise ToolError(f"no open desktop {desktop_id!r}; open one with desktop_open")
        return desks[desktop_id]

    @srv.tool()
    def desktop_open(width: int = 1280, height: int = 800) -> list:
        """Open a desktop and return its id and first screenshot: a fresh cloud desktop (E2B), or this computer's
        screen when the server runs with --local, or your own sandbox with --sandbox. With --sandbox docker, the first
        call can take a few minutes while the desktop image downloads."""
        sb, env = factory((width, height))
        did = uuid.uuid4().hex[:12]
        desks[did] = {"sb": sb, "env": env, "wh": (width, height), "opened": time.time()}
        img = shot(desks[did])
        w, h = desks[did]["wh"]
        return [f"desktop_id: {did} ({w}x{h})", img]

    @srv.tool()
    def desktop_act(desktop_id: str, action: str, x: int | None = None, y: int | None = None, text: str | None = None,
                    keys: str | None = None, direction: str | None = None, amount: int | None = None,
                    end_x: int | None = None, end_y: int | None = None) -> list:
        """Act on the desktop and return the settled screen. action: click, double_click, right_click (x, y); type
        (text); key (keys, e.g. "ctrl+s", "Return"); scroll (direction up/down/left/right, amount, optional x, y);
        drag (x, y to end_x, end_y); wait. Coordinates are pixels of the latest screenshot."""
        d = get(desktop_id)
        kind = ALIASES.get(action.strip().lower(), action.strip().lower())
        if kind not in KINDS or kind in ("done", "fail"):
            raise ToolError(f"unknown action {action!r}")
        if kind in ("click", "double_click", "right_click", "drag") and (x is None or y is None):
            raise ToolError(f"{kind} needs x and y")
        a = Action(kind, x=x, y=y, x2=end_x, y2=end_y, text=text, keys=norm_keys(keys) if keys else None,
                   direction=direction, amount=amount)
        t = time.monotonic()
        obs = d["env"].act(a)
        d["wh"] = (obs.width, obs.height)
        last = d["env"].last
        note = (f"{a.describe()}: done in {time.monotonic() - t:.2f} s "
                f"(waited {last.get('settle_s', 0.0):.2f} s for the screen)")
        return [note, Image(data=obs.screenshot, format="png")]

    @srv.tool()
    def desktop_screenshot(desktop_id: str) -> Image:
        """The current screen of an open desktop."""
        return shot(get(desktop_id))

    @srv.tool()
    def desktop_run_task(desktop_id: str, task: str, model: str = "qwen/qwen3.8-27b",
                         base_url: str = "https://openrouter.ai/api/v1", api_key_env: str = "OPENROUTER_API_KEY",
                         max_steps: int = 30) -> list:
        """Hand a whole task to Legato's harness with the given model (any OpenAI-compatible endpoint; the key is read
        from the environment variable named by api_key_env). Returns the run report and the final screen."""
        from legato.harness import Harness
        d = get(desktop_id)
        agent = Harness(model=model, base_url=base_url, api_key=os.environ.get(api_key_env, ""))
        res = agent.run(task, d["env"], max_steps=max_steps)
        return [res.report() + "\nactions: " + "; ".join(res.actions[-20:]), shot(d)]

    @srv.tool()
    def desktop_close(desktop_id: str) -> str:
        """Stop a desktop."""
        d = desks.pop(desktop_id, None)
        if d is None:
            return f"no open desktop {desktop_id!r}"
        from legato import providers
        providers.close(d["sb"])
        return f"closed {desktop_id} after {time.time() - d['opened']:.0f} s"

    return srv


def main(argv=None) -> int:
    import argparse

    from legato import providers
    ap = argparse.ArgumentParser(prog="legato mcp", description="MCP server: a desktop for any agent")
    ap.add_argument("--sandbox", default="e2b",
                    help="e2b (default), local (this computer), or your own: path/to/file.py:function or "
                         "module:function, a function (width, height) returning a legato Env (see docs/sandboxes.md)")
    ap.add_argument("--local", action="store_true", help="the same as --sandbox local (moves your real mouse)")
    a = ap.parse_args(argv)
    spec = "local" if a.local else a.sandbox
    make = providers.load(spec)
    providers.warm(spec)                       # e.g. pull the desktop image while the agent gets going
    build_server(lambda res: make(*res), about=providers.about(spec)).run()
    return 0
