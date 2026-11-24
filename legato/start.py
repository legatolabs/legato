"""The first run: `legato` with no arguments says what to do next, and `legato init` writes a working agent.py.

    legato init                                   # your saved model and desktop (legato setup), or asks
    legato init --yes --model qwen/qwen3.8-27b    # no questions: the flags, the saved choice, or the defaults
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

PROVIDERS = {
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "qwen/qwen3.8-27b",
                   "one key for Qwen, Claude, DeepSeek and more (openrouter.ai/keys)"),
    "local": ("http://localhost:8000/v1", "", "Qwen/Qwen3.5-9B",
              "a model server on this machine (vLLM, Ollama, LM Studio): no key, nothing leaves the machine"),
    "custom": ("", "", "", "any other OpenAI-compatible URL"),
}
DESKTOPS = {
    "background": "one app's window on this Mac, worked without your mouse, so you keep using the Mac (macOS)",
    "local": "this whole screen: it moves your real mouse, so leave the computer alone while it runs",
    "docker": "a throwaway Linux desktop in Docker: the safest, it can't touch your files",
}
TESTED_APPS = ("Safari", "Google Chrome", "TextEdit")   # background mode is tested on these (tests/test_background_live.py)
DOCS = "https://legato.computer/docs"
SETUP_URL = "https://legato.computer/setup"
AGENT_PROMPT = f"Set up Legato for me: {SETUP_URL}"


def welcome() -> str:
    from legato import __version__, config
    cfg = config.load()
    desk = cfg.desktop or config.default_desktop()
    if cfg.ready:
        now = f"Set up: {cfg.model} · {desk}" + (f" ({cfg.app})" if cfg.app else "") + "   (legato setup to change)"
    else:
        now = "Not set up yet: start with legato setup."
    return f"""legato {__version__}: a fast computer-use agent for any model.

  legato setup        choose your model and where it works, then watch a first task (about two minutes)
  legato run "..."    do a task, e.g. legato run "Find the next train from SF to San Jose on caltrain.com"
  legato mcp          a computer for Claude Code, Codex or Cursor (legato setup adds it to them)
  legato doctor       what is set up, and the fix for anything that is not
  legato init         write agent.py, to call Legato from your own Python

{now}

In Claude Code, Codex or Cursor? Tell it: "{AGENT_PROMPT}"
Docs: {DOCS}"""


def default_browser() -> str | None:
    """The app macOS opens web links with, by name ("Safari", "Google Chrome"), or None."""
    if sys.platform != "darwin":
        return None
    js = ("ObjC.import('AppKit'); var u = $.NSWorkspace.sharedWorkspace.URLForApplicationToOpenURL("
          "$.NSURL.URLWithString('https://example.com')); u ? u.path.js : ''")
    try:
        out = subprocess.run(["osascript", "-l", "JavaScript", "-e", js], capture_output=True, text=True,
                             timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return Path(out).stem or None


def agent_source(model: str, base_url: str, key_env: str, desktop: str, app: str | None) -> str:
    """agent.py: the whole loop in a few lines, to run as it is or copy into your own code."""
    key = (f'os.environ.get("{key_env}", "")' if key_env else '""') if key_env is not None else \
        "None"   # the key legato setup saved for this server
    if desktop == "background":
        open_desk, close_desk = f'desk = legato.local(background="{app}")   # works in {app}\'s window; you keep the Mac', ""
    elif desktop == "local":
        open_desk, close_desk = "desk = legato.local()   # this screen: moves the real mouse", ""
    else:
        open_desk, close_desk = "stop, desk = legato.providers.docker(1280, 800)   # a fresh Linux desktop container", \
            "\nstop()"
    where = f"in {app}" if desktop == "background" else "in the browser"
    return f'''"""A computer-use agent, written by `legato init`. Run it:  python agent.py "your task"

The whole loop is agent.run(task, desk): copy these lines into your own code. Docs: {DOCS}
"""
import os
import sys

import legato

agent = legato.Harness(
    model="{model}",
    base_url="{base_url}",
    api_key={key},
)
{open_desk}
task = " ".join(sys.argv[1:]) or "Open example.com {where} and check that the page says Example Domain"


def show(turn, actions, timing):
    print(f"turn {{turn + 1}}: {{', '.join(a.describe() for a in actions if a) or 'looking'}}")


result = agent.run(task, desk, max_steps=30, on_step=show)
print(result.report())          # done or failed, the steps, and where the time went{close_desk}
'''


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


def _text(prompt: str, default: str) -> str:
    got = input(f"{prompt}" + (f" [{default}]" if default else "") + ": ").strip()
    return got or default


def missing(desktop: str, key_env: str) -> list[str]:
    """What still has to happen before agent.py runs, each with its fix."""
    from legato import doctor
    out = []
    if key_env and not os.environ.get(key_env):
        where = " (get one at https://openrouter.ai/keys)" if key_env == "OPENROUTER_API_KEY" else ""
        out.append(f"set your model key: export {key_env}=...{where}")
    c = doctor.checks()
    if desktop in ("local", "background"):
        s = c["sandboxes"]["local"]
        if desktop == "background" and not doctor._has("ApplicationServices"):
            out.append('install the Mac extras: pip install "legatolabs[local]"')
        elif s.get("fix") and "permissions" not in s["fix"]:
            out.append(s["fix"])
        for name, p in (s.get("permissions") or {}).items():
            if p["ok"] is False:
                out.append(f"turn on {name.replace('_', ' ')}: {p['fix']}")
    elif desktop == "docker" and not c["sandboxes"]["docker"]["ok"]:
        out.append(c["sandboxes"]["docker"]["fix"])
    return out


def init_main(argv=None) -> int:
    try:
        return _init(argv)
    except (EOFError, KeyboardInterrupt):
        print("\nlegato init: stopped; nothing was written", file=sys.stderr)
        return 1


def _init(argv) -> int:
    ap = argparse.ArgumentParser(prog="legato init", description="Pick a model and a desktop; write agent.py.")
    ap.add_argument("--provider", choices=list(PROVIDERS))
    ap.add_argument("--model")
    ap.add_argument("--base-url")
    ap.add_argument("--api-key-env", help="the environment variable holding the model key ('' for none)")
    ap.add_argument("--desktop", choices=list(DESKTOPS))
    ap.add_argument("--app", help="with --desktop background: the app to work in (default: the app the task names, else Safari)")
    ap.add_argument("--out", default="agent.py")
    ap.add_argument("--force", action="store_true", help="overwrite --out if it exists")
    ap.add_argument("--yes", action="store_true", help="ask nothing: use the flags and the defaults")
    a = ap.parse_args(argv)
    asking = not a.yes and sys.stdin.isatty()
    out = Path(a.out)
    if out.exists() and not a.force:
        print(f"legato: {out} exists; pass --force to overwrite it, or --out another.py", file=sys.stderr)
        return 2

    from legato import config
    saved = config.load(use_env=False)
    use_saved = saved.ready and not (a.provider or a.base_url or a.model)
    provider = a.provider or ("custom" if a.base_url else "openrouter")
    if asking and not (a.provider or a.base_url or use_saved):
        provider = _ask("Which model?", list(PROVIDERS), "openrouter", lambda p: PROVIDERS[p][3])
    base_url, key_env, model, _ = PROVIDERS[provider]
    if use_saved:                                   # what legato setup chose
        base_url, model = saved.base_url, saved.model
        key_env = None if saved.api_key else saved.api_key_env
    base_url = a.base_url or base_url
    key_env = a.api_key_env if a.api_key_env is not None else key_env
    model = a.model or model
    if asking and not use_saved:
        if not a.base_url and provider != "openrouter":
            base_url = _text("Base URL (OpenAI-compatible, ends in /v1)", base_url)
        if not a.model:
            model = _text("Model name", model)
        if a.api_key_env is None and provider == "custom":
            key_env = _text("Environment variable with the key (empty for none)", "OPENAI_API_KEY")
    if not base_url or not model:
        print("legato: pass --base-url and --model (any OpenAI-compatible server)", file=sys.stderr)
        return 2

    mac = sys.platform == "darwin"
    choices = [d for d in DESKTOPS if mac or d != "background"]
    desktop = a.desktop or {"screen": "local"}.get(saved.desktop, saved.desktop) or ("background" if mac else "local")
    if desktop not in DESKTOPS:
        desktop = "local"
    if asking and not a.desktop:
        desktop = _ask("Which desktop?", choices, desktop, lambda d: DESKTOPS[d])
    if desktop == "background" and not mac:
        print("legato: --desktop background is macOS only; use local or docker", file=sys.stderr)
        return 2
    app = None
    if desktop == "background":
        app = a.app or saved.app or default_browser() or "Safari"
        if asking and not a.app:
            app = _text("Which app should it work in? (any app, by name)", app)
        if app not in TESTED_APPS:
            print(f"note: background mode is tested on {', '.join(TESTED_APPS)}. {app} may not show all of its "
                  f"window to macOS Accessibility; if clicks do nothing, try --app Safari or 'Google Chrome'.")

    out.write_text(agent_source(model, base_url, key_env, desktop, app))
    print(f"\nWrote {out}: {model} at {base_url}, " + (f"working in {app}'s window" if app else f"desktop: {desktop}"))
    todo = missing(desktop, key_env)
    if todo:
        print("\nBefore it runs:")
        for t in todo:
            print(f"  - {t}")
    print(f'\nThen: python {out} "your task"')
    print(f"To use it in your own code, copy the lines in {out}. Docs: {DOCS}")
    return 0
