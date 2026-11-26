"""Legato as an MCP server: give any agent (Claude Code, Codex, Cursor, your own) a computer, with Legato's wait
for the screen after every action.

    legato setup                                   # adds it to the coding agents it finds, with your desktop
    claude mcp add legato -- legato mcp            # by hand: the desktop legato setup saved (on a Mac, default:
                                                   # one app's window in the background, so you keep the Mac)
    claude mcp add legato -- legato mcp --sandbox docker   # a desktop container (needs Docker)
    claude mcp add legato -- legato mcp --local            # this computer's whole screen
    claude mcp add legato -- legato mcp --sandbox my_desktop.py:desktop   # your own sandbox (docs/sandboxes.md)

Tools:
- desktop_open: open a desktop and return its first screenshot. With url or app, it starts that first and returns
  once its window has drawn.
- desktop_act: click, type, press keys, scroll or drag. The tool returns once the screen has stopped changing, with
  the new screenshot. Coordinates are pixels of the last screenshot.
- desktop_screenshot: the current screen.
- desktop_read: the window's text as an outline, to check what it says without a screenshot: in the background on
  a Mac (its Accessibility tree) and on a Windows screen (the foreground window, UI Automation).
- desktop_wait: wait until given text is in the window, or until the screen has changed and holds still.
- desktop_shell: run a shell command on the desktop's computer and get its output and exit code.
- desktop_run_task: hand a whole task to Legato's own agent (your saved model, or one you name) and get back its
  report and the final screen.
- desktop_close: stop the desktop.
"""
from __future__ import annotations

import re
import shlex
import subprocess
import sys
import time
import uuid
from contextlib import ExitStack
from typing import Any, Callable

from typing_extensions import TypedDict

from legato.actions import ALIASES, KINDS, Action, norm_keys
from legato.env import Env
from legato.safety import Denied, Stopped, Unavailable
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


def _said(fn):
    """The tool's failure with its reason, for the client: the MCP library shows the model only "Error executing tool
    <name>" for any exception but ToolError, and the reason (a window that can't be captured, an app that won't open)
    stays in the server's log."""
    import functools

    @functools.wraps(fn)
    def tool(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ToolError:
            raise
        except (RuntimeError, OSError, subprocess.SubprocessError) as e:
            raise ToolError(f"{fn.__name__} failed: {e}") from None
    return tool


LAUNCH_RULE = {"launch": {"floor": 0.5, "quiet": 1.0, "min_same": 2}}   # once the window starts drawing
PAGE_RULE = {"page": {"floor": 0.1, "quiet": 0.3, "min_same": 2}}       # a page already loaded: until it is drawn


def launch_command(platform: str, url: str | None = None, app: str | None = None) -> str:
    """The shell command that opens `url` (in the default browser) or starts `app`, without waiting for it. `app` is
    an app name on macOS (open -a) and a command elsewhere. `platform`: "darwin", "win32" (PowerShell, which is what
    Legato's shell runs there) or anything else for Linux."""
    if platform == "darwin":
        return f"open -a {shlex.quote(app)}" + (f" {shlex.quote(url)}" if url else "") if app else f"open {shlex.quote(url or '')}"
    if platform == "win32":
        # PowerShell: Start-Process, as cmd's `start` is not there. Its single quotes are ' and the curly ‘ ’ ‚ ‛,
        # and any of them doubled is a literal one, so each is doubled
        q = "'" + re.sub("([\u0027\u2018\u2019\u201a\u201b])", r"\1\1", url or "") + "'"
        return f"Start-Process {app}" + (f" {q}" if url else "") if app else f"Start-Process {q}"
    if app:
        return f"nohup {app}" + (f" {shlex.quote(url)}" if url else "") + " >/dev/null 2>&1 &"
    q = shlex.quote(url or "")
    return f"(xdg-open {q} || firefox-esr {q} || firefox {q}) >/dev/null 2>&1 &"


def shell_risk(command: str) -> str | None:
    """Why a command for this computer's shell waits for a yes (rm, sudo, a command inside bash -c, an inline
    program that deletes or uses the network, printing a key; PowerShell's Remove-Item and the like), or None."""
    from legato import safety
    from legato.app_engine import command_risk
    return command_risk(command) or safety.text_risk(command + "\n")


def ask_first(env: Any, command: str) -> None:
    """On this computer, a risky command runs only with the yes of the desktop's confirm: under `legato mcp`
    nobody can answer, so it is refused unless the server was started with --yes. Other desktops run it."""
    from legato.local import LocalEnv
    if not isinstance(env, LocalEnv):
        return
    why = shell_risk(command)
    confirm = getattr(env, "confirm", None)
    if why and confirm is not None and not confirm(f"run this command, which {why}: {command}"):
        raise ToolError(f"Not run: this command {why}, and risky commands are refused here, since nobody at this "
                        "computer can be asked. Ask the user: they can allow them by starting `legato mcp --yes`.")


def wait_for_launch(screenshot, before: str, appear_s: float = 10.0) -> None:
    """Wait until the screen differs from the frame digest `before` (the app's window appeared), then until it holds
    still. Gives up after `appear_s` with no change: the app may have been open already."""
    t = time.monotonic()
    while time.monotonic() - t < appear_s:
        if digest(screenshot()) != before:
            Waiter(screenshot, LAUNCH_RULE).settle("launch", cap_s=10.0, poll_s=0.15)
            return
        time.sleep(0.15)


class RunTaskOutput(TypedDict):
    """desktop_run_task's structured result, for a client to branch on."""
    status: str               # done, failed, max_steps, timeout, cancelled, stopped or error
    answer: str               # what the agent said at the end ("" if nothing)
    seconds: float            # how long the run took
    steps: int                # model turns
    error: str | None         # why it failed, for error and timeout
    cost_usd: float | None    # what the model calls cost, where the provider reports it (OpenRouter does)


class OpenOutput(TypedDict):
    """desktop_open's structured result."""
    desktop_id: str           # for every other tool
    width: int                # the desktop's real size in pixels, which may not be the size asked for
    height: int
    started: str | None       # the url or app that was started first, if one was asked for and it started
    start_error: str | None   # why the url or app did not start; the desktop is open all the same


FAILED = ("failed", "timeout", "error")       # statuses reported as a failed tool call (isError)
NEW_MCP = _Server.__module__.startswith("mcp.server.mcpserver")    # mcp 2: a tool can return its own CallToolResult
if NEW_MCP:
    from typing import Annotated

    from mcp.types import CallToolResult, TextContent
    RUN_RETURN: Any = Annotated[CallToolResult, RunTaskOutput]
    OPEN_RETURN: Any = Annotated[CallToolResult, OpenOutput]
else:                                         # mcp 1: text and the screenshot, as before
    RUN_RETURN = OPEN_RETURN = list


def run_result(text: str, img: Any, out: dict) -> Any:
    """desktop_run_task's reply: the report as text (older clients read that), the final screen, and `out` as
    structured content; a run that failed, timed out or errored is marked as an error (isError), so a client sees it
    without reading the text. With mcp 1, the text and the screen only."""
    if not NEW_MCP:
        return [text] + ([img] if img is not None else [])
    content = [TextContent(type="text", text=text)] + ([img.to_image_content()] if img is not None else [])
    return CallToolResult.model_validate({"content": content, "structuredContent": out,
                                          "isError": out["status"] in FAILED})


def open_result(text: str, img: Any, out: dict) -> Any:
    """desktop_open's reply: the id and size as text, the first screen, and `out` as structured content; a url or
    app that did not start marks it as an error (isError), with the desktop's id still there. With mcp 1, the text
    and the screen only."""
    if not NEW_MCP:
        return [text, img]
    return CallToolResult.model_validate({"content": [TextContent(type="text", text=text), img.to_image_content()],
                                          "structuredContent": out, "isError": bool(out["start_error"])})


def no_key(choice: Any = None) -> str:
    """desktop_run_task's message when it has no model key: an MCP client starts the server with its own
    environment, often without the shell's keys, so "run legato setup" alone misleads. With the model that was
    chosen (a legato.config.Choice): the key it needs, and the providers this server has a key for, by name."""
    from legato import config
    if choice is None or not choice.key_env:
        return ("no model for desktop_run_task: no model key in this server's environment: add it (OPENROUTER_API_KEY, "
                "ANTHROPIC_API_KEY, OPENAI_API_KEY or another provider's) to your MCP client's `env` for this server, "
                "or run `legato setup` to save one")
    name = config.provider_name(choice.provider) or choice.base_url
    save = (f"legato setup --add-key {choice.provider}" if choice.provider in config.KEYED and choice.cfg.model
            else "legato setup")
    has = [p for p in choice.cfg.keys_found() if p != choice.provider]
    return (f"no model for desktop_run_task: no key for {name} ({choice.model}) in this server's environment: add "
            f"{choice.key_env} to your MCP client's `env` for this server, or run `{save}` to save one"
            + (f". It has a key for: {', '.join(has)}" if has else ""))


def windows_screen(env: Any) -> bool:
    """Is `env` this computer's own screen, on Windows? (Its shell is then PowerShell, with UI Automation.)"""
    return sys.platform == "win32" and getattr(env, "shell", None) is local_shell


def e2b_factory(resolution: tuple[int, int]):
    from legato import providers
    return providers.e2b(*resolution)


def to_image(png: bytes, image_format: str = "jpeg", quality: int = 85) -> Image:
    """A screenshot for the agent: JPEG by default (the same pixels in about a fifth of the bytes, as the harness
    sends), PNG as it came with image_format="png" or when Pillow can't read it."""
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
                 record: str | None = None, image_format: str = "jpeg",
                 background: Callable[[str], Env] | None = None) -> _Server:
    """The MCP server. `factory(resolution)` returns (sandbox, env); the default starts an E2B desktop. `about` tells
    the agent what kind of desktop it gets. With `record`, every screenshot the agent gets is saved under
    record/<desktop_id>/, with a log.jsonl of the actions. `image_format`: "jpeg" (default) or "png" for the
    screenshots the agent gets; recordings stay PNG. With `background(app) -> Env`, desktop_open works in one app's
    window in the background (macOS, `legato.background`) and needs `app`."""
    if image_format not in ("jpeg", "png"):
        raise ValueError("image_format must be 'jpeg' or 'png'")
    srv = _Server("legato", instructions=(
        "Desktops for computer use. Open one with desktop_open, act with desktop_act using pixel coordinates of "
        "the latest screenshot, and close it with desktop_close when done. desktop_act returns as soon as the screen "
        "has settled, so there is no need to wait or take extra screenshots. desktop_shell runs a command on the "
        "desktop's computer: use it to start apps, read and write files and check results, which is faster than "
        "the screen. A window started that way takes a moment: desktop_wait waits for it. desktop_read gives a "
        "window's text without a screenshot (a Mac in the background, a Windows screen)."
        + (" " + about if about else "")))
    from legato import __version__
    for low in (getattr(srv, "_lowlevel_server", None), getattr(srv, "_mcp_server", None)):
        if low is not None:
            low.version = __version__             # serverInfo.version, which the library leaves empty
    desks: dict[str, dict] = {}

    def keep(did: str, event: str, png: bytes | None, *, state: dict | None = None, **detail: Any) -> None:
        if not record:
            return
        import json
        from pathlib import Path
        d = desks[did] if state is None else state
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

    def desktop_open(width: int | None = None, height: int | None = None, url: str | None = None,
                     app: str | None = None) -> Any:
        """Open a desktop and return its id, size and first screenshot (also as structured content: desktop_id,
        width, height, started, start_error). Which desktop depends on how the server was started (the server's
        instructions say): one app's window on the person's Mac in the background, their whole screen (macOS,
        Windows, Linux), a desktop container, a cloud desktop or their own sandbox. With --sandbox docker, the first
        call can take a few minutes while the desktop image downloads. width, height: the size of a new container or
        cloud desktop (default 1280x800); a real screen or window keeps its own size, which the result gives. url:
        open it in the browser first. app: start it first (an app name on macOS, such as "TextEdit"; in the
        background, the app to work in, default the browser; a command on Linux or Windows, such as
        "gnome-calculator" or "notepad"). With url or app the screenshot is taken once the window has drawn; if
        starting it failed, the result is an error that says why, and the desktop is open all the same."""
        if background is not None:
            return open_background(url, app)
        asked = (width, height) if width or height else None
        width, height = width or 1280, height or 800
        sb, env = factory((width, height))
        from legato import providers
        with ExitStack() as opening:
            opening.callback(providers.close, sb)
            did = uuid.uuid4().hex[:12]
            d = {"sb": sb, "env": env, "wh": (width, height), "opened": time.time()}
            note, started, failed = "", None, None
            if url or app:
                if not callable(getattr(env, "shell", None)):
                    note = " (this desktop has no shell, so url and app were not opened)"
                    failed = "this desktop has no shell, so url and app were not opened"
                else:
                    platform = sys.platform if env.shell is local_shell else "linux"
                    before = digest(env.observe().screenshot)
                    t = time.monotonic()
                    launch = launch_command(platform, url, app)
                    ask_first(env, launch)                       # `app` is a command line on Windows and Linux
                    out = env.shell(launch, 30.0)
                    if not out.rstrip().endswith("[exit 0]"):
                        failed = " ".join(out.split())           # PowerShell says why first, bash last
                        failed = failed if len(failed) <= 500 else failed[:300] + " ... " + failed[-150:]
                        note = f" (NOT started: starting {app or url} failed, so the desktop is as it was: {failed})"
                    else:
                        wait_for_launch(lambda: env.observe().screenshot, before)
                        note = f" (started {app or url} in {time.monotonic() - t:.1f} s)"
                        started = app or url
            img = shot(d)
            w, h = d["wh"]
            if asked and (w, h) != (width, height):
                note += f" (its size is {w}x{h}, not the {width}x{height} asked for: this desktop's size is its own)"
            keep(did, "open", d["png"], state=d, width=w, height=h, url=url, app=app)
            result = open_result(f"desktop_id: {did} ({w}x{h}){note}", img,
                               {"desktop_id": did, "width": w, "height": h, "started": started, "start_error": failed})
            desks[did] = d
            opening.pop_all()
            return result

    desktop_open.__annotations__["return"] = OPEN_RETURN
    srv.tool()(_said(desktop_open))

    def open_background(url: str | None, app: str | None) -> Any:
        if not app:
            from legato import config
            app = config.pick_app("")                       # the saved app, else the browser
        try:
            env = background(app)                           # a window of its own: the person's stay as they are
            with ExitStack() as opening:
                close = getattr(env, "close", None)
                if callable(close):
                    opening.callback(close)
                if url:
                    env.open_url(url)
                    Waiter(lambda: env.observe().screenshot, PAGE_RULE).settle("page", cap_s=5.0, poll_s=0.1)
                did = uuid.uuid4().hex[:12]
                d = {"sb": None, "env": env, "wh": (0, 0), "opened": time.time()}
                img = shot(d)
                w, h = d["wh"]
                what = env.describe() if hasattr(env, "describe") else f"{app}'s window"
                keep(did, "open", d["png"], state=d, width=w, height=h, url=url, app=app, window=what)
                result = open_result(
                    f"desktop_id: {did} ({w}x{h}): {what}, worked in the background; the person's other windows are left "
                    "alone. Coordinates are pixels of this window. Clicks press, focus or select what is under them; "
                    "drags and clicks on nothing pressable are not possible here, so use the keyboard for those.", img,
                    {"desktop_id": did, "width": w, "height": h, "started": url or app, "start_error": None})
                desks[did] = d
                opening.pop_all()
                return result
        except (RuntimeError, OSError, subprocess.SubprocessError) as e:
            raise ToolError(f"could not open {app} in the background: {e}") from None

    @srv.tool()
    @_said
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
        except Unavailable as e:
            raise ToolError(f"Not run: this desktop can't do that ({e}).") from None
        except Denied as e:
            from legato import safety
            if getattr(d["env"], "confirm", None) is safety.deny:      # nobody at the server's terminal to ask
                raise ToolError(f"Not run: risky actions are refused here, since nobody at this computer can be "
                                f"asked ({e}). Ask the user: they can allow them by starting `legato mcp --yes`.") \
                    from None
            raise ToolError(f"Not run: the person at this computer did not allow it ({e}).") from None
        d["wh"], d["png"] = (obs.width, obs.height), obs.screenshot
        last = d["env"].last
        note = (f"{a.describe()}: done in {time.monotonic() - t:.2f} s "
                f"(waited {last.get('settle_s', 0.0):.2f} s for the screen)")
        keep(desktop_id, kind, obs.screenshot, action=a.describe(), settle_s=round(last.get("settle_s", 0.0), 3))
        return [note, to_image(obs.screenshot, image_format)]

    @srv.tool()
    @_said
    def desktop_screenshot(desktop_id: str) -> Image:
        """The current screen of an open desktop."""
        img = shot(get(desktop_id))
        keep(desktop_id, "screenshot", desks[desktop_id]["png"])
        return img

    def read_text(d: dict, max_chars: int) -> str:
        env = d["env"]
        reader = getattr(env, "read", None)
        if callable(reader):
            return reader(max_chars=max_chars)
        if windows_screen(env):
            from legato import winread
            return winread.read(env.shell, max_chars)
        raise ToolError("desktop_read needs a reader for this desktop. Built-in readers support background mode on a Mac "
                        "(the window's Accessibility tree) and a Windows screen (the foreground window's UI Automation "
                        "tree). A custom desktop can implement read(max_chars=...). Use desktop_screenshot to see it.")

    @srv.tool()
    @_said
    def desktop_read(desktop_id: str, max_chars: int = 8000) -> str:
        """The text of the window, without a screenshot: an outline with one line per element, such as
        `- heading "Counter"`, `- text "Count: 1"` or `- textbox "Name": "Ada" [focused]`. In a browser it is the
        page, with its title and address first. Use it to check what an app or page says (a count, a message, a
        field's value). It works with a developer-supplied read(max_chars=...) method, in the background on a Mac (the window's tree), and
        on a Windows screen (the foreground window, from UI Automation; about half a second). Other desktops (a
        Mac's or Linux's whole screen, a container, a sandbox) have no such tree and answer with an error: use
        desktop_screenshot. max_chars: where the outline is cut (default 8000)."""
        d = get(desktop_id)
        t = time.monotonic()
        text = read_text(d, min(max(int(max_chars), 1), 200_000))
        keep(desktop_id, "read", None, chars=len(text), seconds=round(time.monotonic() - t, 3))
        return text

    @srv.tool()
    @_said
    def desktop_wait(desktop_id: str, text: str | None = None, timeout_s: float = 10.0) -> list:
        """Wait for a window or page that is on its way, for example after desktop_shell started an app. text: wait
        until the window's text (what desktop_read gives, so where desktop_read works) contains it, ignoring case;
        returns the lines that have it, with no screenshot. Without text: wait until the screen differs from the
        last screenshot you got and holds still, on any desktop; returns the new screenshot. timeout_s: 1 to 120
        (default 10). Text that never came is an error that says what the window read instead."""
        d = get(desktop_id)
        cap = min(max(float(timeout_s), 1.0), 120.0)
        t = time.monotonic()
        if text:
            want, seen = text.casefold(), ""
            wake = getattr(d["env"], "wait_for_text_change", None)
            while True:
                try:
                    seen = read_text(d, 100_000)
                except RuntimeError as e:         # no window to read yet
                    seen, hit = f"(nothing: {e})", []
                else:
                    hit = [line.strip() for line in seen.splitlines() if want in line.casefold()]
                took = time.monotonic() - t
                if hit:
                    keep(desktop_id, "wait", None, text=text, seconds=round(took, 3), found=True)
                    return [f"{text!r} is there after {took:.1f} s: {seen.splitlines()[0]}\n" + "\n".join(hit[:5])]
                if took >= cap:
                    break
                pause = min(0.3, cap - took)
                if callable(wake):
                    wake(pause)                 # a notification can wake a read; only the read can match
                else:
                    time.sleep(pause)
            keep(desktop_id, "wait", None, text=text, seconds=round(took, 3), found=False)
            raise ToolError(f"{text!r} was not in the window after {took:.0f} s. It reads:\n{seen[:800]}")
        env = d["env"]

        def grab() -> bytes:
            return env.observe().screenshot
        before = digest(d["png"]) if d.get("png") else None
        changed = before is None
        while not changed and time.monotonic() - t < cap:
            changed = digest(grab()) != before
            if not changed:
                time.sleep(0.15)
        if changed:
            Waiter(grab, LAUNCH_RULE).settle("launch", cap_s=max(cap - (time.monotonic() - t), 1.0), poll_s=0.15)
        img = shot(d)
        took = time.monotonic() - t
        keep(desktop_id, "wait", d["png"], seconds=round(took, 3), changed=changed)
        return [f"the screen changed and held still after {took:.1f} s" if changed else
                f"the screen did not change in {took:.0f} s: it is as in your last screenshot", img]

    @srv.tool()
    @_said
    def desktop_shell(desktop_id: str, command: str, timeout_s: float = 30.0) -> str:
        """Run a shell command on the desktop's computer and return its output (stdout and stderr) and exit code.
        Use it to start apps, read and write files and check results; it is faster than the screen. On Windows the
        shell is PowerShell; elsewhere bash. Start a GUI app in the background, or the command waits for it to exit:
        `nohup app >/dev/null 2>&1 &` (macOS, Linux), `Start-Process app` (Windows). Its window takes a moment to
        appear: call desktop_wait before looking (desktop_open's url and app wait by themselves). timeout_s: 1 to
        300."""
        from legato.tools import bash
        d = get(desktop_id)
        try:
            tool = bash(d["env"], timeout_s=min(max(float(timeout_s), 1.0), 300.0))
        except TypeError:
            raise ToolError("this desktop has no shell") from None
        ask_first(d["env"], command)
        t = time.monotonic()
        out = tool.handler({"command": command})
        keep(desktop_id, "shell", None, command=command, seconds=round(time.monotonic() - t, 3))
        return out

    def desktop_run_task(task: str, desktop_id: str | None = None, model: str | None = None,
                         provider: str | None = None, base_url: str | None = None, api_key_env: str | None = None,
                         max_steps: int = 40, timeout_s: float | None = None) -> Any:
        """Hand a whole task to Legato's own agent and get back what it found, its report and the final screen (also
        as structured content: status, answer, seconds, steps, error, cost_usd). desktop_id: the desktop to work on;
        without one, a desktop is opened for the run and closed after it. Good for long GUI chores while you keep
        working. It uses the model saved by `legato setup` unless you name one. model: its name says where it runs:
        claude-... on Anthropic's API, gpt-... and o3... on OpenAI's, gemini-... on Google's, grok-... on xAI's,
        vendor/model (anthropic/claude-sonnet-5.5) on OpenRouter, each with that provider's key (saved with `legato
        setup --add-key PROVIDER`, or ANTHROPIC_API_KEY, OPENAI_API_KEY, ... in this server's environment). provider:
        name it instead (openrouter, anthropic, openai, gemini, xai, together, fireworks, groq, cerebras). base_url:
        any other OpenAI-compatible server, with its key in the environment variable api_key_env.
        The reply starts with `status:`, how the run ended: done, failed, max_steps, timeout, cancelled, stopped or
        error; only done means the agent says it finished, so check the result before relying on it. timeout_s: a
        deadline for the whole run in seconds (status timeout, with what was done so far)."""
        from legato import config
        if desktop_id:
            get(desktop_id)
        try:
            choice = config.resolve(model or None, provider=provider or None, base_url=base_url or None,
                                    api_key_env=api_key_env or None)
        except ValueError as e:                 # nothing set up, an unknown provider, a server with no model named
            raise ToolError(no_key() if "legato setup" in str(e) else f"no model for desktop_run_task: {e}") from None
        if choice.missing():
            raise ToolError(no_key(choice))
        own = not desktop_id
        if own:                                 # a desktop for this run only, once there is a model to run it
            first = open_background(None, None) if background is not None else desktop_open()
            desktop_id = (first.content[0].text if NEW_MCP else first[0]).split()[1].rstrip(":")
        try:
            return run_task(desktop_id, task, choice, max_steps, timeout_s)
        finally:
            if own:
                desktop_close(desktop_id)

    def run_task(desktop_id: str, task: str, choice: Any, max_steps: int, timeout_s: float | None) -> Any:
        from legato.agent import agent_for
        d = get(desktop_id)
        try:
            agent = agent_for(d["env"], model=choice.model, base_url=choice.base_url, api_key=choice.key,
                              cfg=choice.cfg)
        except ValueError as e:
            raise ToolError(f"no model for desktop_run_task: {e}") from None
        if choice.key_source and hasattr(agent.client, "key_source"):  # named if the server refuses the key
            agent.client.key_source = choice.key_source
        from legato.agent import ANSWER_NOTE
        env, last = d["env"], {}
        on_step = None
        if record:                              # each step's actions and screen, not only the last one
            def seen(fn):
                def wrapped(*a, **k):
                    obs = fn(*a, **k)
                    last["png"] = getattr(obs, "screenshot", None)
                    return obs
                return wrapped
            env.observe, env.act = seen(env.observe), seen(env.act)

            def on_step(turn, actions, timing):
                keep(desktop_id, "run_step", last.pop("png", None), turn=turn,
                     actions=[a.describe() if a else "screenshot" for a in actions],
                     seconds={k: round(v, 3) for k, v in timing.items() if isinstance(v, float)})
        try:
            res = agent.run(task + ANSWER_NOTE, env, max_steps=max_steps, on_step=on_step, raise_desktop_errors=False,
                            timeout_s=timeout_s)
        finally:
            if record:
                del env.observe, env.act                # the class's own again
        answer = f"answer: {res.answer}\n" if res.answer else ""
        text = (f"status: {res.status}\n" + answer + res.report() + "\nactions: " + "; ".join(res.actions[-20:]))
        cost = (getattr(res, "usage", None) or {}).get("cost_usd")
        out = {"status": res.status, "answer": res.answer, "seconds": round(res.wall_s, 2), "steps": res.steps,
               "error": res.error, "cost_usd": round(cost, 6) if cost else None}
        try:
            img = shot(d)
        except Exception:  # noqa: BLE001 - the desktop that failed the run may not give a screenshot either
            if res.status != "error":
                raise
            img = None                          # report() leads with the error
        keep(desktop_id, "run_task", d.get("png") if img is not None else None, task=task, model=agent.model,
             status=res.status, done=res.done, actions=res.actions[-50:])
        return run_result(text, img, out)

    desktop_run_task.__annotations__["return"] = RUN_RETURN
    srv.tool()(_said(desktop_run_task))

    def release(d: dict) -> None:
        from legato import providers
        from legato.background import BackgroundEnv
        try:
            providers.close(d["sb"])                     # only the handle its factory gave us
        finally:
            if isinstance(d["env"], BackgroundEnv) and d["sb"] is not d["env"]:
                d["env"].close()

    @srv.tool()
    @_said
    def desktop_close(desktop_id: str) -> str:
        """Stop a desktop. In the background, the window desktop_open(url=...) opened is closed; the app stays."""
        d = desks.pop(desktop_id, None)
        if d is None:
            return f"no open desktop {desktop_id!r}"
        try:
            keep(desktop_id, "close", None, state=d)
        finally:
            release(d)
        return f"closed {desktop_id} after {time.time() - d['opened']:.0f} s"

    def close_left() -> None:                   # the client went without closing its local desktops
        from legato.background import BackgroundEnv
        from legato.local import LocalEnv
        for did, d in list(desks.items()):
            if isinstance(d["env"], (BackgroundEnv, LocalEnv)) and desks.pop(did, None) is not None:
                try:
                    release(d)
                except Exception:  # noqa: BLE001 - continue closing the other screens
                    pass
    import atexit
    atexit.register(close_left)
    srv.close_left = close_left                 # for tests
    return srv


def main(argv=None) -> int:
    import argparse

    from legato import providers
    ap = argparse.ArgumentParser(prog="legato mcp", description="MCP server: a desktop for any agent")
    ap.add_argument("--sandbox", default=None,
                    help="docker (a desktop container), e2b (a cloud desktop), local (this screen), or your own: "
                         "path/to/file.py:function or module:function returning a legato Env (docs/sandboxes.md). "
                         "Default: the desktop legato setup saved; with none saved, the background on a Mac and this "
                         "screen elsewhere")
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
    ap.add_argument("--yes", action="store_true",
                    help="on this computer: run risky actions (Cmd+Q, a typed rm or sudo: legato.safety's list) "
                         "without asking. By default they are refused, since nobody is at the server's terminal to "
                         "say yes, and a client's approval of a tool call is not approval of each action in it")
    ap.add_argument("--background", action="store_true",
                    help="macOS: work in one app's window (desktop_open's app) without moving your mouse or taking "
                         "the screen, so you can keep using the Mac (docs/sandboxes.md)")
    a = ap.parse_args(argv)
    if not (a.local or a.background or a.sandbox):        # what legato setup saved
        from legato import config
        saved = config.load().desktop or config.default_desktop()
        a.background = saved == "background"
        a.sandbox = {"screen": "local", "background": "local"}.get(saved, saved)
    if a.background and sys.platform != "darwin":
        ap.error("background mode requires macOS; use --local for the foreground screen or --sandbox docker")
    spec = "local" if a.local or a.background else a.sandbox
    make = providers.load(spec)
    if spec == "local" and a.wait_scope != "screen":
        def make(w, h):  # noqa: F811 - the same desktop, with the wait on the front app's windows
            return providers.local(w, h, wait_scope=a.wait_scope)
    providers.warm(spec)                       # e.g. pull the desktop image while the agent gets going
    from legato import safety
    from legato.local import LocalEnv
    # this computer's risky actions: an MCP server's stdin is the client's pipe, so "auto" would ask nobody and run
    # them all; they are refused instead (desktop_act's, and the shell and AppleScript steps of desktop_run_task)
    confirm = None if a.yes else safety.default_confirm(unattended=safety.deny)

    def factory(res):
        sb, env = make(*res)
        if a.fixed_wait is not None:
            env.fixed_wait_s = a.fixed_wait
        if isinstance(env, LocalEnv):
            env.confirm = confirm
        return sb, env
    bg = None
    if a.background:
        def bg(app: str) -> Env:
            from legato.background import background
            return background(app, fixed_wait_s=a.fixed_wait, confirm=confirm)
    build_server(factory, about=providers.about("background" if a.background else spec), record=a.record,
                 image_format=a.image_format, background=bg).run()
    return 0
