"""Presets: the harness set up as a configuration that was measured, ready to run.

`mac` is the Legato Mac app's setup on this Mac, which is legato-research's Mac champion (mac-2) with the app's guard
and AppleScript limits: plans (one model call carries several steps), a shell and AppleScript beside the screen, the
ending fix, four screenshots kept, JPEG screenshots, and a wait that leaves out what moves on its own.

    env = legato.presets.mac_env()                    # legato.local(wait="cells")
    agent = legato.presets.mac(env, api_key=os.environ["OPENROUTER_API_KEY"])   # Qwen3.8-27B on OpenRouter
    print(agent.run("Play Lose Yourself on Spotify", env, max_steps=legato.presets.MAX_STEPS).report())

Any OpenAI-compatible model works (`model=`, `base_url=`). The shell and AppleScript tools ask `env.confirm`, the
same question as the env's own risky keys, before a command or script that `legato.safety.command_risk` flags; a no
goes back to the model as the tool's result ("not run: ..."), and the plan stops there. With no confirm
(`legato.local(confirm=None)`, or "auto" with no terminal) nothing is asked, as for the env's keys.
"""
from __future__ import annotations

import json
import re
import shlex
from typing import TYPE_CHECKING, Any, Callable

from legato import plan as plans
from legato import safety
from legato.env import Env
from legato.harness import Harness
from legato.tools import Tool, applescript, clip

if TYPE_CHECKING:
    from legato.local import LocalEnv

DEFAULT_MODEL = "qwen/qwen3.8-27b"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
MAX_STEPS = 40                      # the Mac app's model turns a task

# The champion's harness options (legato-research tracks/mac/champion.json, mac-2), and what set each one.
MAC_OPTIONS: dict[str, Any] = {
    "image_max": 4,                 # EXP-0030
    "plan": True,                   # EXP-0044 arm C
    "end_fix": True,                # EXP-0044
    "image_format": "jpeg",         # EXP-0053
}
MAC_TOOLS = ("bash", "applescript")  # EXP-0037, EXP-0043; here behind the app's command guard
MAC_WAIT = "cells"                  # the app's wait: what moves on its own (a spinner, a progress bar) is left out

SHELL_HINT = ("Run a shell command on this Mac (bash) and get its output and exit code. It is often much faster than "
              "the screen: open an app with `open -a \"Name\"`, a website with `open https://...`, an app's own link "
              "(`open \"spotify:search:...\"`), read, make or move files. AppleScript goes through the applescript "
              "tool, not osascript here. Commands that delete or overwrite, send something off this Mac, quit apps, "
              "change system settings or read private data wait for the person's yes; everything else runs at once. "
              "Never read passwords, keys or private files.")

# Where a script has worked for Qwen3.8-27B (legato-research EXP-0037 to EXP-0044): creating or changing named items in
# Calendar and Reminders, named notes, Finder's Trash and System Events settings. Contacts, Messages and reading
# through the person's data to work out what a task means are for the screen.
SCRIPT_APPS = ("Calendar", "Reminders", "Notes", "Finder", "System Events")
# reading every item of the person's data, with no `whose` to name one: searching, not doing
_TROLL = re.compile(r"\bevery\s+(?:notes?|chats?|messages?|persons?|people|accounts?|buddy|buddies|participants?)\b"
                    r"(?![^\n]*\bwhose\b)", re.I)
APPLESCRIPT_HINT = (
    "Run AppleScript, compiled against the app's dictionary before it runs, for one step that creates or changes "
    "something the task names: in Calendar (a calendar, events), Reminders (a list, reminders, completing them), Notes "
    "(make, add to or delete a named note), Finder (move a known file to the Trash) or System Events (a setting such "
    "as dark mode). One script for the whole step, with `check`: a read-only script that reads back that item. Build "
    "dates from parts (set year, month, day and hours of (current date)); a Reminders list is a `list`, an item takes "
    "`name:`. Not for other apps (Messages, Contacts, Mail, Spotify: use the screen or the shell), not to search or "
    "list the person's notes, chats, contacts or events to work out what the task means (look at the screen: pinned "
    "and recent chats, recently played), and not for what one or two clicks on the screen would do. On an error you "
    "get the app's real terms; after two failed scripts for an app, use the screen for it.")
_OSASCRIPT = re.compile(r"(?:^|[\s;&|(`$])(?:/usr/bin/)?osascript\b")
_SCRIPT_TELLS = re.compile(r'\btell\s+(?:application|app)\s+(?:id\s+)?"([^"]+)"', re.I)


def plan_system() -> str:
    """The plans prompt (legato/plan.py) with the Mac app's limits on AppleScript (SCRIPT_APPS), and a note that
    wait_for reads words only."""
    s = plans.PLAN_SYSTEM
    i, j = s.index("- applescript (script; check):"), s.index("- wait_for (text;")
    return s[:i] + (
        "- applescript (script; check): one script that creates or changes something the task names in Calendar (a "
        "calendar, events), Reminders (a list, reminders, completing them), Notes (a named note), Finder (a known file "
        "to the Trash) or System Events (a setting such as dark mode). Not other apps (Messages, Contacts, Mail, "
        "Spotify: use the screen or shell), and never to search or list the person's notes, chats or contacts to work "
        "out what the task means: look at the screen (pinned and recent chats, recently played). The script is "
        "compiled against the app's dictionary first; an error comes back with the app's real terms. check is a "
        "read-only script that reads back what you changed. Build dates from parts (set year, month, day, hours of "
        "(current date)).\n") + s[j:].replace(
        "- wait_for (text; optional timeout_s, at most 15): waits until that label is on the screen.",
        "- wait_for (text; optional timeout_s, at most 15): waits until that label is on the screen. Only words can be "
        "read: an icon (play, pause, a heart) cannot be waited for; wait for a word that appears instead.", 1)


def asker(env: Env) -> Callable[[str], bool]:
    """`question -> bool` through `env.confirm` (read when asked), with the app that was in front put back after the
    answer: a dialog or the terminal takes the focus, and the next screenshot is for the agent's app. No confirm:
    allowed, as for the env's own keys."""
    def ask(question: str) -> bool:
        confirm = getattr(env, "confirm", None)
        if confirm is None:
            return True
        front = env._front() if callable(getattr(env, "_front", None)) else None
        try:
            return bool(confirm(question))
        finally:
            if front:
                env._refocus(front)  # type: ignore[attr-defined]
    return ask


def check_command(env: Env, cmd: str) -> str | None:
    """None when the shell command `cmd` may run: the guard does not flag it, or the person said yes. Otherwise the
    text for the model, which starts with "not run:" (a plan stops at it)."""
    why = safety.command_risk(cmd)
    if why and not asker(env)(f"run this command, which {why}: {cmd}"):
        return (f"not run: the person at this computer said no to this command ({why}). Do it on the screen, or "
                "another way.")
    return None


def shell_tool(env: Env, timeout_s: float = 30.0, max_chars: int = 4000) -> Tool:
    """The `bash` tool on this Mac, behind the command guard (`check_command`). AppleScript is refused here: it goes
    through `applescript_tool`, which checks it against the app's dictionary first."""
    if not callable(getattr(env, "shell", None)):
        raise TypeError("this desktop has no shell")

    def handler(args: dict) -> str:
        cmd = args.get("command")
        if not isinstance(cmd, str) or not cmd.strip():
            return "error: give a command"
        if _OSASCRIPT.search(cmd):
            return ("not run: use the applescript tool for AppleScript (it is checked against the app's dictionary "
                    "first), or do this on the screen.")
        refused = check_command(env, cmd)
        if refused:
            return refused
        return clip(env.shell(cmd, timeout_s), max_chars)  # type: ignore[attr-defined]
    return Tool("bash", SHELL_HINT, {"type": "object", "required": ["command"],
                                     "properties": {"command": {"type": "string", "description": "The shell command"}}},
                handler)


def _unescape(script: str) -> str:
    """A script sent JSON-escaped twice (`tell application \\"Contacts\\"\\n...`) cannot compile: undo the extra
    escaping once."""
    if "\n" not in script and "\\n" in script and '\\"' in script:
        try:
            return json.loads('"' + script.replace('"', '\\"').replace('\\\\"', '\\"') + '"')
        except ValueError:
            pass
    return script


def applescript_tool(env: Env, timeout_s: float = 30.0, apps: tuple[str, ...] = SCRIPT_APPS) -> Tool:
    """The checked `applescript` tool (legato.tools.applescript: compiled first, the app's dictionary with any error, a
    read-back, two failures per app then the screen), only for `apps`, not for reading through the person's data, and
    behind the command guard: a script that deletes, sends, quits an app or presses keys waits for the person's yes."""
    inner = applescript(env, timeout_s=timeout_s)
    allowed = {a.lower() for a in apps}

    def handler(args: dict) -> str:
        script = args.get("script")
        if isinstance(script, str) and script.strip():
            script = _unescape(script)
            args = {**args, "script": script}
            other = [a for a in dict.fromkeys(_SCRIPT_TELLS.findall(script)) if a.lower() not in allowed]
            if other:
                return (f"not run: AppleScript here is only for {', '.join(apps)}. Do this in {other[0]} on the "
                        "screen, or with the shell (open -a, an app's link).")
            if _TROLL.search(script):
                return ("not run: this script reads through every item of the person's data. Scripts here only "
                        "create or change an item the task names. To find something, look at the screen.")
            why = safety.command_risk(f"osascript -e {shlex.quote(script)}")
            if why and not asker(env)(f"run this AppleScript, which {why}: {script}"):
                return (f"not run: the person at this computer said no to this script ({why}). Do it on the screen, "
                        "or another way.")
        return inner.handler(args)
    return Tool(inner.name, APPLESCRIPT_HINT, inner.parameters, handler)


def mac_env(confirm: safety.Confirm | str | None = "auto", **options: Any) -> "LocalEnv":
    """This Mac's screen as the Mac app drives it: `legato.local(wait="cells")`. `confirm` and `options` go to
    `legato.local`."""
    from legato.local import local
    return local(confirm=confirm, **{"wait": MAC_WAIT, **options})


def mac(env: Env, model: str = DEFAULT_MODEL, base_url: str = DEFAULT_BASE_URL, api_key: str = "",
        **options: Any) -> Harness:
    """The Legato Mac app's harness for `env` (this Mac: `mac_env()` or `legato.local()`): MAC_OPTIONS, the plans
    prompt with the app's AppleScript limits, and the guarded `bash` and `applescript` tools. `options` go to
    `Harness` and override any of these (`plan=False` drops the plans prompt too). Run it with
    max_steps=MAX_STEPS, the app's."""
    opts: dict[str, Any] = {**MAC_OPTIONS, "tools": [shell_tool(env), applescript_tool(env)], **options}
    if "system" not in options:
        opts["system"] = plan_system() if opts.get("plan") else None
    return Harness(model, base_url, api_key, **opts)


__all__ = ["APPLESCRIPT_HINT", "DEFAULT_BASE_URL", "DEFAULT_MODEL", "MAC_OPTIONS", "MAC_TOOLS", "MAC_WAIT", "MAX_STEPS",
           "SCRIPT_APPS", "SHELL_HINT", "applescript_tool", "asker", "check_command", "mac", "mac_env", "plan_system",
           "shell_tool"]
