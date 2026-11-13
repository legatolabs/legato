"""Where `legato run --sandbox` and `legato mcp --sandbox` get a desktop.

    --sandbox e2b                      a fresh E2B Desktop (the default; needs E2B_API_KEY)
    --sandbox local                    this computer's screen (pip install "legatolabs[local]")
    --sandbox my_desktop.py:desktop    your own: a function (width, height) -> legato Env, or (handle, Env)
    --sandbox mypkg.desktops:desktop   the same, from an importable module

Your function can return any `legato.attach(...)` result. If it also returns a handle, Legato calls the handle's
`kill()`, `close()`, `stop()` or `delete()`, or calls the handle itself if it is a function, when the desktop is
closed.
"""
from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
from typing import Any, Callable

from legato.env import Env, attach

Factory = Callable[[int, int], "tuple[Any, Env]"]


def e2b(width: int, height: int) -> tuple[Any, Env]:
    from e2b_desktop import Sandbox
    sb = Sandbox.create(resolution=(width, height), timeout=3600)
    return sb, attach(sb)


def local(width: int, height: int) -> tuple[Any, Env]:
    from legato.local import local as this_computer
    return None, this_computer()


BUILTIN: dict[str, Factory] = {"e2b": e2b, "local": local}


def load(spec: str) -> Factory:
    """`e2b`, `local`, `path/to/file.py:function` or `module:function`, as a function (width, height) -> (handle, Env)."""
    if spec in BUILTIN:
        return BUILTIN[spec]
    where, sep, name = spec.rpartition(":")
    if not sep or not where or not name:
        raise ValueError(f"--sandbox {spec!r}: use e2b, local, path/to/file.py:function or module:function")
    if where.endswith(".py"):
        path = Path(where).expanduser().resolve()
        mod_spec = importlib.util.spec_from_file_location(path.stem, path)
        if mod_spec is None or mod_spec.loader is None:
            raise ValueError(f"--sandbox {spec!r}: cannot load {path}")
        mod = importlib.util.module_from_spec(mod_spec)
        mod_spec.loader.exec_module(mod)
    else:
        mod = importlib.import_module(where)
    fn = getattr(mod, name)

    def factory(width: int, height: int) -> tuple[Any, Env]:
        out = fn(width, height)
        if isinstance(out, Env):
            return None, out
        handle, env = out
        return handle, env
    return factory


def close(handle: Any) -> None:
    for name in ("kill", "close", "stop", "delete"):
        fn = getattr(handle, name, None)
        if callable(fn):
            fn()
            return
    if callable(handle):                       # a plain cleanup function
        handle()
