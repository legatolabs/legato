"""`legato setup`: choose your model and where Legato works, add it to your coding agents, and watch a first task.

    legato setup                       # asks a few questions (about two minutes), then offers a first task
    legato setup --yes                 # no questions: keys already in the environment and the defaults
    legato setup --yes --provider anthropic --api-key - < key.txt --desktop screen --mcp claude,codex
    legato setup --json                # what is set up, as one JSON object (for coding agents)

What it saves (~/.config/legato/config.json, see legato/config.py) is what `legato run`, `legato mcp` and
`legato.run()` use. Run it again any time to change it.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from legato import config as C

DOCS = "https://legato.computer/docs"
FIRST_TASK = "On Wikipedia, look up the Eiffel Tower and tell me how tall it is"
CLIENTS = ("claude", "codex", "cursor", "claude-desktop")


# ------------------------------------------------------------ asking
def _ask(prompt: str, choices: list[str], default: str, describe=lambda c: "") -> str:
    print(prompt)
    for i, c in enumerate(choices, 1):
        print(f"  {i}. {c}" + (f": {describe(c)}" if describe(c) else "") + ("  (default)" if c == default else ""))
    while True:
        got = input(f"[{choices.index(default) + 1}] ").strip()
        if not got:
            return default
        if got.isdigit() and 1 <= int(got) <= len(choices):
            return choices[int(got) - 1]
        if got in choices:
            return got
        print(f"  type 1 to {len(choices)}")


def _text(prompt: str, default: str = "") -> str:
    got = input(f"{prompt}" + (f" [{default}]" if default else "") + ": ").strip()
    return got or default


def _yes(prompt: str, default: bool = True) -> bool:
    got = input(f"{prompt} [{'Y/n' if default else 'y/N'}] ").strip().lower()
    return default if not got else got.startswith("y")


# ------------------------------------------------------------ coding agents
def legato_command() -> list[str]:
    """How another program starts this Legato: the `legato` next to this Python, or `uvx` when this is a throwaway
    uvx environment, or this Python with -m."""
    here = Path(sys.executable).parent / ("legato.exe" if sys.platform == "win32" else "legato")
    if "/uv/archive-" in sys.prefix or "/.cache/uv/" in sys.prefix or "\\uv\\cache\\" in sys.prefix:
        return ["uvx", "--from", "legatolabs", "legato"]
    if here.exists():
        return [str(here)]
    return [sys.executable, "-m", "legato"]


def _cursor_file() -> Path:
    return Path.home() / ".cursor" / "mcp.json"


def _claude_desktop_file() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def coding_agents() -> list[str]:
    """The MCP clients found on this machine: Claude Code and Codex (their command), Cursor and Claude Desktop (their
    config folder)."""
    found = [c for c in ("claude", "codex") if shutil.which(c)]
    if _cursor_file().parent.exists():
        found.append("cursor")
    if _claude_desktop_file().parent.exists():
        found.append("claude-desktop")
    return found


def _merge_json(file: Path, cmd: list[str]) -> None:
    data = {}
    if file.exists():
        try:
            data = json.loads(file.read_text() or "{}")
        except json.JSONDecodeError as e:
            raise RuntimeError(f"{file} is not valid JSON, so it was left alone: {e}") from None
    data.setdefault("mcpServers", {})["legato"] = {"command": cmd[0], "args": cmd[1:] + ["mcp"]}
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(data, indent=2) + "\n")


def add_mcp(client: str, cmd: list[str] | None = None, run=None) -> str:
    """Add Legato's MCP server to one coding agent (user-wide where it has that), replacing an older entry.
    Returns what was done, as one line."""
    cmd = cmd or legato_command()
    run = run or subprocess.run
    if client == "claude":
        run(["claude", "mcp", "remove", "legato", "-s", "user"], capture_output=True, text=True)
        r = run(["claude", "mcp", "add", "legato", "-s", "user", "--", *cmd, "mcp"], capture_output=True, text=True)
    elif client == "codex":
        run(["codex", "mcp", "remove", "legato"], capture_output=True, text=True)
        r = run(["codex", "mcp", "add", "legato", "--", *cmd, "mcp"], capture_output=True, text=True)
    elif client in ("cursor", "claude-desktop"):
        f = _cursor_file() if client == "cursor" else _claude_desktop_file()
        _merge_json(f, cmd)
        return f"{client}: added to {f}" + (" (restart Claude Desktop)" if client == "claude-desktop" else "")
    else:
        raise ValueError(f"unknown coding agent {client!r}: one of {', '.join(CLIENTS)}")
    if r.returncode != 0:
        raise RuntimeError(f"{client} mcp add failed: {(r.stderr or r.stdout).strip()[-300:]}")
    return f"{client}: added (`{client} mcp list` shows it)"


# ------------------------------------------------------------ the checks
def permission_lines(request: bool = False) -> list[str]:
    """macOS: one line per permission that is off, with the fix. request=True first shows the system's own prompts
    (they add the terminal to the list in System Settings, switched off, so it only has to be switched on)."""
    from legato import safety
    if request and sys.platform == "darwin":
        for fn in ("CGRequestPostEventAccess", "CGRequestScreenCaptureAccess"):
            safety._flag(fn)
    return safety.permission_problems()


def state() -> dict:
    """What is set up, for `legato setup --json` and coding agents."""
    cfg = C.load()
    out = {"config": str(C.path()), "model": cfg.model, "base_url": cfg.base_url, "has_key": bool(cfg.key),
           "desktop": cfg.desktop or C.default_desktop(), "app": cfg.app, "ready": cfg.ready,
           "missing": [m for m in [cfg.missing()] if m], "coding_agents_found": coding_agents(),
           "legato": " ".join(legato_command())}
    if (cfg.desktop or C.default_desktop()) in ("background", "screen"):
        out["missing"] += permission_lines()
    out["ready"] = not out["missing"]
    return out


# ------------------------------------------------------------ the first task
def first_task(cfg: C.Config, task: str = FIRST_TASK) -> int:
    """Run `task` with the saved choice, printing each turn with a timer."""
    from legato.agent import open_desktop, run
    t0 = time.monotonic()
    env, close, what = open_desktop(cfg.desktop or None, task=task, cfg=cfg)
    print(f"\nWorking in {what}. Watch it there" + (": your mouse and keyboard stay yours." if "background" in what
                                                   else "; move the mouse to a screen corner to stop it."))

    def show(turn, actions, timing):
        done = ", ".join(a.describe() for a in actions if a) or "looking"
        print(f"  {time.monotonic() - t0:5.1f} s  {done}")
    try:
        res = run(task, env, on_step=show)
    finally:
        close()
    if res.answer:
        print(f"\n{res.answer}")
    print(f"\n{res.report()}")
    return 0 if res.done else 1


# ------------------------------------------------------------ setup
def main(argv=None) -> int:
    try:
        return _main(argv)
    except (EOFError, KeyboardInterrupt):
        print("\nlegato setup: stopped; nothing more was changed", file=sys.stderr)
        return 1


def _main(argv) -> int:
    ap = argparse.ArgumentParser(prog="legato setup", description="Choose your model and desktop; add Legato to your "
                                 "coding agents; run a first task.")
    ap.add_argument("--provider", choices=list(C.PROVIDERS))
    ap.add_argument("--model")
    ap.add_argument("--base-url")
    ap.add_argument("--api-key", help="the model key, or - to read it from stdin (it is saved readable by you only)")
    ap.add_argument("--api-key-env", help="read the key from this environment variable instead of saving it")
    ap.add_argument("--desktop", help="where it works: background (macOS), screen, docker, e2b, or your own sandbox as "
                                      "path/to/file.py:function (docs/sandboxes.md)")
    ap.add_argument("--app", help="background mode: always work in this app (default: the app the task names, else "
                                  "your default browser)")
    ap.add_argument("--mcp", help=f"add Legato to these coding agents ({', '.join(CLIENTS)}, all found, or none); "
                                  "default: ask, or with --yes all found")
    ap.add_argument("--try", dest="try_task", nargs="?", const=FIRST_TASK, metavar="TASK",
                    help="run a first task after setup (default task: the Eiffel Tower on Wikipedia)")
    ap.add_argument("--no-check", action="store_true", help="do not make a test call to the model")
    ap.add_argument("--yes", action="store_true", help="ask nothing: flags, keys in the environment and the defaults")
    ap.add_argument("--json", action="store_true", help="print what is set up as one JSON object and change nothing "
                                                        "(with other flags: set up, then print it)")
    a = ap.parse_args(argv)
    asking = not a.yes and not a.json and sys.stdin.isatty()
    only_show = a.json and not any([a.provider, a.model, a.base_url, a.api_key, a.api_key_env, a.desktop, a.app,
                                    a.mcp, a.yes])
    if only_show:
        print(json.dumps(state()))
        return 0
    def say(*args, **kw) -> None:
        if not a.json:
            print(*args, **kw)
    cfg = C.load(use_env=False)
    from legato import __version__
    say(f"Legato {__version__} setup: your model, where it works, and your coding agents "
        f"(kept in {C.path()}).\n")

    # 1. the model
    provider = a.provider or ("custom" if a.base_url and not a.provider else "")
    if not provider:
        found = next((p for p, v in C.PROVIDERS.items() if v.key_env and os.environ.get(v.key_env)), None)
        provider = cfg.provider or found or "openrouter"
        if asking:
            provider = _ask("1. Which model provider?", list(C.PROVIDERS), provider, lambda p: C.PROVIDERS[p].name)
    pv = C.PROVIDERS[provider]
    base_url = a.base_url or (cfg.base_url if provider == cfg.provider and cfg.base_url else pv.base_url)
    models: list[str] = []
    if provider == "local" and not a.base_url:
        hit = C.find_local_server()
        if hit:
            base_url, models = hit
            say(f"   found a model server at {base_url}")
        elif asking:
            base_url = _text("   Base URL of your model server (ends in /v1)", base_url)
    elif provider == "custom" and asking and not base_url:
        base_url = _text("   Base URL (OpenAI-compatible, ends in /v1)")
    if not base_url:
        print("legato setup: pass --base-url (any OpenAI-compatible server)", file=sys.stderr)
        return 2

    key_env = a.api_key_env if a.api_key_env is not None else (pv.key_env or (cfg.api_key_env if provider == cfg.provider else ""))
    key = ""
    if a.api_key == "-":
        key = sys.stdin.readline().strip()
    elif a.api_key:
        key = a.api_key
    elif a.api_key_env is None and provider == cfg.provider and cfg.api_key:
        key = cfg.api_key
    in_env = bool(key_env and os.environ.get(key_env))
    needs_key = not C.is_local_url(base_url) and (provider != "custom" or asking)
    if not key and needs_key and not in_env:
        if asking:
            where = f" (get one at {pv.keys_url})" if pv.keys_url else ""
            key = getpass.getpass(f"   Paste your {pv.name.split(':')[0]} key{where}, or Enter for none: ").strip()
        elif not C.is_local_url(base_url):
            say(f"   no key yet: export {key_env or 'LEGATO_API_KEY'}=..., or run legato setup --api-key -")
    elif in_env and not key:
        say(f"   using the key in ${key_env}")

    model = a.model or (cfg.model if provider == cfg.provider and cfg.model else pv.model)
    if not models and (provider == "local" or (not model and (key or in_env))):
        models = C.list_models(base_url, key or os.environ.get(key_env or "", ""))
    if asking and not a.model:
        if models and not model:
            model = models[0]
            say("   models it lists: " + ", ".join(models[:12]) + (" ..." if len(models) > 12 else ""))
        model = _text("   Model", model)
    model = model or (models[0] if models else "")
    if not model:
        print("legato setup: pass --model (the model's name at that provider)", file=sys.stderr)
        return 2
    cfg.provider, cfg.model, cfg.base_url = provider, model, base_url
    cfg.api_key, cfg.api_key_env = key, ("" if key else key_env)

    if not a.no_check and (cfg.key or C.is_local_url(base_url)):
        say(f"   checking {model} ...", end=" ", flush=True)
        r = C.check_model(model, base_url, cfg.key)
        if not r["ok"]:
            say(f"\n   the model did not answer: {r['error']}")
            if not asking or not _yes("   Save it anyway?", False):
                fix = (f"No model server answered at {base_url}: start one (vllm serve, ollama serve, or LM Studio's "
                       "server) or pass --base-url" if C.is_local_url(base_url)
                       else "Check the key, the model name and the URL")
                print(f"legato setup: nothing was saved. {fix}, or pass --no-check to save it as it is.",
                      file=sys.stderr)
                if a.json:
                    print(json.dumps({**state(), "ready": False, "missing": [f"the model did not answer: {r['error']}"]}))
                return 1
        else:
            say(f"ok, {r['seconds']} s a call" + ("; it did not see the test image, and computer use needs a "
                                                   "vision model" if r["sees_images"] is False else ""))

    # 2. the desktop
    mac = sys.platform == "darwin"
    choices = [d for d in C.DESKTOPS if mac or d != "background"] + ["own"]
    desktop = a.desktop or cfg.desktop or C.default_desktop()
    if asking and not a.desktop:
        say("")
        picked = _ask("2. Where should it work?", choices, "own" if C.is_own(desktop) else desktop,
                      lambda d: C.OWN if d == "own" else C.DESKTOPS[d])
        if picked == "own":
            say("   A function that starts your sandbox and returns legato.attach(shell=run): "
                "https://legato.computer/docs/sandboxes")
            picked = _text("   The function, as path/to/file.py:function", desktop if C.is_own(desktop) else "")
        desktop = picked
    try:
        desktop = C.desktop_spec(desktop)
        if C.is_own(desktop):
            from legato import providers
            providers.load(desktop)                        # imports it and finds the function; does not start it
    except Exception as e:  # noqa: BLE001 - any error in the user's file is the answer
        print(f"legato setup: cannot use that desktop: {e}", file=sys.stderr)
        return 2
    if desktop == "background" and not mac:
        print("legato setup: background is macOS only; use screen or docker", file=sys.stderr)
        return 2
    cfg.desktop = desktop
    if a.app is not None:
        cfg.app = a.app
    elif asking and desktop == "background":
        cfg.app = _text("   Always work in one app? Name it, or Enter to use the app each task names "
                        "(else your browser)", cfg.app)
    if desktop in ("background", "screen") and mac:
        problems = permission_lines(request=asking)
        for p in problems:
            say(f"   needs: {p}")
        if problems and asking:
            from legato import safety
            if _yes("   Open System Settings there now?"):
                subprocess.run(["open", safety.PANE + ("ScreenCapture" if "screen" in problems[0] else "Accessibility")])
    elif desktop == "docker":
        from legato import doctor
        d = doctor._docker()
        if not d["ok"]:
            say(f"   needs: {d['fix']}")
    elif C.is_own(desktop):
        say(f"   your sandbox: each task calls {desktop.rpartition(':')[2]}() for a fresh desktop and closes it after")
    path = C.save(cfg)
    where = (f"in the background, in {cfg.app or 'the app each task names, else ' + C.pick_app('', cfg)}"
             if desktop == "background" else f"in your sandbox ({desktop})" if C.is_own(desktop)
             else C.DESKTOPS.get(desktop, desktop).split(":")[0])
    say(f"\nSaved to {path}. Legato will work {where}.")

    # 3. coding agents
    found = coding_agents()
    if a.mcp is not None:
        want = found if a.mcp == "all" else [] if a.mcp == "none" else [c.strip() for c in a.mcp.split(",") if c.strip()]
    elif asking and found:
        say("")
        want = found if _yes(f"3. Give your coding agents a computer too? Adds Legato to {', '.join(found)}") else []
    else:
        want = found if a.yes else []
    added = []
    for c in want:
        try:
            line = add_mcp(c)
            added.append(c)
            say(f"   {line}")
        except (RuntimeError, ValueError, OSError) as e:
            say(f"   {c}: {e}")

    if a.json:
        print(json.dumps({**state(), "mcp_added": added}))
        return 0
    # 4. a first task
    task = a.try_task
    if task is None and asking and cfg.ready:
        say("")
        if _yes(f'4. Try it now? Task: "{FIRST_TASK}"'):
            task = FIRST_TASK
    if task:
        return first_task(C.load(), task)
    say('\nNext: legato run "your task"' + ("   (or ask your coding agent to use its legato desktop)" if added else ""))
    say(f"Docs: {DOCS}")
    return 0
