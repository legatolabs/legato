"""One task, start to finish, with your saved model and desktop: what `legato run`, the MCP server's
desktop_run_task and `legato.run()` all call.

    import legato
    result = legato.run("Find the weather in Tokyo tomorrow and tell me")     # your model, your desktop
    print(result.answer, result.report())

Every way of running Legato gets the same agent for the same desktop: `agent_for(env)` sets the harness up the way
it was measured best there (our Mac test runs for a Mac's screen, the Mac app's own setup;
Harness's defaults elsewhere, where the one-action loop was faster).
"""
from __future__ import annotations

import os
import sys
import threading
import time
from typing import Any, Callable

from legato import config as cfgmod

from legato import safety
from legato.env import DesktopStartError, Env
# ANSWER_NOTE is added to a task run through legato.run, `legato run` and desktop_run_task, so a question gets its
# answer back; a done without one then gets one more turn when the task's words ask (asks_for_information)
from legato.harness import ANSWER_NOTE, Harness, RunResult, asks_for_information  # noqa: F401

# What legato.run says for a desktop name that is none of these (the CLI says it with --sandbox's words).
DESKTOPS = ('"background", "screen", "docker", "docker:IMAGE", "e2b", "path/to/file.py:function" or '
            '"module:function", or a desktop you opened (legato.local(...), legato.attach(...))')


def agent_for(env: Env | None = None, *, model: str | None = None, base_url: str | None = None,
              api_key: str | None = None, provider: str | None = None, api_key_env: str | None = None,
              cfg: cfgmod.Config | None = None, **options: Any) -> Harness:
    """A Harness for `env` with the saved model (or the one given). On a Mac's whole screen: the Mac app's setup
    (one plan per turn, a shell and checked AppleScript beside the screen, 4 screenshots kept, the ending fix). In
    one app's window in the background: one action at a time, with no shell, so nothing it runs takes the screen
    from you (4 screenshots kept, the ending fix). Elsewhere: Harness's defaults. For the saved model, what setup's
    check found it needs: its coordinates, and JSON actions when it takes no tool calls. `options` go to Harness and
    win over these.

    A model named without a provider or server runs where its name says (legato.config.resolve): claude-... on
    Anthropic's API with your Anthropic key, gpt-... on OpenAI's, a vendor/model name on OpenRouter; `provider`
    ("anthropic", "openai", "openrouter", ...) names it instead. Without api_key, the key is that provider's: saved
    by `legato setup --add-key`, or in its usual environment variable (or in api_key_env)."""
    cfg = cfg or cfgmod.load()
    choice = cfgmod.resolve(model, provider=provider, base_url=base_url, api_key_env=api_key_env, cfg=cfg)
    model, base_url = choice.model, choice.base_url
    source = "the api_key you passed"
    if api_key is None:
        api_key, source = choice.key, choice.key_source
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
    if model == cfg.model and base_url.rstrip("/") == cfg.base_url.rstrip("/"):
        # what `legato setup` found this model needs (legato.config.check_model)
        if cfg.coords in ("pixels", "norm1000"):
            setup["coords"] = cfg.coords
        if cfg.tool_calls == "json":
            setup["native_tools"] = False
    setup.update(options)
    h = Harness(model=model, base_url=base_url, api_key=api_key, **setup)
    if hasattr(h.client, "key_source"):          # named in a 401 or 403 (never the key itself)
        h.client.key_source = source
    return h


def desktop_kind(env: Env | None) -> str:
    """"mac-background", "mac-screen" (this Mac's own screen), or "other" (a sandbox, Linux, Windows)."""
    if env is None:
        return "other"
    from legato.local import LocalEnv
    if not isinstance(env, LocalEnv):
        return "other"
    from legato.background import BackgroundEnv
    if isinstance(env, BackgroundEnv):
        return "mac-background"
    if sys.platform == "darwin":
        return "mac-screen"
    return "other"


def open_desktop(desktop: str | None = None, *, task: str = "", app: str | None = None, width: int = 1280,
                 height: int = 800, confirm: Any = "auto", cfg: cfgmod.Config | None = None,
                 timeout_s: float | None = None) -> tuple[Env, Callable[[], None], str]:
    """(env, close, what) for a desktop by name ("background", "screen", "docker", "e2b", or a --sandbox spec such as
    my_desktop.py:desktop). With none given: the saved one, else background on a Mac and the screen elsewhere. In the
    background, the app is `app`, else the saved one, else the one the task names, else Safari, and the desktop is a
    window of its own (a new browser window or document; `close` closes a browser window it opened). timeout_s bounds
    the
    start where it can be bounded: an app's launch in the background, docker (image pull included) and E2B's create
    request; your own --sandbox function is not. Raises DesktopStartError when the desktop can't start, and
    legato.providers.SpecError (a ValueError) for a name that is no desktop."""
    cfg = cfg or cfgmod.load()
    desktop = cfgmod.ALIASES.get(desktop or "", desktop) or cfg.desktop or cfgmod.default_desktop()
    if desktop == "background":
        if sys.platform != "darwin":
            raise DesktopStartError('background mode is macOS only: use "screen" (this screen) or "docker"')
        from legato.background import background
        name = app or cfgmod.pick_app(task, cfg)
        launch = 30.0 if timeout_s is None else max(0.0, min(30.0, timeout_s))
        env = background(name, confirm=confirm, launch_s=launch)    # a window of its own: the person's stay as they are
        what = env.describe() if hasattr(env, "describe") else f"{name}'s window"
        return env, getattr(env, "close", lambda: None), f"{what}, in the background"
    if desktop == "screen":
        from legato.local import local
        env = local(confirm=confirm)
        return env, getattr(env, "close", lambda: None), "this screen"
    from legato import providers
    make = providers.e2b if desktop == "e2b" else providers.load(desktop)
    handle, env = providers.start(make, width, height, timeout_s)
    return env, (lambda: providers.close(handle)), providers.about(desktop) or desktop


def run(task: str, desktop: str | Env | None = None, *, model: str | None = None, base_url: str | None = None,
        api_key: str | None = None, provider: str | None = None, app: str | None = None, max_steps: int = 40, on_step: Callable | None = None,
        confirm: Any = "auto", timeout_s: float | None = None, cancel: threading.Event | None = None,
        max_cost_usd: float | None = None, record_dir: str | os.PathLike | None = None,
        **options: Any) -> RunResult:
    """Run one task with your saved model on your saved desktop (`legato setup`), or the ones given. `desktop` is a
    name ("background", "screen", "docker", "e2b"), a --sandbox spec, or an Env you opened yourself. The result has
    status (how it ended: done, failed, max_steps, timeout, cancelled, stopped or error), done, answer (what the
    model said at the end), error, usage and report() (where the time went). The task is sent with a note asking
    for the answer in done's text; when its words ask for information and the model calls done without one, it gets
    one more turn to give it (one more model call, in steps and usage).

    It returns rather than raises when the run goes wrong: a model call or a desktop that fails, mid-run or while
    opening, comes back with status "error" and `error` saying why, with the usage so far. A missing model or a bad
    argument (a desktop name that is none) still raises. timeout_s: a deadline for the whole call, opening the desktop
    and each model call included (status "timeout"); an app's launch, docker and E2B's create request are cut at the time left, your own
    --sandbox function and the first screenshot are not. cancel: a threading.Event that ends the run before its next
    model call once set (status "cancelled"). max_cost_usd: ends the run (status "max_cost") once the cost its model
    calls report reaches it, checked between turns; only providers that report cost count (OpenRouter does).
    record_dir: a folder for the screen after each turn (start.png, turn-001.png, ...); result.trace names them.

    model: a model's name picks where it runs when provider and base_url are not given: claude-... on Anthropic's
    API, gpt-... on OpenAI's, gemini-... on Google's, grok-... on xAI's, vendor/model on OpenRouter, each with that
    provider's key (`legato setup --add-key PROVIDER`, or ANTHROPIC_API_KEY, OPENAI_API_KEY, ...). provider: name it
    instead ("anthropic", "openai", "openrouter", ...).

    confirm: what happens before a risky action (keys such as cmd+Delete or cmd+Q, a typed rm or sudo: the list is in
    legato.safety) on this computer ("background", "screen"); other desktops ignore it, and an Env you pass keeps its
    own. "auto" (the default) asks yes/no in the terminal when stdin is one; in an unattended run (a server, a job,
    stdin not a terminal) it refuses them, and the model is told they were not allowed. A function
    confirm(description) -> bool decides each one: False skips the action and the model is told to find another
    way. None never asks and runs them all; legato.safety.deny refuses them all, even at a terminal."""
    t0 = time.monotonic()
    end = None if timeout_s is None else t0 + max(0.0, float(timeout_s))
    left = (lambda: None) if end is None else (lambda: max(0.0, end - time.monotonic()))
    if confirm == "auto":                         # nobody to ask: refuse, rather than run it unseen
        confirm = safety.default_confirm(unattended=safety.deny)
    if isinstance(desktop, Env):
        env, close = desktop, (lambda: None)
    else:
        from legato import providers
        try:
            env, close, _ = open_desktop(desktop, task=task, app=app, confirm=confirm, timeout_s=left())
        except providers.SpecError as e:
            why = f"cannot load {e.path}" if e.path else f"use {DESKTOPS}"
            raise ValueError(f"desktop={e.spec!r} is not a desktop: {why}") from None
        except (RuntimeError, OSError) as e:          # Docker stopped, no screen permission, an app that won't open
            if end is not None and time.monotonic() >= end:
                return RunResult(task, error=f"the run's time limit ({timeout_s:g} s) ran out while the desktop was "
                                             f"opening: {e}"[:300], status="timeout", wall_s=time.monotonic() - t0)
            return RunResult(task, error=f"cannot start the desktop: {e}"[:300], status="error",
                             wall_s=time.monotonic() - t0)
    try:
        agent = agent_for(env, model=model, base_url=base_url, api_key=api_key, provider=provider, **options)
        res = agent.run(task + ANSWER_NOTE, env, max_steps=max_steps, on_step=on_step, timeout_s=left(),
                        cancel=cancel, raise_desktop_errors=False, max_cost_usd=max_cost_usd, record_dir=record_dir)
        if res.status == "timeout" and end is not None:      # the limit the caller set, not the part left for the run
            res.error = f"the run's time limit ({timeout_s:g} s) ran out"
        res.task = task
        return res
    finally:
        close()
