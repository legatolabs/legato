"""Where `legato run --sandbox` and `legato mcp --sandbox` get a desktop.

    --sandbox e2b                      a fresh E2B Desktop (the default; needs E2B_API_KEY)
    --sandbox local                    this computer's screen (pip install "legatolabs[local]")
    --sandbox docker                   a fresh desktop container on this machine (ghcr.io/legatolabs/desktop)
    --sandbox docker:IMAGE             the same with your own image (an X display with python3, xdotool, scrot)
    --sandbox my_desktop.py:desktop    your own: a function (width, height) -> legato Env, or (handle, Env)
    --sandbox mypkg.desktops:desktop   the same, from an importable module

Your function can return any `legato.attach(...)` result. If it also returns a handle, Legato calls the handle's
`kill()`, `close()`, `stop()` or `delete()`, or calls the handle itself if it is a function, when the desktop is
closed.

The built-in desktops raise legato.DesktopStartError (a RuntimeError) when they cannot start. docker and e2b take
`timeout_s`, a limit on starting (`start()` passes it to the factories that take it).
"""
from __future__ import annotations

import importlib
import importlib.util
import inspect
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from legato.env import DesktopStartError, Env, attach

Factory = Callable[[int, int], "tuple[Any, Env]"]


class SpecError(ValueError):
    """A desktop spec that names no desktop (`--sandbox nonsense`). `spec` is what was given; `path` the file that
    could not be loaded, if that was the problem."""

    def __init__(self, spec: str, message: str, path: str = ""):
        super().__init__(message)
        self.spec, self.path = spec, path


def e2b(width: int, height: int, *, timeout_s: float | None = None) -> tuple[Any, Env]:
    from e2b_desktop import Sandbox
    kw: dict = {}
    try:                                       # the SDK's limit on its create request, where it has one
        if timeout_s is not None and "request_timeout" in inspect.signature(Sandbox.create).parameters:
            kw["request_timeout"] = max(1.0, timeout_s)
    except (TypeError, ValueError):
        pass
    try:
        sb = Sandbox.create(resolution=(width, height), timeout=3600, **kw)
    except Exception as e:  # noqa: BLE001 - a bad key, no quota, the network: one typed error with E2B's reason
        raise DesktopStartError(f"the E2B desktop could not start: {type(e).__name__}: {e}") from e
    return sb, attach(sb)


def local(width: int, height: int) -> tuple[Any, Env]:
    from legato.local import local as this_computer
    return None, this_computer()


def start(factory: Factory, width: int, height: int, timeout_s: float | None = None) -> tuple[Any, Env]:
    """factory(width, height), with timeout_s for the factories that take it (docker, e2b); others are not bounded."""
    if timeout_s is not None:
        try:
            takes = "timeout_s" in inspect.signature(factory).parameters
        except (TypeError, ValueError):
            takes = False
        if takes:
            return factory(width, height, timeout_s=timeout_s)
    return factory(width, height)


DOCKER_IMAGE = "ghcr.io/legatolabs/desktop:latest"


DOCKER_SOURCE = "https://github.com/legatolabs/legato.git#main:docker/desktop"


_image_lock = threading.Lock()


class _Clock:
    """Time left before a start limit (None: no limit); past it, DesktopStartError."""

    def __init__(self, what: str, timeout_s: float | None):
        self.what, self.timeout_s = what, timeout_s
        self.end = None if timeout_s is None else time.monotonic() + timeout_s

    def left(self) -> float | None:
        if self.end is None:
            return None
        t = self.end - time.monotonic()
        if t <= 0:
            raise DesktopStartError(f"{self.what} did not start within {self.timeout_s:g} s")
        return t

    def run(self, args: list, **kw) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(args, timeout=self.left(), **kw)
        except subprocess.TimeoutExpired:
            raise DesktopStartError(f"{self.what} did not start within {self.timeout_s:g} s") from None


def ensure_image(image: str, timeout_s: float | None = None) -> None:
    """Use the local image, else pull it; the default image is built from the repo if the pull fails. Concurrent calls
    wait for the first one. Raises DesktopStartError (Docker missing or stopped, no image, past timeout_s)."""
    _ensure_within(image, _Clock("the docker desktop", timeout_s))


def _ensure_within(image: str, clock: _Clock) -> None:
    left = clock.left()
    if not _image_lock.acquire(timeout=-1 if left is None else left):
        raise DesktopStartError(f"{clock.what} did not start within {clock.timeout_s:g} s (waiting for its image)")
    try:
        _ensure_image(image, clock)
    finally:
        _image_lock.release()


def _ensure_image(image: str, clock: _Clock) -> None:
    import shutil
    if not shutil.which("docker"):
        raise DesktopStartError("the docker desktop needs Docker: install Docker Desktop (macOS, Windows) or Docker "
                                "Engine (Linux), or use another desktop (local or e2b)")
    if clock.run(["docker", "info"], capture_output=True).returncode != 0:
        raise DesktopStartError("Docker is installed but not running: start Docker Desktop (or the docker service), "
                                "then try again")
    if clock.run(["docker", "image", "inspect", image], capture_output=True).returncode == 0:
        return
    if clock.run(["docker", "pull", "-q", image], capture_output=True).returncode == 0:
        return
    if image != DOCKER_IMAGE:
        raise DesktopStartError(f"docker image {image} is neither local nor pullable")
    import sys
    print(f"legato: building the desktop image from {DOCKER_SOURCE} (first use only, a few minutes)", file=sys.stderr)
    out = clock.run(["docker", "build", "-q", "-t", image, DOCKER_SOURCE], capture_output=True, text=True)
    if out.returncode != 0:
        raise DesktopStartError(f"could not pull or build {image}: {out.stderr.strip()[-300:]}")


def docker(width: int, height: int, image: str | None = None, *, timeout_s: float | None = None) -> tuple[Any, Env]:
    """A fresh desktop container on this machine (needs Docker). The image needs an X display on :0 with python3,
    xdotool and scrot; ghcr.io/legatolabs/desktop has them, plus a terminal and Firefox. Apps running on your machine
    are reachable from inside at http://host.docker.internal:PORT. LEGATO_DOCKER_ARGS adds `docker run` arguments,
    for example a volume with your project. timeout_s: a limit on starting it, image pull included
    (DesktopStartError after it, as for any failure to start)."""
    image = image or os.environ.get("LEGATO_DOCKER_IMAGE") or DOCKER_IMAGE
    clock = _Clock("the docker desktop", timeout_s)
    _ensure_within(image, clock)
    import shlex
    extra = shlex.split(os.environ.get("LEGATO_DOCKER_ARGS", ""))   # e.g. -v "$PWD:/home/user/project"
    out = clock.run(["docker", "run", "-d", "--rm", "--shm-size", "512m", "-e", f"RESOLUTION={width}x{height}x24",
                     "--add-host", "host.docker.internal:host-gateway",   # reach apps on your machine
                     *extra, image], capture_output=True, text=True)
    if out.returncode != 0:
        raise DesktopStartError(f"docker run {image} failed: {out.stderr.strip()[-300:]}")
    cid = out.stdout.strip()

    def run(cmd: str) -> str:
        return subprocess.run(["docker", "exec", cid, "sh", "-c", cmd], capture_output=True, text=True).stdout

    def stop() -> None:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True)

    try:
        for _ in range(100):                      # wait for the X server
            if clock.run(["docker", "exec", cid, "sh", "-c", "xdpyinfo -display :0 >/dev/null 2>&1"]).returncode == 0:
                break
            time.sleep(0.1)
        else:
            raise DesktopStartError(f"no X display came up in {image}")
        for _ in range(80):                       # and for the first window, so the first screenshot is not black
            if clock.run(["docker", "exec", cid, "sh", "-c", "xdotool search --onlyvisible --name . 2>/dev/null | "
                          "head -1"], capture_output=True, text=True).stdout.strip():
                time.sleep(0.5)
                break
            time.sleep(0.1)
    except BaseException:
        stop()
        raise
    return stop, attach(shell=run)


BUILTIN: dict[str, Factory] = {"e2b": e2b, "local": local, "docker": docker}


def load(spec: str) -> Factory:
    """`e2b`, `local`, `docker[:IMAGE]`, `path/to/file.py:function` or `module:function`, as a function
    (width, height) -> (handle, Env)."""
    if spec in BUILTIN:
        return BUILTIN[spec]
    if spec.startswith("docker:"):
        image = spec[len("docker:"):]
        return lambda width, height, timeout_s=None: docker(width, height, image, timeout_s=timeout_s)
    where, sep, name = spec.rpartition(":")
    if not sep or not where or not name:
        raise SpecError(spec, f"--sandbox {spec!r}: use e2b, local, docker, docker:IMAGE, path/to/file.py:function or "
                              "module:function")
    if where.endswith(".py"):
        path = Path(where).expanduser().resolve()
        mod_spec = importlib.util.spec_from_file_location(path.stem, path)
        if mod_spec is None or mod_spec.loader is None:
            raise SpecError(spec, f"--sandbox {spec!r}: cannot load {path}", str(path))
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


# What an agent should know about each kind of desktop (the MCP server adds it to its instructions).
ABOUT = {
    "docker": "The desktop is a Linux container (Debian, fluxbox) with a terminal open; start apps from it, for example "
              "`firefox http://host.docker.internal:3000 &`. host.docker.internal is the user's machine, so apps they "
              "run locally are reachable at http://host.docker.internal:PORT. Right-click the desktop for a menu.",
    "local": "The desktop is the user's own screen, with their real windows, files and accounts. Act carefully: do not "
             "close their windows or change settings unless asked, and ask before anything destructive or irreversible.",
    "background": "The desktop is one app's window on the user's Mac, worked in the background: the user keeps using "
                  "their Mac meanwhile. desktop_open(app=...) picks the app (default: their browser; url= opens a "
                  "page in it). Clicks press, focus or select what is under them; drags are not possible, so use "
                  "the keyboard for those. Do not close their windows or change settings unless asked.",
    "e2b": "The desktop is a fresh E2B cloud desktop (Linux, XFCE); the dock at the bottom has a terminal, a file "
           "manager and a web browser.",
}


def about(spec: str) -> str:
    return ABOUT.get("docker" if spec.startswith("docker") else spec, "")


def warm(spec: str) -> None:
    """Start slow one-time setup in the background, so the first desktop opens sooner (docker: pull or build the
    image)."""
    if spec == "docker" or spec.startswith("docker:"):
        image = spec[len("docker:"):] if spec.startswith("docker:") else (os.environ.get("LEGATO_DOCKER_IMAGE")
                                                                           or DOCKER_IMAGE)

        def go() -> None:
            try:
                ensure_image(image)
            except Exception:  # noqa: BLE001 - desktop_open reports it
                pass
        threading.Thread(target=go, daemon=True).start()


def close(handle: Any) -> None:
    for name in ("kill", "close", "stop", "delete"):
        fn = getattr(handle, name, None)
        if callable(fn):
            fn()
            return
    if callable(handle):                       # a plain cleanup function
        handle()
