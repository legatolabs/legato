"""`legato doctor`: what is installed and configured, and what to do about what is missing.

    legato doctor            # a short report
    legato doctor --json     # the same as one JSON object, for coding agents and scripts
    legato doctor --model    # also try your saved model: does it answer, take images, make tool calls, and where
                             # does its click land on a test image (one small paid call, three at most)
    legato doctor --model NAME [--base-url URL]    # the same for another model, with your saved key

Besides what is installed, it tries the two things that fail at run time: on a Mac, one real window capture, as
background mode takes it (it can stall); and with a model on this machine, whether its server answers.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
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
    system = {"Window Server", "Dock", "Control Center", "Notification Center", "SystemUIServer", "Wallpaper"}
    ordinary = [w for w in wins if w["app"] not in system]
    tries = ([w for w in ordinary if app and w["app"] == app] + [w for w in ordinary if w["app"] != app])[:3]
    if not tries:
        return {"ok": None, "state": "not checked", "fix": None, "why": "no window on screen to capture"}
    out: dict = {}
    for win in tries:            # a window `screencapture` can't take (a panel, an overlay) is no verdict on the Mac
        size = (int(win["rect"][2]), int(win["rect"][3]))
        wait = MacApp.API_WAIT_S if not out else 0.5    # the API has had its chance on the first window
        out = {**capture_state(lambda: _api_capture(win), lambda: _screencapture(win["id"], size), wait),
               "window": f"{win['app']}'s window"}
        if out["ok"]:
            break
    return out


def checks(capture: bool = False, model: str | None = None, base_url: str | None = None) -> dict:
    """What is installed and set up. capture=True also takes one real window capture on a Mac (a few seconds at
    most); a model server on this machine is always asked whether it answers (1.5 s at most). model: also try that
    model ("" for the saved one) with legato.config.check_model, at base_url or the saved server ("model_check")."""
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
    # the `legato` command itself: pip can put it in a folder that is not on PATH (always so for --user on Windows)
    import shutil
    import sysconfig
    if shutil.which("legato"):
        out["command"] = {"ok": True, "fix": None}
    else:
        scripts = [sysconfig.get_path("scripts")]
        try:
            scripts.insert(0, sysconfig.get_path("scripts", f"{os.name}_user"))
        except KeyError:
            pass
        where = next((s for s in scripts if s and (Path(s) / ("legato.exe" if sys.platform == "win32" else "legato")).exists()), scripts[0])
        out["command"] = {"ok": False, "fix": f"`legato` is not on PATH: run it as `{Path(sys.executable).name.replace('.exe', '')} -m legato`, "
                                              f"or add {where} to PATH"}
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
                    "config": str(config.path()), "fix": fix, "server": server,
                    "saved": config.path().exists(), "default_from": cfg.default_from or None,
                    "coords": cfg.coords or None, "tool_calls": cfg.tool_calls or None}
    if model is not None:
        name, url = model or cfg.model, base_url or cfg.base_url
        if not name or not url:
            out["model_check"] = {"ok": False, "model": name or None, "answers": False,
                                  "error": "no model to try", "fix": fix or "run legato setup, or pass --model NAME"}
        else:
            same = url.rstrip("/") == cfg.base_url.rstrip("/")
            if not (cfg.key if same else os.environ.get("LEGATO_API_KEY", "")) and not config.is_local_url(url):
                out["model_check"] = {"ok": False, "model": name, "answers": False, "error": "no key",
                                      "fix": cfg.missing() if same else f"{config.set_env('LEGATO_API_KEY')} (the key for {url})"}
            else:
                out["model_check"] = config.check_model(name, url, cfg.key if same else os.environ.get("LEGATO_API_KEY", ""))
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
    import argparse
    ap = argparse.ArgumentParser(prog="legato doctor", add_help=False)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--model", nargs="?", const="")
    ap.add_argument("--base-url")
    a = ap.parse_args(argv)
    c = checks(capture=True, model=a.model, base_url=a.base_url)
    mc = c.get("model_check")
    if a.json:
        print(json.dumps(c))
        return 0 if mc is None or mc["ok"] else 1
    from legato.config import check_lines, dot
    print(f"legato {c['legato']}, Python {c['python']} on {c['platform']}")
    st = c["setup"]
    if st["ok"] and st["default_from"]:        # nothing saved: a key in the environment picked the default model
        what = (f"nothing saved yet; using {st['default_from']} from the environment with the default model "
                f"({st['model']}), on {st['desktop']}. legato setup saves a choice")
    elif st["ok"]:
        what = f"{st['model']}{dot()}{st['desktop']}" + (f" ({st['app']})" if st["app"] else "")
        what += "".join(f", {k.replace('_', ' ')}: {st[k]}" for k in ("coords", "tool_calls") if st[k])
    else:
        what = st["fix"]
    print(f"  {'ok ' if st['ok'] else 'no '} setup: {what}")
    if st["ok"] and st.get("server"):
        sv = st["server"]
        print(f"      ok  model server answers at {st['base_url']} ({sv['seconds']} s)"
              + (f"; {sv['note']}" if sv.get("note") else ""))
    if mc is not None:
        print(f"  {'ok ' if mc['ok'] else 'no '} model: {mc.get('model') or 'none'}")
        for line in check_lines(mc):
            print(f"      {line}")
    elif st["ok"]:
        print("      the model was not tried: legato doctor --model tries it (one small call)")
    if not c["command"]["ok"]:
        print(f"  no  command: {c['command']['fix']}")
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
    return 0 if mc is None or mc["ok"] else 1


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
