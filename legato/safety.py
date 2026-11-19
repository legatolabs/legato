"""Safety for `legato.local()`, where the agent has the person's real mouse, keyboard, files and accounts.

- `permissions()`: whether macOS lets this process post input (Accessibility) and capture the screen (Screen
  Recording), and what to change in System Settings when it does not. It only reads: nothing is requested or shown.
- `Stopped`: the person stopped the agent (the mouse in a corner of the screen, or Escape held down).
- `risky(...)` and `Denied`: the short list of actions that need the person's yes before they run.
- `command_risk(cmd)`: the guard for a shell or AppleScript tool beside the screen (the Legato Mac app's), which asks
  about more: anything that may not be undone, sends something off this computer or reads private data.
- `dialog`: a `confirm` that asks in a macOS dialog, for when there is no terminal (`legato mcp --local`).

The list covers what can be read from the action itself: key combinations and typed shell commands. A click is a
pair of coordinates, so a click on a Delete, Send or Buy button is not caught, nor is a menu item, a drag to the
Trash, or a command hidden behind an alias or a script. Use a spare account or a VM for anything those could hurt.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from typing import Any, Callable

Confirm = Callable[[str], bool]


class Stopped(Exception):
    """The person stopped the agent. `Harness.run` ends the run with `stopped=True`."""


class Denied(Exception):
    """The person did not allow an action, so it was not run. `Harness.run` tells the model and carries on."""


# ------------------------------------------------------------ what needs a yes
_NAMES = {"super": "command", "cmd": "command", "win": "command", "meta": "command", "control": "ctrl",
          "option": "alt", "return": "enter", "kp_enter": "enter", "del": "delete"}

# (keys that must all be in the combination, what it does). Any extra modifier still matches: cmd+shift+Delete
# empties the Trash and cmd+shift+q logs out.
KEY_RULES: list[tuple[frozenset, str]] = [
    (frozenset({"command", "delete"}), "moves the selection to the Trash, or empties it"),
    (frozenset({"command", "backspace"}), "moves the selection to the Trash, or empties it"),
    (frozenset({"command", "q"}), "quits the app, or logs out"),
    (frozenset({"ctrl", "q"}), "quits the app"),
    (frozenset({"alt", "f4"}), "closes the app"),
    (frozenset({"shift", "delete"}), "deletes without the Recycle Bin"),
    (frozenset({"ctrl", "alt", "delete"}), "opens the system's sign-out screen"),
]

# Shell commands that delete, overwrite or run with more rights, matched anywhere in a typed line.
TEXT_RULES: list[tuple[re.Pattern, str]] = [(re.compile(p), why) for p, why in [
    (r"\b(rm|rmdir|unlink|shred|srm)\s+\S", "deletes files"),
    (r"\bfind\b.*\s-delete\b", "deletes files"),
    (r"\b(sudo|doas)\b", "runs a command as administrator"),
    (r"\bmkfs(\.\w+)?\b|\bdiskutil\s+(erase|zero|secureErase|partitionDisk|apfs\s+delete)\w*|\bdd\b.*\bof=",
     "erases or overwrites a disk"),
    (r"\bgit\s+push\b.*(\s--force\S*|\s-\w*f\w*\b|\s--delete\b|\s--mirror\b|\s\+\S)", "overwrites or deletes a remote branch"),
    (r"\bgit\s+reset\b.*\s--hard\b|\bgit\s+clean\b.*\s-\w*f", "discards local changes"),
    (r"(^|\s)>\s*[\w~./$\"']|\btruncate\s+\S", "overwrites a file"),
    (r"\|\s*(sudo\s+)?(ba|z|da|fi)?sh\b", "runs a downloaded script"),
]]

ENTER = "enter"


def keys_risk(names: list[str]) -> str | None:
    """Why the key combination `names` (one chord, e.g. ["command", "shift", "delete"]) needs a yes, or None."""
    have = {_NAMES.get(n.lower(), n.lower()) for n in names}
    for need, why in KEY_RULES:
        if need <= have:
            return why
    return None


def text_risk(text: str) -> str | None:
    """Why running `text` as a shell command needs a yes, or None."""
    for line in text.splitlines():
        for pat, why in TEXT_RULES:
            if pat.search(line):
                return why
    return None


def is_enter(names: list[str]) -> bool:
    return [_NAMES.get(n.lower(), n.lower()) for n in names] == [ENTER]


# ------------------------------------------------------------ the command guard (a shell or AppleScript tool)
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
    read against any `cd` before them, the home folder and this process's folder; best effort."""
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
    """Why the shell command `cmd` waits for the person's yes, or None to run it at once: the Legato Mac app's guard
    for its shell and AppleScript tools. It asks when the command may not be undone (deletes, overwrites, empties the
    Trash, quits apps), sends something off this computer, changes system settings or installs software, reads private
    data (the output goes to the model's provider), or runs code it can't show. AppleScript is read as `osascript -e
    SCRIPT`. A best-effort reading of the text that errs toward asking."""
    cmd = cmd.strip()
    if not cmd:
        return "is empty"
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


# ------------------------------------------------------------ asking
def ask(description: str) -> bool:
    """The default `confirm`: a yes/no question in the terminal. Anything but y or yes is a no."""
    try:
        print(f"\a\nlegato: the agent wants to {description}", file=sys.stderr)
        return input("legato: allow it? [y/N] ").strip().lower() in ("y", "yes")
    except (EOFError, OSError):
        return False


def deny(description: str) -> bool:
    """A `confirm` that allows nothing risky: for unattended runs on a real computer."""
    return False


def confirm_off() -> bool:
    """LEGATO_CONFIRM=off (or 0, no, false) in this process's environment: ask nothing."""
    return os.environ.get("LEGATO_CONFIRM", "").lower() in ("0", "off", "no", "false")


def default_confirm() -> Confirm | None:
    """`confirm="auto"`: ask in the terminal when a person is at one; unattended (a benchmark, a VM, CI: stdin is not
    a terminal) there is nobody to ask and nothing is held back. LEGATO_CONFIRM=off turns it off. `legato mcp --local`
    on macOS asks in a `dialog` instead."""
    if confirm_off():
        return None
    try:
        return ask if sys.stdin is not None and sys.stdin.isatty() else None
    except (ValueError, OSError):
        return None


DIALOG_TIMEOUT_S = 60
# The question and the timeout arrive as arguments (argv), never inside the script's text: a quote or a backslash in
# the agent's command cannot change the script. Return presses the default button, Deny; so does Escape.
DIALOG_SCRIPT = (
    "on run argv",
    "activate",
    'set r to display dialog (item 1 of argv) with title "Legato" buttons {"Deny", "Allow"} default button "Deny" '
    'cancel button "Deny" with icon caution giving up after ((item 2 of argv) as integer)',
    'if gave up of r then return "timeout"',
    "return button returned of r",
    "end run",
)


def dialog(description: str, timeout_s: float = DIALOG_TIMEOUT_S, run: Callable[..., Any] = subprocess.run) -> bool:
    """A `confirm` that asks in a macOS dialog with Allow and Deny, for when there is no terminal to ask in (`legato
    mcp --local`). Only a click on Allow allows. Deny, Escape, Return (Deny is the default button), no answer within
    `timeout_s`, or a dialog that could not be shown (not macOS, no window server) is a no. The question goes to the
    person at the screen and never to an agent, so no model can answer it."""
    if sys.platform != "darwin":
        return False
    what = description.strip()
    what = what[:600] + ("…" if len(what) > 600 else "")
    text = (f"An agent using Legato on this Mac wants to {what}\n\nAllow it? With no answer in {int(timeout_s)} s, "
            "it is not allowed.")
    cmd = ["osascript"] + [a for line in DIALOG_SCRIPT for a in ("-e", line)] + [text, str(int(timeout_s))]
    try:
        out = run(cmd, capture_output=True, text=True, timeout=timeout_s + 15)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0 and (out.stdout or "").strip() == "Allow"


# ------------------------------------------------------------ macOS permissions and the Escape key
_lib = None


def _mac():
    """ApplicationServices through ctypes: no pyobjc needed, so `legato doctor` works before the local extra is in."""
    global _lib
    if _lib is None:
        import ctypes
        import ctypes.util
        _lib = ctypes.cdll.LoadLibrary(ctypes.util.find_library("ApplicationServices"))
    return _lib


def _flag(name: str, *args: int) -> bool | None:
    """Call a C function that returns a bool. None when it cannot be called (not macOS, or an older system)."""
    if sys.platform != "darwin":
        return None
    try:
        import ctypes
        f = getattr(_mac(), name)
        f.restype = ctypes.c_bool
        f.argtypes = [ctypes.c_uint32] * len(args)
        return bool(f(*args))
    except Exception:  # noqa: BLE001
        return None


def escape_held() -> bool:
    """Is the Escape key down on the real keyboard right now? (macOS; False elsewhere.)"""
    return bool(_flag("CGEventSourceKeyState", 1, 53))       # kCGEventSourceStateHIDSystemState, kVK_Escape


TERMINALS = {"Apple_Terminal": "Terminal", "iTerm.app": "iTerm", "vscode": "Visual Studio Code", "ghostty": "Ghostty",
             "WarpTerminal": "Warp", "WezTerm": "WezTerm", "kitty": "kitty", "Alacritty": "Alacritty", "Hyper": "Hyper"}
PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_"


def host_app() -> str:
    """The app macOS asks about: the one that started this Python (the terminal, the editor or the MCP client)."""
    name = TERMINALS.get(os.environ.get("TERM_PROGRAM", ""))
    return name or "the app that started this program (your terminal, editor or MCP client)"


def permissions(flag: Callable[..., bool | None] = _flag) -> dict:
    """{"accessibility": {...}, "screen_recording": {...}} on macOS, {} elsewhere. Each has `ok` (True, False, or None
    when it could not be checked) and, when not ok, `fix`: the System Settings pane and the app to switch on.
    These are the system's own read-only checks: they show no dialog and add nothing to System Settings."""
    if sys.platform != "darwin":
        return {}
    app = host_app()
    post = [flag("AXIsProcessTrusted"), flag("CGPreflightPostEventAccess")]
    acc = True if True in post else False if False in post else None
    rec = flag("CGPreflightScreenCaptureAccess")
    out = {}
    for key, ok, pane, anchor, what in (
            ("accessibility", acc, "Accessibility", "Accessibility", "the agent's clicks and keys are ignored"),
            ("screen_recording", rec, "Screen & System Audio Recording (Screen Recording before macOS 15)", "ScreenCapture",
             "screenshots show only the wallpaper")):
        fix = None
        if ok is False:
            fix = (f"without it {what}. Run: open \"{PANE}{anchor}\" (System Settings → Privacy & Security → "
                   f"{pane}), switch on {app}, then quit and reopen it. If it is not listed, add it with +; a "
                   f"Python started some other way is {sys.executable}")
        elif ok is None:
            fix = f"could not check; if it fails, see System Settings → Privacy & Security → {pane}"
        out[key] = {"ok": ok, "fix": fix}
    return out


def permission_problems(perms: dict | None = None) -> list[str]:
    """One line per permission that is known to be missing."""
    perms = permissions() if perms is None else perms
    return [f"macOS {k.replace('_', ' ')} permission is off: {v['fix']}" for k, v in perms.items() if v["ok"] is False]
