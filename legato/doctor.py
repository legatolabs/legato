"""`legato doctor`: what is installed and configured, and what to do about what is missing.

    legato doctor            # a short report
    legato doctor --json     # the same as one JSON object, for coding agents and scripts

Besides what is installed, it tries the two things that fail at run time: on a Mac, one real window capture, as
background mode takes it (it can stall); and with a model on this machine, whether its server answers.
"""
from __future__ import annotations

import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from typing import Any, Callable


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


def model_server(base_url: str, model: str = "", timeout_s: float = 1.5) -> dict:
    """Does the model server at `base_url` (one on this machine) answer `/models`? {"ok", "url", "seconds",
    "models", "note"} or {"ok": False, "url", "error"}. Any HTTP answer counts: a server that is up."""
    import urllib.error
    import urllib.request
    url = base_url.rstrip("/") + "/models"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))      # this machine: never a proxy
    t = time.monotonic()
    try:
        with opener.open(url, timeout=timeout_s) as r:
            data = json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return {"ok": True, "url": url, "seconds": round(time.monotonic() - t, 2), "models": [],
                "note": f"/models answered HTTP {e.code}"}
    except (OSError, ValueError) as e:            # refused, timed out, not JSON
        return {"ok": False, "url": url, "error": str(getattr(e, "reason", None) or e)[:200]}
    items = data.get("data", data) if isinstance(data, dict) else data
    items = items if isinstance(items, list) else []
    ids = [m["id"] for m in items if isinstance(m, dict) and isinstance(m.get("id"), str)]
    out: dict = {"ok": True, "url": url, "seconds": round(time.monotonic() - t, 2), "models": ids[:20]}
    if model and ids and model not in ids:
        out["note"] = f"it does not list {model}; it lists {', '.join(ids[:5])}"
    return out


def _windows() -> list[dict]:
    """This Mac's on-screen app windows, front to back: {"id", "app", "rect"}."""
    import Quartz as q
    info = q.CGWindowListCopyWindowInfo(q.kCGWindowListOptionOnScreenOnly | q.kCGWindowListExcludeDesktopElements,
                                        q.kCGNullWindowID) or []
    out = []
    for w in info:
        b = w.get("kCGWindowBounds") or {}
        if w.get("kCGWindowLayer") == 0 and w.get("kCGWindowAlpha", 1) > 0 and b.get("Width", 0) > 40 \
                and b.get("Height", 0) > 40:
            out.append({"id": int(w["kCGWindowNumber"]), "app": str(w.get("kCGWindowOwnerName") or ""),
                        "rect": (float(b["X"]), float(b["Y"]), float(b["Width"]), float(b["Height"]))})
    return out


def _api_capture(win: dict) -> Any:
    """The window through macOS's window capture API, as background mode takes it (None if it gave nothing)."""
    import Quartz as q
    img = q.CGWindowListCreateImageFromArray(q.CGRectMake(*win["rect"]), [win["id"]],
                                             q.kCGWindowImageBoundsIgnoreFraming | q.kCGWindowImageNominalResolution)
    return img if img is not None and q.CGImageGetWidth(img) > 0 else None


def capture_state(api: Callable[[], Any], fallback: Callable[[], bytes | None], wait_s: float) -> dict:
    """"works" (the API answered within `wait_s`), "stalled" (it did not, and `screencapture` works: Legato uses
    that, more slowly) or "failed" (neither), with the fix."""
    from legato.background import _within
    t = time.monotonic()
    img = _within(wait_s, api)
    s = time.monotonic() - t
    if img is not None:
        return {"ok": True, "state": "works", "seconds": round(s, 2), "fix": None}
    why = (f"macOS's window capture did not answer within {wait_s:g} s" if s >= wait_s * 0.9
           else "macOS's window capture returned nothing")
    t = time.monotonic()
    png = fallback()
    s = time.monotonic() - t
    if png:
        return {"ok": True, "state": "stalled", "seconds": round(s, 2), "why": why,
                "fix": f"Legato falls back to screencapture, which is slower ({s:.1f} s a screenshot here) and leaves "
                       "out the window's sheets and menus. Quit screen recorders and other apps that capture the "
                       "screen; if it stays stalled, restart the Mac"}
    return {"ok": False, "state": "failed", "why": why + ", and screencapture failed too",
            "fix": "check Screen Recording for the app that runs Legato (System Settings → Privacy & Security); if "
                   "it is on, quit other screen recorders, or restart the Mac"}


def window_capture(app: str = "", perms: dict | None = None) -> dict:
    """One real window capture with background mode's limit: `app`'s window if it is on screen, else the front one.
    Nothing is kept. Not tried while Screen Recording is known to be off (that would ask for it)."""
    rec = (perms or {}).get("screen_recording") or {}
    if rec.get("ok") is False:
        return {"ok": False, "state": "failed", "why": "Screen Recording is off", "fix": rec.get("fix")}
    try:
        wins = _windows()
        from legato.background import MacApp, _screencapture
    except ImportError:
        return {"ok": None, "state": "not checked", "fix": 'pip install "legatolabs[local]"'}
    win = next((w for w in wins if app and w["app"] == app), None) or (wins[0] if wins else None)
    if win is None:
        return {"ok": None, "state": "not checked", "fix": None, "why": "no window on screen to capture"}
    size = (int(win["rect"][2]), int(win["rect"][3]))
    out = capture_state(lambda: _api_capture(win), lambda: _screencapture(win["id"], size), MacApp.API_WAIT_S)
    return {**out, "window": f"{win['app']}'s window"}


def checks(capture: bool = False) -> dict:
    """What is installed and set up. capture=True also takes one real window capture on a Mac (a few seconds at
    most); a model server on this machine is always asked whether it answers (1.5 s at most)."""
    from legato import __version__
    local_ok = _has("mss") and _has("pyautogui")
    display = sys.platform in ("darwin", "win32") or bool(os.environ.get("DISPLAY"))
    from legato import safety
    perms = safety.permissions()                 # macOS: Accessibility and Screen Recording (read-only checks)
    denied = safety.permission_problems(perms)
    out = {
        "legato": __version__,
        "python": platform.python_version(),
        "platform": sys.platform,
        "sandboxes": {
            "docker": _docker(),
            "local": {"ok": local_ok and display and not denied,
                      "fix": ('pip install "legatolabs[local]"' if not local_ok else
                              "no display: run inside a desktop session" if not display else
                              "macOS permissions are off (below)" if denied else None),
                      "permissions": perms},
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
    from legato import config
    cfg = config.load()
    ok, fix, server = cfg.ready, cfg.missing(), None
    if cfg.base_url and config.is_local_url(cfg.base_url):
        server = model_server(cfg.base_url, cfg.model)
        if not server["ok"]:
            ok = False
            fix = fix or (f"no model server answers at {cfg.base_url} ({server['error']}): start it (vllm serve, "
                          "ollama serve, or LM Studio's server), or run legato setup to choose another model")
    out["setup"] = {"ok": ok, "model": cfg.model or None, "base_url": cfg.base_url or None,
                    "desktop": cfg.desktop or config.default_desktop(), "app": cfg.app or None,
                    "config": str(config.path()), "fix": fix, "server": server}
    if capture and sys.platform == "darwin":
        cap = out["window_capture"] = window_capture(cfg.app, perms)
        local = out["sandboxes"]["local"]
        if cap["ok"] is False and local["ok"]:
            local["ok"], local["fix"] = False, "window capture failed (below)"
    return out


def main(argv=None) -> int:
    argv = list(argv or [])
    if {"-h", "--help", "help"} & set(argv):
        print(__doc__.strip())
        return 0
    c = checks(capture=True)
    if "--json" in argv:
        print(json.dumps(c))
        return 0
    print(f"legato {c['legato']}, Python {c['python']} on {c['platform']}")
    st = c["setup"]
    print(f"  {'ok ' if st['ok'] else 'no '} setup: " + (f"{st['model']} · {st['desktop']}" + (f" ({st['app']})" if st["app"] else "")
                                                       if st["ok"] else st["fix"]))
    if st["ok"] and st.get("server"):
        sv = st["server"]
        print(f"      ok  model server answers at {st['base_url']} ({sv['seconds']} s)"
              + (f"; {sv['note']}" if sv.get("note") else ""))
    for name, s in [*c["sandboxes"].items(), ("mcp", c["mcp"])]:
        line = f"  {'ok ' if s['ok'] else 'no '} {name}"
        if s.get("fix"):
            line += f": {s['fix']}"
        elif s.get("note"):
            line += f" ({s['note']})"
        print(line)
        for perm, p in (s.get("permissions") or {}).items():
            state = {True: "ok ", False: "no "}.get(p["ok"], "?  ")
            print(f"      {state} {perm.replace('_', ' ')}" + (f": {p['fix']}" if p["fix"] else ""))
        if name == "local" and c.get("window_capture"):
            print("      " + _capture_line(c["window_capture"]))
    keys = ", ".join(c["model_keys"]) or "none set"
    print(f"  model keys: {keys}" + (f" ({c['model_note']})" if c["model_note"] else ""))
    return 0


def _capture_line(cap: dict) -> str:
    where = f", {cap['window']}" if cap.get("window") else ""
    if cap["state"] == "works":
        return f"ok  window capture: works ({cap['seconds']} s{where})"
    if cap["state"] == "stalled":
        return f"!   window capture: stalled{where}: {cap['why']}. {cap['fix']}"
    if cap["state"] == "failed":
        return f"no  window capture: failed{where}: {cap['why']}. Fix: {cap['fix']}"
    return "?   window capture: not checked" + (f" ({cap['why']})" if cap.get("why") else "") \
        + (f": {cap['fix']}" if cap.get("fix") else "")
