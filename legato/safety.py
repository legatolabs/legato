"""Safety for `legato.local()`, where the agent has the person's real mouse, keyboard, files and accounts.

- `permissions()`: whether macOS lets this process post input (Accessibility) and capture the screen (Screen
  Recording), and what to change in System Settings when it does not. It only reads: nothing is requested or shown.
- `Stopped`: the person stopped the agent (the mouse in a corner of the screen, or Escape held down).
- `risky(...)` and `Denied`: the short list of actions that need the person's yes before they run.

The list covers what can be read from the action itself: key combinations and typed shell commands. A click is a
pair of coordinates, so a click on a Delete, Send or Buy button is not caught, nor is a menu item, a drag to the
Trash, or a command hidden behind an alias or a script. Use a spare account or a VM for anything those could hurt.
"""
from __future__ import annotations

import os
import re
import sys
from typing import Callable

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


def default_confirm() -> Confirm | None:
    """`confirm="auto"`: ask in the terminal when a person is at one; unattended (a benchmark, a VM, an MCP server,
    CI: stdin is not a terminal) there is nobody to ask and nothing is held back. LEGATO_CONFIRM=off turns it off."""
    if os.environ.get("LEGATO_CONFIRM", "").lower() in ("0", "off", "no", "false"):
        return None
    try:
        return ask if sys.stdin is not None and sys.stdin.isatty() else None
    except (ValueError, OSError):
        return None


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
