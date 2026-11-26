"""The engine behind the Legato Mac app (app/macos): the agent loop on this Mac, driven in JSON lines.

The app starts `legato app-engine` as a child process and talks to it on stdin and stdout, one JSON object per line.
The app owns the screen and the person: screenshots (without its own windows), the ghost cursor, pause and take
over, the yes/no questions, the key in Keychain and the history view. The engine owns the model loop
(`Harness.begin/decide/feed`) and the input, which is `legato.local()`'s macOS driver unchanged.

App to engine:
    {"type": "run", "id", "task", "context", "model", "base_url", "api_key", "max_steps", "max_cost_usd", "image_max",
     "wait_scope", "shell", "runs_dir"}
    {"type": "stop"}
    {"type": "test", "model", "base_url", "api_key"}
    {"type": "reply", "id", ...}                      the answer to a request

Engine to app:
    {"type": "ready", "version", "pid", "screen": [w, h]}
    {"type": "event", "event": "run_started" | "thinking" | "plan" | "step" | "finished" | "test_result", ...}
    {"type": "request", "id", "method": "capture" | "will_act" | "confirm", ...}   the engine waits for the reply
    {"type": "notify", "method": "acted"}             the action's input events have all been posted

`max_cost_usd` (0 or missing: no cap) ends the run with status "cost_cap" once the model calls' reported cost reaches
it. Only providers that report a cost (OpenRouter) count toward it.

`will_act` is asked before every action, with the action in screen points (origin top left of the main display).
The app answers {"go": true}, {"stop": why}, {"deny": why} or {"replan": note}: replan drops the rest of the turn and
shows the model a fresh screenshot with the note (after the person paused the run or took over).
"""
from __future__ import annotations

import io
import json
import os
import re
import shlex
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

from legato import safety
from legato.actions import Action
from legato import plan as plans
from legato.harness import DENIED_NOTE, END_NOTE, Harness
from legato.rl.policy import PolicyClient, PolicyConfig, PolicyError
from legato.settle import LOCAL_RULES, Waiter
from legato.tools import Tool, ToolCall, applescript, clip, local_shell

REPLAN_NOTE = ("The run was paused and the person may have used the computer, so the actions left in your last "
               "reply were not run. {why} Look at this screenshot before you act.")
CONTEXT_NOTE = "\n\nWhere the person was when they asked: {context}"
FOLLOW_NOTE = ("\n\nThis follows the person's previous request to you, “{task}”, which you finished with: “{result}”. "
               "Read this one in that light (\"it\", \"that\", \"again\" mean what they did there).")
REPEAT_NOTE = ("\n\nYou did this same task on this Mac before; these steps finished it then:\n{steps}\n"
               "They worked from the screen as it was then. If they would finish it from this screen too, do them as one "
               "plan in this reply (each labelled click is found again before it runs); if not, plan from what the screen "
               "shows now.")
REPEAT_MAX_STEPS = 12          # a routine longer than this is not a repeat worth suggesting
_NOT_STEPS = re.compile(r'^(wait|screenshot|done|fail)$|^bash\(\{"command": "(echo|sleep|true)\b')


def _task_key(task: str) -> str:
    """A task's words as compared for a repeat: lower case, no punctuation, single spaces."""
    return " ".join(re.sub(r"[^\w\s]", " ", task.lower()).split())


def known_routine(runs_dir: str | Path | None, task: str, limit: int = 300) -> dict | None:
    """The shortest earlier run of the same task (same words) that ended done, as {"id", "steps": [...]}: the
    actions that did something (no waits, screenshots or `echo`), at most REPEAT_MAX_STEPS. None if there is none."""
    if not runs_dir:
        return None
    key = _task_key(task)
    if not key:
        return None
    best = None
    try:
        dirs = sorted((d for d in Path(runs_dir).iterdir() if d.is_dir()), reverse=True)[:limit]
    except OSError:
        return None
    for d in dirs:
        try:
            r = json.loads((d / "run.json").read_text())
        except (OSError, ValueError):
            continue
        if r.get("status") != "done" or _task_key(r.get("task") or "") != key:
            continue
        steps = [a for t in r.get("turns") or [] for a in t.get("actions") or [] if not _NOT_STEPS.search(a.strip())]
        if not steps or len(steps) > REPEAT_MAX_STEPS:
            continue
        if best is None or len(steps) < len(best["steps"]):
            best = {"id": r.get("id") or d.name, "steps": steps, "wall_s": r.get("wall_s")}
    return best

# ------------------------------------------------------------------ the shell: what can't be undone waits for a yes
SHELL_HINT = ("Run a shell command on this Mac (bash) and get its output and exit code. It is often much faster than "
              "the screen: open an app with `open -a \"Name\"`, a website with `open https://...`, an app's own link "
              "(`open \"spotify:search:...\"`), read, make or move files. AppleScript goes through the applescript "
              "tool, not osascript here. Commands that delete or "
              "overwrite, send something off this Mac, quit apps, change system settings or read private data wait "
              "for the person's yes; everything else runs at once. Never read passwords, keys or private files.")

_ASK_PROGRAMS = {
    "deletes files": {"rm", "srm", "shred", "unlink"},
    "can't be undone": {"dd", "truncate", "diskutil", "newfs_apfs", "asr", "tmutil"},
    "stops running apps, and their unsaved work": {"kill", "killall", "pkill"},
    "changes system settings": {"sudo", "su", "launchctl", "systemsetup", "networksetup", "pmset", "scutil", "csrutil",
                                "spctl", "tccutil", "dscl", "sysadminctl", "shutdown", "reboot", "halt", "crontab",
                                "chown", "chflags", "nvram", "fdesetup", "security"},
    "sends something off this Mac": {"scp", "sftp", "ftp", "ssh", "nc", "ncat", "telnet", "mail", "mailx", "sendmail",
                                     "gh", "aws", "gcloud", "az", "kubectl", "terraform", "vercel", "heroku"},
    "installs software": {"brew", "pip", "pip3", "npm", "npx", "gem", "cargo", "port", "softwareupdate", "installer"},
    "runs code it can't show": {"eval"},
}
_PRIVATE = re.compile(r"\.ssh\b|\.aws\b|\.gnupg|\.netrc|keychains?\b|\.env\b|id_rsa|id_ed25519|credentials|cookies|"
                      r"login data|\.kube\b|\.docker/config|passwords?\b|\.pem\b|\.p12\b", re.I)
_RUNS_HIDDEN = re.compile(r"\|\s*(?:sudo\s+)?(?:sh|bash|zsh|dash|python3?|perl|ruby|node)\b|base64\s+(?:-d|-D|--decode)"
                          r"|xxd\s+-r")
# commands inside a command: `...` and $(...), and what sh/bash/zsh -c runs (checked like the outer command)
_INNER = re.compile(r"`([^`]*)`|\$\(([^()]*)\)")
_SHELL_C = re.compile(r"(?:^|[\s;&|(`'\"])(?:/bin/|/usr/bin/)?(?:sh|bash|zsh|dash|ksh|fish)\s+(?:-\w+\s+)*-\w*c\w*\s+"
                      r"(?:'([^']*)'|\"((?:[^\"\\]|\\.)*)\"|(\S+))")
# a program written inline (python3 -c "...", node -e "...") that deletes, writes, runs programs or uses the network
_INLINE = re.compile(r"(?:^|[\s;&|(`'\"])(?:python3?|perl|ruby|node|php)\s+(?:-\w+\s+)*-[ce]\b", re.I)
_INLINE_RISK = re.compile(r"remove|unlink|rmtree|rmdir|truncate|subprocess|\bsystem\s*\(|popen|\bexec|\beval|spawn|"
                          r"open\s*\([^)]*['\"][wax+]|write|urllib|requests|socket|http|fetch|child_process|\bfs\.", re.I)
# the environment's secrets, which a command's output would carry to the model's provider
_SECRET_ENV = re.compile(r"(?:^|[\s;&|(`'\"])(?:printenv|export\s+-p|set)\s*(?:$|[;&|)`'\"]|\s\w)|(?:^|[\s;&|(])env\s*(?:$|[;&|)])"
                         r"|\$\{?\w*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD)\w*\}?", re.I)
_EXECUTABLE = re.compile(r"\.(?:command|sh|tool|pkg|mpkg|workflow|terminal|scpt|scptd)$|\.app/?$", re.I)
_SEPARATORS = {";", "&&", "||", "|", "&", "\n", "(", ")", "$(", "`"}
# AppleScript (or JavaScript for Automation) the model wrote; matched case-insensitively on the script text
_SCRIPT_ASK = [
    (re.compile(r"\bdo shell script\b", re.I), "runs a shell command inside AppleScript"),
    (re.compile(r"\bempty\b", re.I), "empties the Trash"),
    (re.compile(r"\bsend\b", re.I), "sends a message"),
    (re.compile(r"\b(?:quit|shut down|restart|log out)\b", re.I), "quits, and unsaved work may be lost"),
    (re.compile(r"\bsaving\s+no\b", re.I), "closes without saving"),
    (re.compile(r"\b(?:purchase|buy|pay)\b", re.I), "may spend money"),
    (re.compile(r"system events\".*\b(?:keystroke|key code|click)\b", re.I | re.S), "types or clicks for you"),
]
_SCRIPT_DELETE = re.compile(r"\b(?:delete|erase|remove)\b", re.I)
_TELLS = re.compile(r"(?:tell application|Application\()\s*\(?\s*[\"']([^\"']+)[\"']", re.I)


def _tokens(cmd: str) -> list[str]:
    lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    return list(lex)


def _segments(toks: list[str]) -> list[list[str]]:
    out, cur = [], []
    for t in toks:
        if t in _SEPARATORS:
            out.append(cur)
            cur = []
        else:
            cur.append(t)
    out.append(cur)
    return [c for c in out if c]


def _paths(p: str, bases: list[str]) -> list[str]:
    p = os.path.expandvars(os.path.expanduser(p))
    return [p] if os.path.isabs(p) else [os.path.join(b, p) for b in bases]


def _overwrites(segs: list[list[str]]) -> bool:
    """Does a segment replace a file that is already there (a `>` redirect, `tee`, `cp` or `mv` onto it)? Paths are
    read against any `cd` before them, the home folder and the engine's folder; best effort."""
    bases = [os.path.expanduser("~"), os.getcwd()]
    exists = lambda p: any(os.path.exists(x) for x in _paths(p, bases))  # noqa: E731
    for seg in segs:
        prog = os.path.basename(seg[0])
        if prog == "cd" and len(seg) > 1:
            bases = _paths(seg[1], bases)[:1] + bases
            continue
        for i, t in enumerate(seg[:-1]):
            if t in (">", ">|", "&>") and seg[i + 1] != "/dev/null" and exists(seg[i + 1]):
                return True
        args = [x for x in seg[1:] if not x.startswith("-")]
        flags = [x for x in seg[1:] if x.startswith("-")]
        if prog == "tee" and not any("a" in f for f in flags) and any(exists(x) for x in args):
            return True
        if prog in ("cp", "mv", "ditto") and len(args) >= 2 and not any(f in ("-n", "-i") for f in flags):
            dest = args[-1]
            for d in _paths(dest, bases):
                if os.path.isdir(d):
                    if any(os.path.exists(os.path.join(d, os.path.basename(src.rstrip("/")))) for src in args[:-1]):
                        return True
                elif os.path.exists(d):
                    return True
    return False


def command_risk(cmd: str) -> str | None:
    """Why `cmd` waits for the person's yes, or None to run it at once. It asks when the command may not be undone
    (deletes, overwrites, empties the Trash, quits apps), sends something off this Mac, changes system settings or
    installs software, reads private data (the output goes to the model's provider), or runs code it can't show.
    A best-effort reading of the text that errs toward asking; the HUD shows every command either way."""
    cmd = cmd.strip()
    if not cmd:
        return "is empty"
    for m in list(_INNER.finditer(cmd)) + list(_SHELL_C.finditer(cmd)):    # bash -c "rm -rf ~/x", `rm -rf ~/x`
        inner = next((g for g in m.groups() if g), "")
        if inner.strip() and inner.strip() != cmd and (why := command_risk(inner)):
            return why
    if _SECRET_ENV.search(cmd):
        return "reads private data, which would go to the model's provider"
    if _INLINE.search(cmd) and _INLINE_RISK.search(cmd):
        return "runs a program inline that deletes, writes, runs commands or uses the network"
    if _PRIVATE.search(cmd):
        return "reads private data, which would go to the model's provider"
    if _RUNS_HIDDEN.search(cmd):
        return "runs code it can't show"
    try:
        toks = _tokens(cmd)
    except ValueError:
        return "can't be read"
    segs = _segments(toks)
    for seg in segs:
        words = [os.path.basename(t) for t in seg]
        for why, progs in _ASK_PROGRAMS.items():
            if any(w in progs for w in words):
                return why
        prog = words[0]
        if "-delete" in seg or ("-exec" in seg and "rm" in words):
            return "deletes files"
        if prog in ("sed", "perl") and any(t.startswith("-i") or t == "-pi" for t in seg[1:]):
            return "edits a file in place"
        if prog == "chmod" and any(t.startswith("-") and "R" in t for t in seg[1:]):
            return "changes permissions on a whole folder"
        if prog == "defaults" and len(seg) > 1 and seg[1] == "delete":
            return "deletes settings"
        if prog == "git" and len(seg) > 1 and (seg[1] in ("push", "clean") or "--hard" in seg or
                                               (seg[1] in ("checkout", "restore") and "--" in seg) or "-D" in seg):
            return "can't be undone in git"
        if prog in ("curl", "wget") and any(t in ("-d", "--data", "--data-binary", "--data-raw", "-F", "--form", "-T",
                                                  "--upload-file", "--post-data", "--post-file") or
                                            t.startswith(("--data", "-X", "--request")) and not t.endswith("GET")
                                            for t in seg[1:]):
            return "sends something off this Mac"
        if prog == "rsync" and ("--delete" in seg or any(":" in t and not t.startswith("-") for t in seg[1:])):
            return "sends or deletes files"
        if prog == "open":
            for t in seg[1:]:
                path = os.path.expanduser(t)
                if _EXECUTABLE.search(t.rstrip("/")) and not path.startswith(("/Applications/", "/System/Applications/")):
                    return "runs a program"
        if prog == "osascript":
            script = " ".join(seg[1:])
            for pat, why in _SCRIPT_ASK:
                if pat.search(script):
                    return why
            if _SCRIPT_DELETE.search(script) and {a.lower() for a in _TELLS.findall(script)} - {"finder"}:
                return "deletes something"           # Finder's delete moves to the Trash; other apps' may not
    if _overwrites(segs):
        return "replaces a file that is already there"
    return None


def command_needs_ok(cmd: str) -> bool:
    return command_risk(cmd) is not None


_SCHEME = re.compile(r"^[A-Za-z][\w+.-]*:")


def opened_app(cmd: str) -> dict | None:
    """The app the last `open` in `cmd` brings up, as the app's focus request takes it: {"app": name} for -a,
    {"bundle": id} for -b, {"url": link} for a link (its app). None for none, or for open -g / -j (kept in the
    background on purpose)."""
    try:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        words = list(lex)
    except ValueError:
        return None
    found, i = None, 0
    while i < len(words):
        if words[i] == "open" and (i == 0 or words[i - 1] in {"&&", ";", "||", "|", "&", "(", "then", "do"}):
            target, background, j = None, False, i + 1
            while j < len(words) and words[j] not in {"&&", ";", "||", "|", "&", ")"}:
                w = words[j]
                if w == "--args":
                    break
                if w in ("-a", "-b") and j + 1 < len(words):
                    target = {"app" if w == "-a" else "bundle": words[j + 1]}
                    j += 2
                    continue
                if w.startswith("-") and not w.startswith("--"):
                    background = background or any(f in w[1:] for f in "gj")
                elif target is None and _SCHEME.match(w):
                    target = {"url": w}
                j += 1
            found = None if background else (target or found)
            i = j
        else:
            i += 1
    return found


_OSASCRIPT = re.compile(r"(?:^|[\s;&|(`$'\"])(?:/usr/bin/)?osascript\b")   # also inside sh -c "osascript ..."


def shell_tool(ask, timeout_s: float = 30.0, max_chars: int = 4000, focus=None) -> Tool:
    """The `bash` tool on this Mac. `ask(question) -> bool` gets the person's yes for what command_risk() flags; a no
    goes back to the model as the tool's result. `focus(target)` brings an app the command opened to the front: macOS
    leaves an app that was already running behind when a background process opens it (Messages on 17 Nov)."""
    def handler(args: dict) -> str:
        cmd = args.get("command")
        if not isinstance(cmd, str) or not cmd.strip():
            return "error: give a command"
        if _OSASCRIPT.search(cmd):                # one way to AppleScript: the checked tool, with its limits
            return ("not run: use the applescript tool for AppleScript (it is checked against the app's dictionary "
                    "first), or do this on the screen.")
        why = command_risk(cmd)
        if why and not ask(f"run this command, which {why}: {cmd}"):
            return "not run: the person said no to this command. Do it on the screen, or another way."
        out = local_shell(cmd, timeout_s)
        target = opened_app(cmd) if focus is not None and out.rstrip().endswith("[exit 0]") else None
        if target:
            try:
                focus(target)
            except Exception:  # noqa: BLE001 - the command ran; the screenshot shows where things are
                pass
        return clip(out, max_chars)
    return Tool("bash", SHELL_HINT, {"type": "object", "required": ["command"],
                                     "properties": {"command": {"type": "string", "description": "The shell command"}}},
                handler)


# Where a script has worked for Qwen3.8-27B (164 scripts): creating or changing
# named items in Calendar and Reminders (calendar-week 6 of 8 passed scripted, 2 of 10 on the screen), named notes,
# Finder's Trash and System Events settings. Contacts failed first try in 13 of 16 episodes (`phone numbers` for
# `phones`), and on a real Mac the app's runs spent their time reading through Notes and every Messages chat to work
# out what a vague request ("my favorite song", "text my partner") meant: those are for the screen.
SCRIPT_APPS = ("Calendar", "Reminders", "Notes", "Finder", "System Events")
# reading every item of the person's data, with no `whose` to name one: searching, not doing
_TROLL = re.compile(r"\bevery\s+(?:notes?|chats?|messages?|persons?|people|accounts?|buddy|buddies|participants?)\b"
                    r"(?![^\n]*\bwhose\b)", re.I)
APPLESCRIPT_APP_HINT = (
    "Run AppleScript, compiled against the app's dictionary before it runs, for one step that creates or changes "
    "something the task names: in Calendar (a calendar, events), Reminders (a list, reminders, completing them), Notes "
    "(make, add to or delete a named note), Finder (move a known file to the Trash) or System Events (a setting such as "
    "dark mode). One script for the whole step, with `check`: a read-only script that reads back that item. Build "
    "dates from parts (set year, month, day and hours of (current date)); a Reminders list is a `list`, an item takes "
    "`name:`. Not for other apps (Messages, Contacts, Mail, Spotify: use the screen or the shell), not to search or "
    "list the person's notes, chats, contacts or events to work out what the task means (look at the screen: pinned "
    "and recent chats, recently played), and not for what one or two clicks on the screen would do. On an error you "
    "get the app's real terms; after two failed scripts for an app, use the screen for it.")


def _app_plan_system() -> str:
    """The plans prompt (legato/plan.py) with the app's limits on AppleScript (SCRIPT_APPS)."""
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


def _script_apps(script: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r'\btell\s+(?:application|app)\s+(?:id\s+)?"([^"]+)"', script, re.I)))


def _unescape(script: str) -> str:
    """A script sent JSON-escaped twice (`tell application \\"Contacts\\"\\n...`, qwen4-max on 17 Nov) cannot
    compile: undo the extra escaping once."""
    if "\n" not in script and "\\n" in script and '\\"' in script:
        try:
            return json.loads('"' + script.replace('"', '\\"').replace('\\\\"', '\\"') + '"')
        except ValueError:
            pass
    return script


def applescript_tool(ask, timeout_s: float = 30.0, apps: tuple = SCRIPT_APPS) -> Tool:
    """The checked `applescript` tool (legato.tools.applescript: compiled first, the app's dictionary with any error, a
    read-back, two failures per app then the screen) behind the same guard as the shell: a script that deletes,
    sends, quits an app or presses keys in other apps waits for the person's yes (command_risk reads it as osascript).
    Only for `apps`, and not for reading through the person's data (see SCRIPT_APPS). Measured:
    app-data tasks 164 -> 89 s, passes held."""
    inner = applescript(timeout_s=timeout_s)
    allowed = {a.lower() for a in apps}

    def handler(args: dict) -> str:
        script = args.get("script")
        if isinstance(script, str) and script.strip():
            script = _unescape(script)
            args = {**args, "script": script}
            other = [a for a in _script_apps(script) if a.lower() not in allowed]
            if other:
                return (f"not run: AppleScript here is only for {', '.join(apps)}. Do this in {other[0]} on the "
                        "screen, or with the shell (open -a, an app's link).")
            if _TROLL.search(script):
                return ("not run: this script reads through every item of the person's data. Scripts here only "
                        "create or change an item the task names. To find something, look at the screen.")
            why = command_risk(f"osascript -e {shlex.quote(script)}")
            if why and not ask(f"run this AppleScript, which {why}: {script}"):
                return "not run: the person said no to this script. Do it on the screen, or another way."
        return inner.handler(args)
    return Tool(inner.name, APPLESCRIPT_APP_HINT, inner.parameters, handler)


class Replan(Exception):
    """Drop the rest of the turn and show the model the screen as it is now."""


class StopNow(Exception):
    """The person stopped the run while the model was still thinking."""


class Link:
    """JSON lines on the original stdout; anything else the process prints goes to stderr."""

    def __init__(self, out: Any = None, inp: Any = None):
        self.out = out or os.fdopen(os.dup(1), "w", buffering=1)
        self.inp = inp or sys.stdin
        self.lock = threading.Lock()
        self.pending: dict[int, tuple[threading.Event, dict]] = {}
        self.next_id = 0
        self.closed = False

    def send(self, msg: dict) -> None:
        with self.lock:
            self.out.write(json.dumps(msg, separators=(",", ":")) + "\n")
            self.out.flush()

    def event(self, name: str, **fields) -> None:
        self.send({"type": "event", "event": name, **fields})

    def request(self, method: str, **params) -> dict:
        """Ask the app and wait for its answer. When the app has gone, every question is answered with a stop."""
        if self.closed:
            return {"stop": "the Legato app closed"}
        done, box = threading.Event(), {}
        with self.lock:
            self.next_id += 1
            rid = self.next_id
            self.pending[rid] = (done, box)
        self.send({"type": "request", "id": rid, "method": method, **params})
        done.wait()
        return box

    def reply(self, msg: dict) -> None:
        with self.lock:
            slot = self.pending.pop(msg.get("id"), None)
        if slot:
            slot[1].update(msg)
            slot[0].set()

    def close(self) -> None:
        self.closed = True
        with self.lock:
            slots, self.pending = list(self.pending.values()), {}
        for done, box in slots:
            box["stop"] = "the Legato app closed"
            done.set()


class _NoGrab:
    """LocalEnv wants an mss-like grabber; the app takes the screenshots instead."""
    monitors = [None, None]

    def grab(self, mon):  # pragma: no cover - never called: AppEnv overrides _digest and _png
        raise RuntimeError("screenshots come from the app")


def _local_env_class():
    from legato.local import LocalEnv
    return LocalEnv


def app_env(link: Link, run: "Run", gui: Any = None, wait_scope: str = "screen"):
    """This Mac, with screenshots, the go-ahead for each action and the yes/no questions coming from the app.
    wait_scope="screen" (the default): the wait watches the whole screen. "app" watches only the front app's windows (as
    legato.local's option); it lost 3 of 12 tasks in its first measured run (stale screenshots in Notes), so it is off."""
    LocalEnv = _local_env_class()

    class AppEnv(LocalEnv):
        def __init__(self):
            super().__init__(mac_cmd=True, gui=gui, grabber=_NoGrab(), confirm=self._confirm, wait_scope=wait_scope)
            self.refocus = False                  # the app asks in a panel that never takes the focus
            # the wait leaves out what moves on its own (a spinner in another window, a music player's progress bar): with
            # the whole screen exact, those held every action to the 2 s cap (the app's runs on 16-17 Nov)
            self.waiter = Waiter(self._digest, rules=LOCAL_RULES, background=wait_scope == "screen")

        # -------------------------------------------------- the screen, from the app
        def _capture(self, kind: str) -> dict:
            scope = self.wait_scope if kind == "digest" else "screen"      # the model always sees the whole screen
            r = link.request("capture", kind=kind, size=list(self.size), scope=scope)
            if r.get("error") or r.get("stop"):
                raise RuntimeError(f"screenshot failed: {r.get('error') or r.get('stop')}")
            return r

        def _digest(self):
            r = self._capture("digest")
            cells = r.get("cells")                # a grid of cell digests (an app built before 18 Nov sends none)
            return tuple(cells) if isinstance(cells, list) and cells else r["digest"]

        def _png(self) -> bytes:
            r = self._capture("png")
            path = Path(r["path"])
            try:
                png = path.read_bytes()
            finally:
                path.unlink(missing_ok=True)
            if tuple(r.get("size") or self.size) != tuple(self.size):
                from PIL import Image
                im = Image.open(io.BytesIO(png)).convert("RGB").resize(self.size, Image.BILINEAR)
                buf = io.BytesIO()
                im.save(buf, "PNG", compress_level=1)
                png = buf.getvalue()
            return png

        def _focus(self, target: dict) -> None:
            """Bring an app the shell opened to the front (the app does it: it has Accessibility access), then wait
            for its window like after a click."""
            r = link.request("focus", **target)
            if r.get("stop"):
                raise safety.Stopped(r["stop"])
            self.waiter.settle("click")

        # -------------------------------------------------- the person
        def _confirm(self, question: str) -> bool:
            r = link.request("confirm", question=question)
            if r.get("stop"):
                raise safety.Stopped(r["stop"])
            return bool(r.get("allow"))

        def stop_requested(self) -> str | None:
            if run.stopping:
                return "stopped from the Legato app"
            return super().stop_requested()

        def _act(self, action: Action, *, until, timeout_s: float, read):
            if action.done:
                return super()._act(action, until=until, timeout_s=timeout_s, read=read)
            if run.stopping:
                raise safety.Stopped("stopped from the Legato app")
            r = link.request("will_act", action=action.to_dict(), describe=action.describe(), screen=list(self.size))
            if r.get("stop"):
                raise safety.Stopped(r["stop"])
            if r.get("deny"):
                raise safety.Denied(r["deny"])
            if r.get("replan"):
                raise Replan(r["replan"])
            return super()._act(action, until=until, timeout_s=timeout_s, read=read)

        def _exec(self, a: Action) -> None:
            try:
                super()._exec(a)
            finally:
                link.send({"type": "notify", "method": "acted"})

    return AppEnv()


def _jpeg(png: bytes, path: Path, width: int = 1280) -> None:
    from PIL import Image
    im = Image.open(io.BytesIO(png)).convert("RGB")
    if im.width > width:
        im = im.resize((width, round(im.height * width / im.width)), Image.BILINEAR)
    im.save(path, "JPEG", quality=70)


class Run:
    """One task: the loop, its events for the app, and its record on disk (run.json and a JPEG per turn)."""

    def __init__(self, link: Link, msg: dict, env_factory=None, harness: Harness | None = None):
        self.link, self.msg = link, msg
        self.id = msg.get("id") or uuid.uuid4().hex[:12]
        self.stopping = False
        self.env_factory = env_factory or (lambda run: app_env(link, run, wait_scope=msg.get("wait_scope") or "screen"))
        self.harness = harness
        base = Path(msg.get("runs_dir") or Path.home() / "Library/Application Support/Legato/runs")
        self.dir = base / self.id
        self.record: dict = {}

    # -------------------------------------------------- record
    def _save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / "run.json.tmp"
        tmp.write_text(json.dumps(self.record, indent=1))
        tmp.replace(self.dir / "run.json")

    def _shot(self, png: bytes, n: int) -> str | None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            name = f"turn-{n:03d}.jpg"
            _jpeg(png, self.dir / name)
            return name
        except Exception:  # noqa: BLE001 - a missing thumbnail never stops a run
            return None

    def _think(self, fn):
        """Run the model call in a thread so a stop from the app ends the run without waiting for the reply."""
        box: dict = {}

        def go():
            try:
                box["r"] = fn()
            except BaseException as e:  # noqa: BLE001
                box["e"] = e
        t = threading.Thread(target=go, daemon=True)
        t.start()
        while t.is_alive():
            t.join(0.1)
            if self.stopping:
                raise StopNow()
        if "e" in box:
            raise box["e"]
        return box["r"]

    # -------------------------------------------------- the loop
    def __call__(self) -> dict:
        m, link = self.msg, self.link
        t0 = time.monotonic()
        task = (m.get("task") or "").strip()
        full_task = task + (CONTEXT_NOTE.format(context=m["context"].strip()) if (m.get("context") or "").strip() else "")
        fu = m.get("follow_up") or {}
        if (fu.get("task") or "").strip():
            full_task += FOLLOW_NOTE.format(task=fu["task"].strip()[:300], result=(fu.get("result") or "").strip()[:1500])
        routine = known_routine(m.get("runs_dir"), task) if m.get("repeats", True) is not False and not fu else None
        if routine:
            full_task += REPEAT_NOTE.format(steps="\n".join(f"{i}. {a}" for i, a in enumerate(routine["steps"], 1)))
        image_max = int(m["image_max"]) if m.get("image_max") else 8      # Harness's default
        self.record = {"id": self.id, "task": task, "context": m.get("context") or "", "model": m.get("model"),
                       "base_url": m.get("base_url"), "image_max": image_max, "started": time.time(),
                       "status": "running", "turns": [],
                       "usage": {"prompt": 0, "completion": 0, "cached": 0, "cost_usd": 0.0}}
        status, message, error = "max_steps", "", None
        if routine:
            self.record["routine"] = {"from": routine["id"], "steps": len(routine["steps"])}
        if fu:
            self.record["follow_up"] = (fu.get("task") or "")[:300]
        link.event("run_started", id=self.id, task=task, dir=str(self.dir),
                   routine={"from": routine["id"], "steps": len(routine["steps"]), "wall_s": routine.get("wall_s")} if routine else None)
        try:
            env = self.env_factory(self)
            tools = ([shell_tool(env._confirm, focus=getattr(env, "_focus", None)), applescript_tool(env._confirm)]
                     if m.get("shell") else [])
            use_plans = m.get("plan", True) is not False      # one call carries several steps
            h = self.harness or Harness(model=m["model"], base_url=m["base_url"], api_key=m.get("api_key") or "",
                                        image_max=image_max, tools=tools, plan=use_plans,
                                        system=_app_plan_system() if use_plans else None,
                                        end_fix=m.get("end_fix", True) is not False,
                                        image_format=m.get("image_format") or "jpeg")
            idle = 0                                          # replies in a row with no action (the ending fix)
            plan_mode = bool(getattr(h, "plan_mode", False))
            obs = env.observe()
            ep = h.begin(full_task, obs.screenshot)
            seen = obs.screenshot                             # the screenshot the model plans on
            if plan_mode:                                     # the text reader loads once (~1-10 s): during the 1st call
                threading.Thread(target=plans.warm_up, args=(obs.screenshot,), daemon=True).start()
            self.record["first"] = self._shot(obs.screenshot, 0)
            self._save()
            for turn in range(int(m.get("max_steps") or 40)):
                link.event("thinking", id=self.id, turn=turn + 1)
                try:
                    plan = self._think(lambda: h.decide(ep))
                except PolicyError as e:
                    status, error = "error", str(e)[:300]
                    break
                u = plan.rec.get("usage") or {}
                for k in ("prompt", "completion", "cached"):
                    self.record["usage"][k] += u.get(k) or 0
                self.record["usage"]["cost_usd"] += u.get("cost") or 0.0
                text = (plan.rec.get("content") or "").strip()
                link.event("plan", id=self.id, turn=turn + 1, text=text[:600], model_s=round(plan.rec.get("s", 0.0), 2),
                           actions=[a.describe() if a else "screenshot" for a in plan.actions], done=plan.done,
                           failed=plan.failed, usage=self.record["usage"])
                timing = {"model_s": plan.rec.get("s", 0.0), "act_s": 0.0, "settle_s": 0.0, "obs_s": 0.0}
                pngs, ran, note, stopped, results, stale = [], [], None, None, [], False
                if plan_mode:
                    try:
                        pngs, results = h._run_plan(plan, env, seen, timing)
                    except safety.Stopped as e:
                        stopped = str(e)
                    except safety.Denied as e:
                        note = DENIED_NOTE.format(what=e)
                        ran.append(f"not allowed: {e}")
                        obs = env.observe()
                        pngs = [obs.screenshot]
                    except Replan as e:
                        note = REPLAN_NOTE.format(why=str(e).strip())
                        ran.append("paused")
                        obs = env.observe()
                        pngs = [obs.screenshot]
                    ran = [a.describe() for a in plan.actions[: int(timing.get("plan_ran", 0))]] + ran
                    if plan.done or plan.failed:
                        ran.append("done" if plan.done else "fail")
                for a in ([] if plan_mode else plan.actions):
                    if isinstance(a, ToolCall):
                        t = time.monotonic()
                        try:
                            results.append(f"{a.name} result:\n{h.call_tool(a)}")
                        except safety.Stopped as e:          # stopped while the person was asked
                            stopped = str(e)
                            break
                        timing["tool_s"] = timing.get("tool_s", 0.0) + time.monotonic() - t
                        ran.append(a.describe())
                        stale = True                         # a command may have changed the screen
                        continue
                    try:
                        obs = env.observe() if a is None else env.act(a)
                    except safety.Stopped as e:
                        stopped = str(e)
                        break
                    except safety.Denied as e:
                        note = DENIED_NOTE.format(what=e)
                        ran.append(f"not allowed: {a.describe()}")
                        obs = env.observe()
                        pngs.append(obs.screenshot)
                        break
                    except Replan as e:
                        note = REPLAN_NOTE.format(why=str(e).strip())
                        ran.append(f"paused before: {a.describe()}")
                        obs = env.observe()
                        pngs.append(obs.screenshot)
                        break
                    ran.append(a.describe() if a else "screenshot")
                    pngs.append(obs.screenshot)
                    stale = False
                    for k in ("act_s", "settle_s", "obs_s"):
                        timing[k] += env.last.get(k, 0.0)
                    if env.last.get("wait"):          # what the wait saw: for reading where the time went
                        timing.setdefault("waits", []).append(env.last["wait"])
                if stale and not stopped:
                    obs = env.observe()
                    pngs.append(obs.screenshot)
                    timing["obs_s"] += env.last.get("obs_s", 0.0)
                if getattr(h, "end_fix", False) and not stopped and not note:
                    # "The task is complete." with no action: a note asks for done; said twice, the task is done
                    silent = not plan.rec.get("tool_calls") and not plans._objects(text)
                    if silent and not plan.actions and not plan.done and not plan.failed and \
                            not getattr(plan, "cut_off", False):
                        idle += 1
                        if idle >= 2:
                            plan.done, timing["ended"] = True, "no action twice"
                            ran.append("done")
                        else:
                            results = list(results or []) + [END_NOTE]
                    else:
                        idle = 0
                shot = self._shot(pngs[-1], turn + 1) if pngs else None
                self.record["turns"].append({"turn": turn + 1, "text": text[:2000], "actions": ran, "shot": shot,
                                             "timing": {k: (round(v, 2) if isinstance(v, float) else v)
                                                        for k, v in timing.items()}})
                self._save()
                link.event("step", id=self.id, turn=turn + 1, actions=ran, timing=timing, shot=shot,
                           usage=self.record["usage"], elapsed_s=round(time.monotonic() - t0, 1))
                if stopped:
                    status, message = "stopped", stopped
                    break
                if not note and (plan.done or plan.failed):
                    status, message = ("done" if plan.done else "failed"), text
                    break
                cap = float(m.get("max_cost_usd") or 0)
                if cap > 0 and self.record["usage"]["cost_usd"] >= cap:
                    status, message = "cost_cap", (f"Stopped at ${self.record['usage']['cost_usd']:.2f}, the most a task "
                                                   f"may cost (${cap:.2f}). You can allow more in Settings.")
                    break
                if pngs:
                    seen = pngs[-1]
                h.feed(ep, plan, pngs or [seen], results or None, note)
        except StopNow:
            status, message = "stopped", "stopped from the Legato app"
        except safety.Stopped as e:
            status, message = "stopped", str(e)
        except Exception as e:  # noqa: BLE001 - the app shows it; the engine keeps serving
            status, error = "error", f"{type(e).__name__}: {e}"[:500]
            traceback.print_exc(file=sys.stderr)
        self.record.update(status=status, message=message[:2000], error=error, ended=time.time(),
                           wall_s=round(time.monotonic() - t0, 1), steps=len(self.record["turns"]))
        try:
            self._save()
        except OSError:
            pass
        link.event("finished", id=self.id, status=status, message=message[:2000], error=error,
                   steps=self.record["steps"], wall_s=self.record["wall_s"], usage=self.record["usage"],
                   dir=str(self.dir))
        return self.record


def _test_png() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (220, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


def test_connection(msg: dict) -> dict:
    """One small vision call: does the endpoint answer, does the model take images, and how long a turn takes."""
    import base64
    try:
        client = PolicyClient(PolicyConfig(base_url=msg["base_url"], model=msg["model"], api_key=msg.get("api_key") or "",
                                           timeout_s=30.0, retries=0, logprobs=False, token_ids=False))
        img = "data:image/png;base64," + base64.b64encode(_test_png()).decode()
        rec = client.chat([{"role": "user", "content": [
            {"type": "text", "text": "What colour is this image? Answer with one word."},
            {"type": "image_url", "image_url": {"url": img}}]}], max_tokens=20, temperature=0.0)
        reply = (rec.get("content") or "").strip()
        return {"ok": True, "reply": reply[:80], "seconds": round(rec.get("s", 0.0), 2),
                "sees_images": "red" in reply.lower()}
    except (PolicyError, KeyError, ValueError) as e:
        return {"ok": False, "error": str(e)[:300]}


class Engine:
    def __init__(self, link: Link, env_factory=None):
        self.link, self.env_factory = link, env_factory
        self.run: Run | None = None
        self.thread: threading.Thread | None = None

    def handle(self, msg: dict) -> None:
        t = msg.get("type")
        if t == "reply":
            self.link.reply(msg)
        elif t == "run":
            if self.thread and self.thread.is_alive():
                self.link.event("finished", id=msg.get("id"), status="error", error="a task is already running",
                                steps=0, wall_s=0, usage={}, message="")
                return
            self.run = Run(self.link, msg, self.env_factory)
            self.thread = threading.Thread(target=self.run, daemon=True)
            self.thread.start()
        elif t == "stop":
            if self.run:
                self.run.stopping = True
        elif t == "test":
            threading.Thread(target=lambda: self.link.event("test_result", **test_connection(msg)), daemon=True).start()

    def serve(self) -> int:
        size = None
        try:
            import pyautogui
            size = list(pyautogui.size())
        except Exception:  # noqa: BLE001
            pass
        from legato import __version__
        self.link.send({"type": "ready", "version": __version__, "pid": os.getpid(), "screen": size})
        for line in self.link.inp:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                print(f"legato app-engine: not JSON: {line[:200]}", file=sys.stderr)
                continue
            self.handle(msg)
        # the app has gone: stop any run where it stands and release whatever it waits on
        if self.run:
            self.run.stopping = True
        self.link.close()
        if self.thread:
            self.thread.join(5.0)
        return 0


def main(argv=None) -> int:
    link = Link()
    os.dup2(2, 1)                               # stray prints, from Python or C, must not corrupt the protocol
    sys.stdout = sys.stderr
    return Engine(link).serve()


if __name__ == "__main__":
    sys.exit(main())
