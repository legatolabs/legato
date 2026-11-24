"""`--backend local`: rollouts on this computer's own screen, with the reset and the grading you supply.

    legato rl collect --backend local --backend-opts @local.json --harness legato --desktops 1 ...
    local.json: {"hooks": "my_hooks.py"}

my_hooks.py holds two functions:

    def reset(task: dict) -> None:      # put the screen in the task's start state (close and reopen the app, ...)
    def evaluate(task: dict) -> float:  # the reward of the attempt that just ended (read the file it saved, ...)

The policy moves the real mouse and types on the real keyboard (legato.local): one screen, so one worker. Actions on
the short list in legato.safety (a typed `rm`, cmd+Q...) are refused, not asked about. Moving the mouse into a corner
of the screen stops the batch.
"""
from __future__ import annotations

import importlib.util
import time
from pathlib import Path
from typing import Any

from legato.rl.collect import Desktop, SetupError

USAGE = ('--backend local needs --backend-opts \'{"hooks": "my_hooks.py"}\' (or @file.json): a file with '
         "reset(task) and evaluate(task) -> float (legato/rl/local.py)")


def load_hooks(path: str) -> Any:
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise SetupError(f"--backend local: no hooks file {p}. {USAGE}")
    spec = importlib.util.spec_from_file_location(f"legato_rl_hooks_{p.stem}", p)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:  # noqa: BLE001  the user's file: say what it raised, in one line
        raise SetupError(f"--backend local: cannot import {p}: {type(e).__name__}: {str(e)[:200]}") from e
    missing = [n for n in ("reset", "evaluate") if not callable(getattr(mod, n, None))]
    if missing:
        raise SetupError(f"--backend local: {p} has no {', '.join(missing)}(task). {USAGE}")
    return mod


def check(desktop: Any, harness: Any) -> None:
    """Raise SetupError when the local backend cannot run as configured."""
    if not desktop.options.get("hooks"):
        raise SetupError(USAGE)
    load_hooks(desktop.options["hooks"])
    if harness.name not in ("legato", "reference"):
        raise SetupError("--backend local runs parsed actions: pass --harness legato (or reference)")


class LocalDesktop(Desktop):
    """This computer's screen as an RL desktop. `hooks`: the path of the file with reset(task) and evaluate(task).
    `env`: a legato Env to drive instead of legato.local() (tests)."""

    def __init__(self, hooks: str, env: Any = None, **local_options: Any):
        self.hooks = load_hooks(hooks)
        if env is None:
            from legato import safety
            from legato.local import local
            env = local(confirm=safety.deny, **local_options)
        self.env, self.task = env, {}
        self.reward_source = f"{Path(hooks).name}:evaluate"

    def reset(self, task: dict) -> dict:
        t = time.perf_counter()
        self.task, self.instruction = task, task.get("instruction", f"do task {task['task_id']}")
        self.hooks.reset(task)
        return {"total_s": time.perf_counter() - t, "path": "local"}

    def observe(self) -> dict:
        return {"screenshot": self.env.observe().screenshot}

    def act(self, action: Any) -> tuple[dict, bool, dict]:
        from legato import safety
        a = getattr(action, "action", None)
        if str(action) in ("DONE", "FAIL"):
            return self.observe(), True, {}
        if a is None:                                 # the reference loop's screenshot action
            return self.observe(), False, {}
        t = time.perf_counter()
        try:
            obs = self.env.act(a)
        except safety.Denied:                         # a risky action: not run; the policy sees the screen unchanged
            return self.observe(), False, {}
        except safety.Stopped as e:
            raise SystemExit(f"legato rl: stopped by the person at this computer: {e}") from None
        return {"screenshot": obs.screenshot}, False, {"act_s": time.perf_counter() - t}

    def evaluate(self) -> float:
        return float(self.hooks.evaluate(self.task))
