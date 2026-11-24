"""One task, start to finish, with your saved model and desktop: what `legato run`, the MCP server's
desktop_run_task and `legato.run()` all call.

    import legato
    result = legato.run("Find the weather in Tokyo tomorrow and tell me")     # your model, your desktop
    print(result.answer, result.report())

Every way of running Legato gets the same agent for the same desktop: `agent_for(env)` sets the harness up the way
it was measured best there (legato-research tracks/mac/champion.json for a Mac's screen, the Mac app's own setup;
Harness's defaults elsewhere, where the one-action loop was faster: KNOWLEDGE.md, EXP-0055).
"""
from __future__ import annotations

import sys
import threading
import time
from typing import Any, Callable

from legato import config as cfgmod

# Added to a task run through legato.run, `legato run` and desktop_run_task, so a question gets its answer back.
ANSWER_NOTE = ("\n\nIf the task asks for information, finish with done and put the answer itself in done's text "
               "(the number, name or sentence asked for).")
from legato.env import Env
from legato.harness import Harness, RunResult


def agent_for(env: Env | None = None, *, model: str | None = None, base_url: str | None = None,
              api_key: str | None = None, cfg: cfgmod.Config | None = None, **options: Any) -> Harness:
    """A Harness for `env` with the saved model (or the one given). On a Mac's whole screen: the Mac app's setup
    (one plan per turn, a shell and checked AppleScript beside the screen, 4 screenshots kept, the ending fix). In
    one app's window in the background: one action at a time, with no shell, so nothing it runs takes the screen
    from you (4 screenshots kept, the ending fix). Elsewhere: Harness's defaults. `options` go to Harness and win over these."""
    cfg = cfg or cfgmod.load()
    model = model or cfg.model
    base_url = base_url or cfg.base_url
    if not model or not base_url:
        raise ValueError(cfg.missing() or "pass model= and base_url=")
    if api_key is None:
        api_key = cfg.key if base_url == cfg.base_url else ""
    setup: dict[str, Any] = {}
    kind = desktop_kind(env)
    if kind == "mac-screen":
        from legato import app_engine
        ask = getattr(env, "confirm", None) or (lambda question: True)
        setup = dict(plan=True, system=app_engine._app_plan_system(), end_fix=True, image_max=4,
                     tools=[app_engine.shell_tool(ask), app_engine.applescript_tool(ask)])
    elif kind == "mac-background":
        # the loop background mode is tested with (tests/test_background_live.py), with the Mac's ending fix and 4
        # screenshots; plans in the background are unmeasured (two small runs on 24 Nov were inconclusive)
        setup = dict(end_fix=True, image_max=4)
    setup.update(options)
    return Harness(model=model, base_url=base_url, api_key=api_key, **setup)


def desktop_kind(env: Env | None) -> str:
    """"mac-background", "mac-screen" (this Mac's own screen), or "other" (a sandbox, Linux, Windows)."""
    if env is None:
        return "other"
    from legato.local import LocalEnv
    if type(env).__name__ == "BackgroundEnv":
        return "mac-background"
    if isinstance(env, LocalEnv) and sys.platform == "darwin":
        return "mac-screen"
    return "other"


def open_desktop(desktop: str | None = None, *, task: str = "", app: str | None = None, width: int = 1280,
                 height: int = 800, confirm: Any = "auto", cfg: cfgmod.Config | None = None
                 ) -> tuple[Env, Callable[[], None], str]:
    """(env, close, what) for a desktop by name ("background", "screen", "docker", "e2b", or a --sandbox spec such as
    my_desktop.py:desktop). With none given: the saved one, else background on a Mac and the screen elsewhere. In the
    background, the app is `app`, else the saved one, else the one the task names, else the default browser."""
    cfg = cfg or cfgmod.load()
    desktop = cfgmod.ALIASES.get(desktop or "", desktop) or cfg.desktop or cfgmod.default_desktop()
    if desktop == "background":
        if sys.platform != "darwin":
            raise RuntimeError("background mode is macOS only; use --screen (this screen) or --sandbox docker")
        from legato.background import background
        name = app or cfgmod.pick_app(task, cfg)
        return background(name, confirm=confirm), (lambda: None), f"{name}'s window, in the background"
    if desktop == "screen":
        from legato.local import local
        return local(confirm=confirm), (lambda: None), "this screen"
    from legato import providers
    make = providers.e2b if desktop == "e2b" else providers.load(desktop)
    handle, env = make(width, height)
    return env, (lambda: providers.close(handle)), providers.about(desktop) or desktop


def run(task: str, desktop: str | Env | None = None, *, model: str | None = None, base_url: str | None = None,
        api_key: str | None = None, app: str | None = None, max_steps: int = 40, on_step: Callable | None = None,
        confirm: Any = "auto", timeout_s: float | None = None, cancel: threading.Event | None = None,
        **options: Any) -> RunResult:
    """Run one task with your saved model on your saved desktop (`legato setup`), or the ones given. `desktop` is a
    name ("background", "screen", "docker", "e2b"), a --sandbox spec, or an Env you opened yourself. The result has
    status (how it ended: done, failed, max_steps, timeout, cancelled, stopped or error), done, answer (what the
    model said at the end), error, usage and report() (where the time went).

    It returns rather than raises when the run goes wrong: a model call or a desktop that fails, mid-run or while
    opening, comes back with status "error" and `error` saying why, with the usage so far. A missing model or a bad
    argument still raises. timeout_s: a deadline for the whole run (status "timeout"); cancel: a threading.Event
    that ends the run before its next model call once set (status "cancelled").

    confirm: what happens before a risky action (keys such as cmd+Delete or cmd+Q, a typed rm or sudo: the list is in
    legato.safety) on this computer ("background", "screen"); other desktops ignore it, and an Env you pass keeps its
    own. "auto" (the default) asks yes/no in the terminal when stdin is one, and in an unattended run (a server, a
    job, stdin not a terminal) asks nothing and runs the action. A function confirm(description) -> bool decides
    each one: False skips the action and the model is told to find another way. None never asks and runs them all;
    legato.safety.deny refuses them all, the safe choice for unattended runs on a real computer."""
    t0 = time.monotonic()
    if isinstance(desktop, Env):
        env, close = desktop, (lambda: None)
    else:
        try:
            env, close, _ = open_desktop(desktop, task=task, app=app, confirm=confirm)
        except (RuntimeError, OSError) as e:          # Docker stopped, no screen permission, an app that won't open
            return RunResult(task, error=f"cannot start the desktop: {e}"[:300], status="error",
                             wall_s=time.monotonic() - t0)
    try:
        agent = agent_for(env, model=model, base_url=base_url, api_key=api_key, **options)
        res = agent.run(task + ANSWER_NOTE, env, max_steps=max_steps, on_step=on_step, timeout_s=timeout_s,
                        cancel=cancel, raise_desktop_errors=False)
        res.task = task
        return res
    finally:
        close()
