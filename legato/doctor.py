"""`legato doctor`: what is installed and configured, and what to do about what is missing.

    legato doctor            # a short report
    legato doctor --json     # the same as one JSON object, for coding agents and scripts
"""
from __future__ import annotations

import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys


def _has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def _docker() -> dict:
    if not shutil.which("docker"):
        return {"ok": False, "fix": "install Docker to use --sandbox docker"}
    up = subprocess.run(["docker", "info"], capture_output=True).returncode == 0
    if not up:
        return {"ok": False, "fix": "start the Docker daemon (Docker Desktop on macOS and Windows)"}
    from legato.providers import DOCKER_IMAGE
    have = subprocess.run(["docker", "image", "inspect", DOCKER_IMAGE], capture_output=True).returncode == 0
    return {"ok": True, "image": DOCKER_IMAGE, "image_present": have,
            "note": None if have else "the first --sandbox docker pulls or builds the image, which takes a few minutes"}


def checks() -> dict:
    from legato import __version__
    local_ok = _has("mss") and _has("pyautogui")
    display = sys.platform in ("darwin", "win32") or bool(os.environ.get("DISPLAY"))
    out = {
        "legato": __version__,
        "python": platform.python_version(),
        "platform": sys.platform,
        "sandboxes": {
            "docker": _docker(),
            "local": {"ok": local_ok and display,
                      "fix": None if local_ok and display else
                      ('pip install "legatolabs[local]"' if not local_ok else "no display: run inside a desktop session")},
            "e2b": {"ok": _has("e2b_desktop") and bool(os.environ.get("E2B_API_KEY")),
                    "fix": None if _has("e2b_desktop") and os.environ.get("E2B_API_KEY") else
                    ('pip install "legatolabs[e2b]"' if not _has("e2b_desktop") else "export E2B_API_KEY (e2b.dev)")},
        },
        "mcp": {"ok": _has("mcp"), "fix": None if _has("mcp") else 'pip install "legatolabs[mcp]"'},
        "model_keys": sorted(k for k in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "TOGETHER_API_KEY",
                                         "FIREWORKS_API_KEY") if os.environ.get(k)),
    }
    out["model_note"] = ("a local model server (--base-url http://localhost:8000/v1) needs no key"
                         if not out["model_keys"] else None)
    return out


def main(argv=None) -> int:
    argv = list(argv or [])
    c = checks()
    if "--json" in argv:
        print(json.dumps(c))
        return 0
    print(f"legato {c['legato']}, Python {c['python']} on {c['platform']}")
    for name, s in [*c["sandboxes"].items(), ("mcp", c["mcp"])]:
        line = f"  {'ok ' if s['ok'] else 'no '} {name}"
        if s.get("fix"):
            line += f": {s['fix']}"
        elif s.get("note"):
            line += f" ({s['note']})"
        print(line)
    keys = ", ".join(c["model_keys"]) or "none set"
    print(f"  model keys: {keys}" + (f" ({c['model_note']})" if c["model_note"] else ""))
    return 0
