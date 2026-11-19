"""Legato as an MCP server: give any agent (Claude Code, Cursor, your own) a desktop with Legato's ready-wait.

    pip install "legatolabs[e2b,mcp]"
    claude mcp add legato -e E2B_API_KEY=... -- legato mcp          # Claude Code, cloud desktops
    claude mcp add legato -- legato mcp --local                      # this computer (pip install "legatolabs[local,mcp]")
    claude mcp add legato -- legato mcp --sandbox my_desktop.py:desktop   # your own sandbox (docs/sandboxes.md)
    # Cursor and others: {"mcpServers": {"legato": {"command": "legato", "args": ["mcp"], "env": {"E2B_API_KEY": "..."}}}}

Tools:
- desktop_open: start an E2B desktop (with --local: this computer's screen) and return its first screenshot. With url or
  app, it starts that first and returns once its window has drawn.
- desktop_act: click, type, press keys, scroll or drag. The tool returns once the screen has stopped changing, with
  the new screenshot. Coordinates are pixels of the last screenshot.
- desktop_screenshot: the current screen.
- desktop_shell: run a shell command on the desktop's computer and get its output and exit code.
- desktop_run_task: hand a whole task to `legato.Harness` with a model of your choice (for example an open model on
  OpenRouter) and get back its report and the final screen.
- desktop_close: stop the desktop.
"""
from __future__ import annotations

import os
import shlex
import sys
import time
import uuid
from typing import Any, Callable

from legato.actions import ALIASES, KINDS, Action, norm_keys
from legato.env import Env
from legato.safety import Denied, Stopped
from legato.settle import Waiter, digest
from legato.tools import local_shell

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


LAUNCH_RULE = {"launch": {"floor": 0.5, "quiet": 1.0, "min_same": 2}}   # once the window starts drawing


def launch_command(platform: str, url: str | None = None, app: str | None = None) -> str:
    """The shell command that opens `url` (in the default browser) or starts `app`, without waiting for it. `app` is
    an app name on macOS (open -a) and a command elsewhere. `platform`: "darwin", "win32" or anything else for Linux."""
    if platform == "darwin":
        return f"open -a {shlex.quote(app)}" + (f" {shlex.quote(url)}" if url else "") if app else f"open {shlex.quote(url or '')}"
    if platform == "win32":
        return f'start "" {app} {url or ""}'.strip() if app else f'start "" "{url}"'
    if app:
        return f"nohup {app}" + (f" {shlex.quote(url)}" if url else "") + " >/dev/null 2>&1 &"
    q = shlex.quote(url or "")
    return f"(xdg-open {q} || firefox-esr {q} || firefox {q}) >/dev/null 2>&1 &"


def wait_for_launch(screenshot, before: str, appear_s: float = 10.0) -> None:
    """Wait until the screen differs from the frame digest `before` (the app's window appeared), then until it holds
    still. Gives up after `appear_s` with no change: the app may have been open already."""
    t = time.monotonic()
    while time.monotonic() - t < appear_s:
        if digest(screenshot()) != before:
            Waiter(screenshot, LAUNCH_RULE).settle("launch", cap_s=10.0, poll_s=0.15)
            return
        time.sleep(0.15)


def e2b_factory(resolution: tuple[int, int]):
    from legato import providers
    return providers.e2b(*resolution)


def to_image(png: bytes, image_format: str = "jpeg", quality: int = 85) -> Image:
    """A screenshot for the agent: JPEG by default (the same pixels in about a fifth of the bytes, as the harness sends
    since EXP-0053), PNG as it came with image_format="png" or when Pillow can't read it."""
    if image_format == "jpeg":
        try:
            import io

            from PIL import Image as PILImage
            buf = io.BytesIO()
            PILImage.open(io.BytesIO(png)).convert("RGB").save(buf, format="JPEG", quality=quality)
            return Image(data=buf.getvalue(), format="jpeg")
        except Exception:  # noqa: BLE001 - never fail an action over the encoding
            pass
    return Image(data=png, format="png")


def build_server(factory: Callable[[tuple[int, int]], tuple[Any, Env]] = e2b_factory, about: str = "",
                 record: str | None = None, image_format: str = "jpeg") -> _Server:
    """The MCP server. `factory(resolution)` returns (sandbox, env); the default starts an E2B desktop. `about` tells
    the agent what kind of desktop it gets. With `record`, every screenshot the agent gets is saved under
    record/<desktop_id>/, with a log.jsonl of the actions. `image_format`: "jpeg" (default) or "png" for the
    screenshots the agent gets; recordings stay PNG."""
    if image_format not in ("jpeg", "png"):
        raise ValueError("image_format must be 'jpeg' or 'png'")
    srv = _Server("legato", instructions=(
        "Desktops for computer use. Open one with desktop_open, act with desktop_act using pixel coordinates of "
        "the latest screenshot, and close it with desktop_close when done. desktop_act returns as soon as the screen "
        "has settled, so there is no need to wait or take extra screenshots. desktop_shell runs a command on the "
        "desktop's computer: use it to start apps, read and write files and check results, which is faster than "
        "the screen." + (" " + about if about else "")))
    desks: dict[str, dict] = {}

    def keep(did: str, event: str, png: bytes | None, **detail: Any) -> None:
        if not record:
            return
        import json
        from pathlib import Path
        d = desks[did]
        d["n"] = d.get("n", 0) + 1
        folder = Path(record) / did
        folder.mkdir(parents=True, exist_ok=True)
        name = f"{d['n']:04d}-{event}.png"
        if png:
            (folder / name).write_bytes(png)
        with open(folder / "log.jsonl", "a") as f:
            f.write(json.dumps({"t": round(time.time() - d["opened"], 3), "step": d["n"], "event": event,
                                "screenshot": name if png else None, **detail}) + "\n")

    def shot(d: dict) -> Image:
        obs = d["env"].observe()
        d["wh"] = (obs.width, obs.height)
        d["png"] = obs.screenshot
        return to_image(obs.screenshot, image_format)

    def get(desktop_id: str) -> dict:
        if desktop_id not in desks:
            raise ToolError(f"no open desktop {desktop_id!r}; open one with desktop_open")
        return desks[desktop_id]

    @srv.tool()
    def desktop_open(width: int = 1280, height: int = 800, url: str | None = None, app: str | None = None) -> list:
        """Open a desktop and return its id and first screenshot: a fresh cloud desktop (E2B), or this computer's
        screen when the server runs with --local, or your own sandbox with --sandbox. With --sandbox docker, the first
        call can take a few minutes while the desktop image downloads. url: open it in the browser first. app: start
        it first (an app name on macOS, such as "TextEdit"; a command on Linux, such as "gnome-calculator"). With url
        or app the screenshot is taken once the window has drawn."""
        sb, env = factory((width, height))
        did = uuid.uuid4().hex[:12]
        desks[did] = {"sb": sb, "env": env, "wh": (width, height), "opened": time.time()}
        note = ""
        if url or app:
            if not callable(getattr(env, "shell", None)):
                note = " (this desktop has no shell, so url and app were not opened)"
            else:
                platform = sys.platform if env.shell is local_shell else "linux"
                before = digest(env.observe().screenshot)
                t = time.monotonic()
                out = env.shell(launch_command(platform, url, app), 30.0)
                if not out.rstrip().endswith("[exit 0]"):
                    note = f" (starting it failed: {out.strip()[-300:]})"
                else:
                    wait_for_launch(lambda: env.observe().screenshot, before)
                    note = f" (started {app or url} in {time.monotonic() - t:.1f} s)"
        img = shot(desks[did])
        w, h = desks[did]["wh"]
        keep(did, "open", desks[did]["png"], width=w, height=h, url=url, app=app)
        return [f"desktop_id: {did} ({w}x{h}){note}", img]

    @srv.tool()
    def desktop_act(desktop_id: str, action: str, x: int | None = None, y: int | None = None, text: str | None = None,
                    keys: str | None = None, direction: str | None = None, amount: int | None = None,
                    end_x: int | None = None, end_y: int | None = None) -> list:
        """Act on the desktop and return the settled screen. action: click, double_click, triple_click, right_click
        (x, y); type (text); key (keys, e.g. "ctrl+s", "Return"); scroll (direction up/down/left/right, amount,
        optional x, y); drag (x, y to end_x, end_y); wait. Coordinates are pixels of the latest screenshot."""
        d = get(desktop_id)
        kind = ALIASES.get(action.strip().lower(), action.strip().lower())
        if kind not in KINDS or kind in ("done", "fail"):
            raise ToolError(f"unknown action {action!r}")
        if kind in ("click", "double_click", "triple_click", "right_click", "drag") and (x is None or y is None):
            raise ToolError(f"{kind} needs x and y")
        a = Action(kind, x=x, y=y, x2=end_x, y2=end_y, text=text, keys=norm_keys(keys) if keys else None,
                   direction=direction, amount=amount)
        t = time.monotonic()
        try:
            obs = d["env"].act(a)
        except Stopped as e:
            raise ToolError(f"The person at this computer stopped the agent ({e}). Do not act again until they "
                            "ask you to.") from None
        except Denied as e:
            raise ToolError(f"Not run: the person at this computer did not allow it ({e}).") from None
        d["wh"] = (obs.width, obs.height)
        last = d["env"].last
        note = (f"{a.describe()}: done in {time.monotonic() - t:.2f} s "
                f"(waited {last.get('settle_s', 0.0):.2f} s for the screen)")
        keep(desktop_id, kind, obs.screenshot, action=a.describe(), settle_s=round(last.get("settle_s", 0.0), 3))
        return [note, to_image(obs.screenshot, image_format)]

    @srv.tool()
    def desktop_screenshot(desktop_id: str) -> Image:
        """The current screen of an open desktop."""
        img = shot(get(desktop_id))
        keep(desktop_id, "screenshot", desks[desktop_id]["png"])
        return img

    @srv.tool()
    def desktop_shell(desktop_id: str, command: str, timeout_s: float = 30.0) -> str:
        """Run a shell command on the desktop's computer and return its output (stdout and stderr) and exit code.
        Use it to start apps, read and write files and check results; it is faster than the screen. Start a GUI app
        in the background (nohup app >/dev/null 2>&1 &), or the command waits for it to exit. timeout_s: 1 to 300."""
        from legato.tools import bash
        d = get(desktop_id)
        try:
            tool = bash(d["env"], timeout_s=min(max(float(timeout_s), 1.0), 300.0))
        except TypeError:
            raise ToolError("this desktop has no shell") from None
        t = time.monotonic()
        out = tool.handler({"command": command})
        keep(desktop_id, "shell", None, command=command, seconds=round(time.monotonic() - t, 3))
        return out

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
        img = shot(d)
        keep(desktop_id, "run_task", d["png"], task=task, model=model, done=res.done, actions=res.actions[-50:])
        return [res.report() + "\nactions: " + "; ".join(res.actions[-20:]), img]

    @srv.tool()
    def desktop_close(desktop_id: str) -> str:
        """Stop a desktop."""
        if desktop_id not in desks:
            return f"no open desktop {desktop_id!r}"
        keep(desktop_id, "close", None)
        d = desks.pop(desktop_id)
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
    ap.add_argument("--record", metavar="DIR", help="save every screenshot the agent gets, with a log of its actions, "
                                                   "under DIR/<desktop_id>/")
    ap.add_argument("--image-format", choices=("jpeg", "png"), default="jpeg",
                    help="screenshots the agent gets: jpeg (default, about a fifth of the bytes) or png (lossless)")
    ap.add_argument("--fixed-wait", type=float, metavar="S",
                    help="sleep S seconds after every action instead of waiting for the screen (a baseline to "
                         "compare against, as in docs/benchmarks.md)")
    ap.add_argument("--wait-scope", choices=("screen", "app"), default="screen",
                    help="with --local on macOS: what the wait for the screen watches, all of it (default) or only "
                         "the front app's windows, so another app animating doesn't hold every action to the cap")
    a = ap.parse_args(argv)
    spec = "local" if a.local else a.sandbox
    make = providers.load(spec)
    if spec == "local" and a.wait_scope != "screen":
        def make(w, h):  # noqa: F811 - the same desktop, with the wait on the front app's windows
            from legato.local import local
            return None, local(wait_scope=a.wait_scope)
    providers.warm(spec)                       # e.g. pull the desktop image while the agent gets going

    def factory(res):
        sb, env = make(*res)
        if a.fixed_wait is not None:
            env.fixed_wait_s = a.fixed_wait
        return sb, env
    build_server(factory, about=providers.about(spec), record=a.record,
                 image_format=a.image_format).run()
    return 0
