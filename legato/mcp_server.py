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
- desktop_read: in the background on a Mac, the window's text from its Accessibility tree, to check what it says
  without a screenshot.
- desktop_shell: run a shell command on the desktop's computer and get its output and exit code.
- desktop_run_task: hand a whole task to Legato's own agent (your saved model, or one you name) and get back its
  report and the final screen.
- desktop_close: stop the desktop.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time
import uuid
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


class RunTaskOutput(TypedDict):
    """desktop_run_task's structured result, for a client to branch on."""
    status: str               # done, failed, max_steps, timeout, cancelled, stopped or error
    answer: str               # what the agent said at the end ("" if nothing)
    seconds: float            # how long the run took
    steps: int                # model turns
    error: str | None         # why it failed, for error and timeout


FAILED = ("failed", "timeout", "error")       # statuses reported as a failed tool call (isError)
NEW_MCP = _Server.__module__.startswith("mcp.server.mcpserver")    # mcp 2: a tool can return its own CallToolResult
if NEW_MCP:
    from typing import Annotated

    from mcp.types import CallToolResult, TextContent
    RUN_RETURN: Any = Annotated[CallToolResult, RunTaskOutput]
else:                                         # mcp 1: text and the screenshot, as before
    RUN_RETURN = list


def run_result(text: str, img: Any, out: dict) -> Any:
    """desktop_run_task's reply: the report as text (older clients read that), the final screen, and `out` as
    structured content; a run that failed, timed out or errored is marked as an error (isError), so a client sees it
    without reading the text. With mcp 1, the text and the screen only."""
    if not NEW_MCP:
        return [text] + ([img] if img is not None else [])
    content = [TextContent(type="text", text=text)] + ([img.to_image_content()] if img is not None else [])
    return CallToolResult.model_validate({"content": content, "structuredContent": out,
                                          "isError": out["status"] in FAILED})


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
    @_said
    def desktop_open(width: int = 1280, height: int = 800, url: str | None = None, app: str | None = None) -> list:
        """Open a desktop and return its id and first screenshot. Which desktop depends on how the server was
        started (the server's instructions say): one app's window on the person's Mac in the background, their whole
        screen, a desktop container, a cloud desktop or their own sandbox. With --sandbox docker, the first call can
        take a few minutes while the desktop image downloads. url: open it in the browser first. app: start it first
        (an app name on macOS, such as "TextEdit"; in the background, the app to work in, default the browser; a
        command on Linux, such as "gnome-calculator"). With url or app the screenshot is taken once the window has
        drawn."""
        if background is not None:
            return open_background(url, app)
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

    def open_background(url: str | None, app: str | None) -> list:
        if not app:
            from legato import config
            app = config.pick_app("")                       # the saved app, else the browser
        env = None
        try:
            env = background(app)                           # a window of its own: the person's stay as they are
            if url:
                env.open_url(url)                           # returns once the page has loaded (Accessibility)
                Waiter(lambda: env.observe().screenshot, PAGE_RULE).settle("page", cap_s=5.0, poll_s=0.1)
            did = uuid.uuid4().hex[:12]
            desks[did] = {"sb": None, "env": env, "wh": (0, 0), "opened": time.time()}
            img = shot(desks[did])
        except (RuntimeError, OSError, subprocess.SubprocessError) as e:
            close = getattr(env, "close", None)
            if callable(close):
                close()                                     # the window it opened, if it got that far
            raise ToolError(f"could not open {app} in the background: {e}") from None
        w, h = desks[did]["wh"]
        what = env.describe() if hasattr(env, "describe") else f"{app}'s window"
        keep(did, "open", desks[did]["png"], width=w, height=h, url=url, app=app, window=what)
        return [f"desktop_id: {did} ({w}x{h}): {what}, worked in the background; the person's other windows are left "
                "alone. Coordinates are pixels of this window. Clicks press, focus or select what is under them; "
                "drags and clicks on nothing pressable are not possible here, so use the keyboard for those.", img]

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
            raise ToolError(f"Not run: the person at this computer did not allow it ({e}).") from None
        d["wh"] = (obs.width, obs.height)
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

    @srv.tool()
    @_said
    def desktop_read(desktop_id: str, max_chars: int = 8000) -> str:
        """The text of a background desktop's window (legato mcp --background, macOS), read from its Accessibility
        tree: an outline with one line per element, such as `- heading "Counter"`, `- text "Count: 1"` or
        `- textbox "Name": "Ada" [focused]`. In a browser it is the page, with its title and address first. Use it to
        check what an app or page says (a count, a message, a field's value) without a screenshot. max_chars: where
        the outline is cut (default 8000). Other desktops have no such tree: use desktop_screenshot."""
        from legato.background import BackgroundEnv
        d = get(desktop_id)
        if not isinstance(d["env"], BackgroundEnv):
            raise ToolError("desktop_read works only in background mode on a Mac (legato mcp --background), where it "
                            "reads the window's Accessibility tree; on this desktop use desktop_screenshot, or "
                            "desktop_shell to check files and output")
        t = time.monotonic()
        text = d["env"].read(max_chars=min(max(int(max_chars), 1), 200_000))
        keep(desktop_id, "read", None, chars=len(text), seconds=round(time.monotonic() - t, 3))
        return text

    @srv.tool()
    @_said
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

    def desktop_run_task(desktop_id: str, task: str, model: str | None = None, base_url: str | None = None,
                         api_key_env: str | None = None, max_steps: int = 40, timeout_s: float | None = None) -> Any:
        """Hand a whole task to Legato's own agent and get back what it found, its report and the final screen. It
        uses the model saved by `legato setup` unless you name one (model, base_url: any OpenAI-compatible endpoint;
        the key is read from the environment variable api_key_env). Good for long GUI chores while you keep working.
        The reply starts with `status:`, how the run ended: done, failed, max_steps, timeout, cancelled, stopped or
        error; only done means the agent says it finished, so check the result before relying on it. timeout_s: a
        deadline for the whole run in seconds (status timeout, with what was done so far)."""
        from legato import config
        from legato.agent import agent_for
        d = get(desktop_id)
        key = os.environ.get(api_key_env, "") if api_key_env else None
        source = f"the key in ${api_key_env}" if api_key_env else None
        if model and not base_url and not config.load().base_url:
            base_url = "https://openrouter.ai/api/v1"
            key = key if key is not None else os.environ.get("OPENROUTER_API_KEY", "")
            source = source or "the key in $OPENROUTER_API_KEY"
        try:
            agent = agent_for(d["env"], model=model, base_url=base_url, api_key=key)
        except ValueError as e:
            raise ToolError(f"no model for desktop_run_task: {e}") from None
        if source and hasattr(agent.client, "key_source"):  # named if the server refuses the key
            agent.client.key_source = source
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
                            timeout_s=float(timeout_s) if timeout_s else None)
        finally:
            if record:
                del env.observe, env.act                # the class's own again
        answer = f"answer: {res.answer}\n" if res.answer else ""
        text = (f"status: {res.status}\n" + answer + res.report() + "\nactions: " + "; ".join(res.actions[-20:]))
        out = {"status": res.status, "answer": res.answer, "seconds": round(res.wall_s, 2), "steps": res.steps,
               "error": res.error}
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

    @srv.tool()
    @_said
    def desktop_close(desktop_id: str) -> str:
        """Stop a desktop. In the background, the window desktop_open(url=...) opened is closed; the app stays."""
        if desktop_id not in desks:
            return f"no open desktop {desktop_id!r}"
        keep(desktop_id, "close", None)
        d = desks.pop(desktop_id)
        from legato import providers
        providers.close(d["sb"])
        from legato.background import BackgroundEnv
        if isinstance(d["env"], BackgroundEnv):           # the window desktop_open(url=...) opened
            try:
                d["env"].close()
            except Exception:  # noqa: BLE001 - the desktop is gone from the list either way
                pass
        return f"closed {desktop_id} after {time.time() - d['opened']:.0f} s"

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
    ap.add_argument("--background", action="store_true",
                    help="macOS: work in one app's window (desktop_open's app) without moving your mouse or taking "
                         "the screen, so you can keep using the Mac (docs/sandboxes.md)")
    a = ap.parse_args(argv)
    if not (a.local or a.background or a.sandbox):        # what legato setup saved
        from legato import config
        saved = config.load().desktop or config.default_desktop()
        a.background = saved == "background" and sys.platform == "darwin"
        a.sandbox = {"screen": "local", "background": "local"}.get(saved, saved)
    spec = "local" if a.local or a.background else a.sandbox
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
    bg = None
    if a.background:
        def bg(app: str) -> Env:
            from legato.background import background
            return background(app, fixed_wait_s=a.fixed_wait)
    build_server(factory, about=providers.about("background" if a.background else spec), record=a.record,
                 image_format=a.image_format, background=bg).run()
    return 0
