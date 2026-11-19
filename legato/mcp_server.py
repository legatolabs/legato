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
- desktop_run_task: hand a whole task to Legato's own loop with a model of your choice (set in the server's
  environment: LEGATO_MODEL, LEGATO_BASE_URL, LEGATO_API_KEY or OPENROUTER_API_KEY) and get back its report and the
  final screen. On this Mac (--local) it runs the Legato Mac app's setup (`legato.presets.mac`).
- desktop_close: stop the desktop.

With --local on macOS, risky actions (legato.safety: keys, typed and shell commands, AppleScript) wait for the
person's Allow in a macOS dialog. The MCP client and its model never see the question, so they cannot answer it, and
the desktop's other tools wait while it is open.
"""
from __future__ import annotations

import contextlib
import os
import shlex
import sys
import threading
import time
import uuid
from typing import Any, Callable, Mapping

from legato import safety
from legato.actions import ALIASES, KINDS, Action, norm_keys
from legato.env import Env
from legato.safety import Denied, Stopped
from legato.settle import Waiter, digest
from legato.tools import local_shell

DEFAULT_MODEL = "qwen/qwen3.8-27b"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "[::1]")

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


def local_confirm() -> safety.Confirm | None:
    """Who answers a risky action's question under `legato mcp --local`: the person, in a macOS dialog (stdin is the
    MCP client's pipe, so there is no terminal to ask in). Elsewhere, or with LEGATO_CONFIRM=off in the server's
    environment, nothing is asked, as before."""
    if safety.confirm_off() or sys.platform != "darwin":
        return None
    return safety.dialog


def local_factory(confirm: safety.Confirm | None) -> Callable[[tuple[int, int]], tuple[Any, Env]]:
    """`--local`: this computer, as the Legato Mac app drives it (`legato.presets.mac_env`: the wait that leaves out
    what moves on its own), with `confirm` for risky actions."""
    def make(resolution: tuple[int, int]) -> tuple[Any, Env]:
        from legato import presets
        return None, presets.mac_env(confirm=confirm)
    return make


def is_local(env: Env) -> bool:
    """Is `env` this computer (`legato.local()`)?"""
    from legato.local import LocalEnv
    return isinstance(env, LocalEnv)


def model_settings(model: str | None = None, base_url: str | None = None, api_key_env: str | None = None,
                   environ: Mapping[str, str] = os.environ) -> tuple[str, str, str]:
    """(model, base_url, api key) for desktop_run_task: the tool's arguments first, then the server's environment
    (LEGATO_MODEL, LEGATO_BASE_URL), then Qwen3.8-27B on OpenRouter. The key: the variable named by `api_key_env`;
    else LEGATO_API_KEY, but only for the server's own base URL (a key set for one endpoint is not sent to another
    that a tool call names); else OPENROUTER_API_KEY for an OpenRouter URL. "" when there is none."""
    own = (environ.get("LEGATO_BASE_URL") or DEFAULT_BASE_URL).strip()
    url = (base_url or own).strip()
    name = (model or environ.get("LEGATO_MODEL") or DEFAULT_MODEL).strip()
    if api_key_env:
        return name, url, environ.get(api_key_env, "")
    key = environ.get("LEGATO_API_KEY", "") if url.rstrip("/") == own.rstrip("/") else ""
    if not key and "openrouter.ai" in url:
        key = environ.get("OPENROUTER_API_KEY", "")
    return name, url, key


def _needs_key(base_url: str) -> bool:
    return not any(h in base_url for h in LOCAL_HOSTS)


def build_server(factory: Callable[[tuple[int, int]], tuple[Any, Env]] = e2b_factory, about: str = "",
                 record: str | None = None, run_task: bool = True) -> _Server:
    """The MCP server. `factory(resolution)` returns (sandbox, env); the default starts an E2B desktop. `about` tells
    the agent what kind of desktop it gets. With `record`, every screenshot the agent gets is saved under
    record/<desktop_id>/, with a log.jsonl of the actions. A desktop that is this computer (`legato.local()`) runs
    one tool call at a time, and desktop_run_task gives it the Legato Mac app's setup on macOS. run_task=False
    leaves desktop_run_task out: the agent's own model does all the clicking."""
    _, own_url, own_key = model_settings()
    no_model = (" desktop_run_task has no model on this server (set LEGATO_API_KEY or OPENROUTER_API_KEY in its "
                "environment), so drive with desktop_act." if run_task and _needs_key(own_url) and not own_key else "")
    srv = _Server("legato", instructions=(
        "Desktops for computer use. Open one with desktop_open, act with desktop_act using pixel coordinates of "
        "the latest screenshot, and close it with desktop_close when done. desktop_act returns as soon as the screen "
        "has settled, so there is no need to wait or take extra screenshots. desktop_shell runs a command on the "
        "desktop's computer: use it to start apps, read and write files and check results, which is faster than "
        "the screen." + (" " + about if about else "") + no_model))
    desks: dict[str, dict] = {}
    local_lock = threading.Lock()              # this computer has one screen, mouse and keyboard: one call at a time

    def busy(d: dict):
        """Hold this computer while a call acts on it. While a dialog asks the person about a risky action, another
        call waits here: it cannot click the dialog's buttons."""
        return local_lock if d.get("local") else contextlib.nullcontext()

    def new_desk(width: int, height: int) -> str:
        sb, env = factory((width, height))
        did = uuid.uuid4().hex[:12]
        desks[did] = {"sb": sb, "env": env, "wh": (width, height), "opened": time.time(), "local": is_local(env)}
        return did

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
        return Image(data=obs.screenshot, format="png")

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
        or app the screenshot is taken once the window has drawn. To do a whole task on the user's own computer,
        call desktop_run_task instead: it opens the desktop itself."""
        did = new_desk(width, height)
        env = desks[did]["env"]
        note = ""
        if url or app:
            if not callable(getattr(env, "shell", None)):
                note = " (this desktop has no shell, so url and app were not opened)"
            else:
                with busy(desks[did]):
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
            with busy(d):
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
        return [note, Image(data=obs.screenshot, format="png")]

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
        in the background (nohup app >/dev/null 2>&1 &), or the command waits for it to exit. timeout_s: 1 to 300.
        On the user's own computer, a command that may not be undone, sends something off it or reads private data
        waits for the person's yes."""
        from legato.tools import bash
        d = get(desktop_id)
        try:
            tool = bash(d["env"], timeout_s=min(max(float(timeout_s), 1.0), 300.0))
        except TypeError:
            raise ToolError("this desktop has no shell") from None
        t = time.monotonic()
        with busy(d):
            out = None
            if d["local"] and command.strip():
                from legato.presets import check_command
                out = check_command(d["env"], command)
            out = out or tool.handler({"command": command})
        keep(desktop_id, "shell", None, command=command, seconds=round(time.monotonic() - t, 3))
        return out

    def desktop_run_task(task: str, desktop_id: str | None = None, model: str | None = None,
                         base_url: str | None = None, api_key_env: str | None = None, max_steps: int | None = None,
                         plain: bool = False) -> list:
        """Hand a whole task to Legato and get back its report and the final screen. Legato runs it with its own
        loop and model, so your turn ends when the task is done. On the user's own Mac (--local) this is the fast
        default for "do X on my computer" (open an app, play a song, add a reminder, fill in a form): give the task in
        plain words, in one call. It runs the Legato Mac app's setup: plans of several steps a model call, a shell
        and AppleScript beside the screen, and the person's yes before anything risky. task: what to do, with any
        detail the user gave. desktop_id: a desktop from desktop_open (default: open one). The model is the
        server's (LEGATO_MODEL, LEGATO_BASE_URL, LEGATO_API_KEY or OPENROUTER_API_KEY in its environment; default
        Qwen3.8-27B on OpenRouter); model and base_url override it, and api_key_env names the environment variable
        with the key. max_steps: model turns (default 40 on a Mac, else 30). plain: the library's default loop
        instead."""
        from legato.harness import Harness
        from legato import presets
        name, url, key = model_settings(model, base_url, api_key_env)
        if _needs_key(url) and not key:
            raise ToolError(f"no API key for {url}: set LEGATO_API_KEY (or OPENROUTER_API_KEY for OpenRouter) in the "
                            "MCP server's environment" + (f", or set {api_key_env}" if api_key_env else ""))
        did = desktop_id or new_desk(1280, 800)
        d = get(did)
        env = d["env"]
        preset = not plain and d["local"] and bool(getattr(env, "mac", False))
        if preset:
            agent, steps, kind = presets.mac(env, name, url, key), max_steps or presets.MAX_STEPS, "the Mac app's setup"
        else:
            agent, steps, kind = Harness(model=name, base_url=url, api_key=key), max_steps or 30, "the plain loop"
        with busy(d):
            res = agent.run(task, env, max_steps=steps)
            img = shot(d)
        keep(did, "run_task", d["png"], task=task, model=name, harness="mac" if preset else "plain", done=res.done,
             actions=res.actions[-50:])
        return [f"desktop_id: {did} · {name} with {kind}\n" + res.report() + "\nactions: " +
                "; ".join(res.actions[-20:]), img]

    if run_task:
        srv.tool()(desktop_run_task)

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
    ap.add_argument("--drive", action="store_true", help="leave out desktop_run_task: your agent's own model does "
                                                         "all the clicking with desktop_act")
    ap.add_argument("--record", metavar="DIR", help="save every screenshot the agent gets, with a log of its actions, "
                                                   "under DIR/<desktop_id>/")
    a = ap.parse_args(argv)
    spec = "local" if a.local else a.sandbox
    about = providers.about(spec)
    if spec == "local":
        confirm = local_confirm()
        factory = local_factory(confirm)
        _, url, key = model_settings()
        if not a.drive and (key or not _needs_key(url)):
            about += (" To do a task on it (\"open Spotify and play X\", \"add a reminder\"), hand the whole task to "
                      "desktop_run_task in one call: Legato runs it with its own loop. Drive with desktop_act only to "
                      "do it step by step yourself.")
        if confirm is safety.dialog:
            about += (" Risky actions (deleting, quitting apps, sending, installing) wait for the person's Allow in a "
                      "macOS dialog; you cannot answer it, and a no comes back as not run.")
    else:
        make = providers.load(spec)
        factory = lambda res: make(*res)  # noqa: E731
    providers.warm(spec)                       # e.g. pull the desktop image while the agent gets going
    build_server(factory, about=about, record=a.record, run_task=not a.drive).run()
    return 0
