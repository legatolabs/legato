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
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import subprocess
import threading
import time
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


DOCKER_IMAGE = "ghcr.io/legatolabs/desktop:latest"


DOCKER_SOURCE = "https://github.com/legatolabs/legato.git#main:docker/desktop"


_image_lock = threading.Lock()


def ensure_image(image: str) -> None:
    """Use the local image, else pull it; the default image is built from the repo if the pull fails. Concurrent calls
    wait for the first one."""
    with _image_lock:
        _ensure_image(image)


def _ensure_image(image: str) -> None:
    import shutil
    if not shutil.which("docker"):
        raise RuntimeError("--sandbox docker needs Docker: install Docker Desktop (macOS, Windows) or Docker Engine "
                           "(Linux), or use --sandbox local or e2b")
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        raise RuntimeError("Docker is installed but not running: start Docker Desktop (or the docker service), then "
                           "try again")
    if subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode == 0:
        return
    if subprocess.run(["docker", "pull", "-q", image], capture_output=True).returncode == 0:
        return
    if image != DOCKER_IMAGE:
        raise RuntimeError(f"docker image {image} is neither local nor pullable")
    import sys
    print(f"legato: building the desktop image from {DOCKER_SOURCE} (first use only, a few minutes)", file=sys.stderr)
    out = subprocess.run(["docker", "build", "-q", "-t", image, DOCKER_SOURCE], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"could not pull or build {image}: {out.stderr.strip()[-300:]}")


def docker(width: int, height: int, image: str | None = None) -> tuple[Any, Env]:
    """A fresh desktop container on this machine (needs Docker). The image needs an X display on :0 with python3,
    xdotool and scrot; ghcr.io/legatolabs/desktop has them, plus a terminal and Firefox. Apps running on your machine
    are reachable from inside at http://host.docker.internal:PORT."""
    image = image or os.environ.get("LEGATO_DOCKER_IMAGE") or DOCKER_IMAGE
    ensure_image(image)
    out = subprocess.run(["docker", "run", "-d", "--rm", "--shm-size", "512m", "-e", f"RESOLUTION={width}x{height}x24",
                          "--add-host", "host.docker.internal:host-gateway",   # reach apps on your machine
                          image], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"docker run {image} failed: {out.stderr.strip()[-300:]}")
    cid = out.stdout.strip()

    def run(cmd: str) -> str:
        return subprocess.run(["docker", "exec", cid, "sh", "-c", cmd], capture_output=True, text=True).stdout

    def stop() -> None:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True)

    for _ in range(100):                          # wait for the X server
        if subprocess.run(["docker", "exec", cid, "sh", "-c", "xdpyinfo -display :0 >/dev/null 2>&1"]).returncode == 0:
            break
        time.sleep(0.1)
    else:
        stop()
        raise RuntimeError(f"no X display came up in {image}")
    return stop, attach(shell=run)


BUILTIN: dict[str, Factory] = {"e2b": e2b, "local": local, "docker": docker}


def load(spec: str) -> Factory:
    """`e2b`, `local`, `docker[:IMAGE]`, `path/to/file.py:function` or `module:function`, as a function
    (width, height) -> (handle, Env)."""
    if spec in BUILTIN:
        return BUILTIN[spec]
    if spec.startswith("docker:"):
        image = spec[len("docker:"):]
        return lambda width, height: docker(width, height, image)
    where, sep, name = spec.rpartition(":")
    if not sep or not where or not name:
        raise ValueError(f"--sandbox {spec!r}: use e2b, local, docker, docker:IMAGE, path/to/file.py:function or "
                         "module:function")
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


# What an agent should know about each kind of desktop (the MCP server adds it to its instructions).
ABOUT = {
    "docker": "The desktop is a Linux container (Debian, fluxbox) with a terminal open; start apps from it, for example "
              "`firefox http://host.docker.internal:3000 &`. host.docker.internal is the user's machine, so apps they "
              "run locally are reachable at http://host.docker.internal:PORT. Right-click the desktop for a menu.",
    "local": "The desktop is the user's own screen, with their real windows, files and accounts. Act carefully: do not "
             "close their windows or change settings unless asked, and ask before anything destructive or irreversible.",
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
