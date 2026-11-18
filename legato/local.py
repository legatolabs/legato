"""Drive the computer you are sitting at: your own macOS, Windows or Linux (X11) desktop.

    pip install "legatolabs[local]"
    env = legato.local()
    agent.run("Sort the files on my desktop into folders by type", env)

Screenshots come from `mss` and input from `pyautogui`. The screenshot is scaled to the screen's logical size (half
the pixel size on a Retina Mac), so the model's coordinates are the ones pyautogui clicks. The wait for the screen
polls it locally, without a network round trip.

The agent moves your real mouse and types on your real keyboard. Move the mouse into a corner of the screen to stop
it (pyautogui's fail-safe raises in the agent's next action). On macOS, give the terminal Screen Recording and
Accessibility permission first.
"""
from __future__ import annotations

import hashlib
import io
import re
import subprocess
import sys
import time
from typing import Any

from legato.actions import Action
from legato.env import Env
from legato.settle import LOCAL_RULES, Waiter
from legato.tools import local_shell

# legato's parser emits xdotool key names; pyautogui has its own
KEYS = {"Return": "enter", "Escape": "esc", "Tab": "tab", "BackSpace": "backspace", "Delete": "delete",
        "space": "space", "Up": "up", "Down": "down", "Left": "left", "Right": "right", "Prior": "pageup",
        "Next": "pagedown", "Home": "home", "End": "end", "Insert": "insert", "ctrl": "ctrl", "alt": "alt",
        "shift": "shift"}


KEYED = re.compile(r"[0-9:./]{1,10}|[A-Za-z]{1,2}")     # on macOS: text typed key by key instead of pasted


class MacKeys:
    """Key presses on macOS as events that carry their own modifier flags.

    pyautogui holds a modifier down with one event and presses the key with another, trusting the system to
    combine them. On macOS that state can go wrong for the rest of the session (after cmd+End, no later cmd
    shortcut arrived: cmd+v typed nothing or a plain "v"). An event with explicit flags depends on no earlier
    event, and a plain key press clears whatever was left over."""
    MODS = {"command": "kCGEventFlagMaskCommand", "cmd": "kCGEventFlagMaskCommand", "shift": "kCGEventFlagMaskShift",
            "alt": "kCGEventFlagMaskAlternate", "option": "kCGEventFlagMaskAlternate",
            "ctrl": "kCGEventFlagMaskControl", "control": "kCGEventFlagMaskControl"}

    CODES = {"kCGEventFlagMaskCommand": 55, "kCGEventFlagMaskShift": 56, "kCGEventFlagMaskAlternate": 58,
             "kCGEventFlagMaskControl": 59}               # the left-hand modifier keys

    def __init__(self, quartz: Any = None, mapping: dict | None = None):
        if quartz is None:
            import Quartz as quartz                       # pyautogui's own dependency on macOS
            from pyautogui import _pyautogui_osx as osx
            mapping = osx.keyboardMapping
        self.q, self.map = quartz, mapping or {}

    def hold(self, names: list[str], down: bool) -> None:
        """Press (or release) modifier keys themselves, for a click made while they are down. A mouse event that
        only carries the flag left the modifier on for later clicks; a key event for the modifier does not."""
        flags = 0
        for n in names:
            flags |= getattr(self.q, self.MODS[n])
        for n in names:
            ev = self.q.CGEventCreateKeyboardEvent(None, self.CODES[self.MODS[n]], down)
            self.q.CGEventSetFlags(ev, flags if down else 0)
            self.q.CGEventPost(self.q.kCGHIDEventTap, ev)
            time.sleep(0.05)

    def chord(self, names: list[str]) -> bool:
        """Press the keys in `names` with its modifiers held. False if a key has no code here (the caller falls
        back to pyautogui)."""
        flags, keys = 0, []
        for n in names:
            if n in self.MODS:
                flags |= getattr(self.q, self.MODS[n])
            else:
                keys.append(n)
        if not keys or any(self.map.get(k) is None for k in keys):
            return False
        for k in keys:
            extra = self.q.kCGEventFlagMaskShift if len(k) == 1 and (k.isupper() or k in '~!@#$%^&*()_+{}|:"<>?') else 0
            for down in (True, False):
                ev = self.q.CGEventCreateKeyboardEvent(None, self.map[k], down)
                self.q.CGEventSetFlags(ev, flags | extra)
                self.q.CGEventPost(self.q.kCGHIDEventTap, ev)
                time.sleep(0.01)
        return True


class MacMouse:
    """Double and triple clicks on macOS as events that carry their click count.

    pyautogui's multi-click sends plain clicks and trusts the system to count them. In practice Finder then sees
    separate single clicks (a folder is selected, never opened). An event with kCGMouseEventClickState set is a
    double click whatever the timing."""

    def __init__(self, quartz: Any = None):
        if quartz is None:
            import Quartz as quartz
        self.q = quartz

    def click(self, x: float, y: float, count: int) -> None:
        q = self.q
        for n in range(1, count + 1):
            for kind in (q.kCGEventLeftMouseDown, q.kCGEventLeftMouseUp):
                ev = q.CGEventCreateMouseEvent(None, kind, (x, y), q.kCGMouseButtonLeft)
                q.CGEventSetIntegerValueField(ev, q.kCGMouseEventClickState, n)
                q.CGEventPost(q.kCGHIDEventTap, ev)
                time.sleep(0.03)


def _need(name: str):
    try:
        return __import__(name)
    except ImportError as e:
        raise ImportError(f'driving this computer needs {name}: pip install "legatolabs[local]"') from e
    except SystemExit as e:                       # pyautogui exits on import on Linux without tkinter
        raise ImportError(f"{name} could not start: {e}") from None


def no_dock_icon() -> bool:
    """Take this process out of the Dock. A Python that macOS treats as an app (Homebrew's and python.org's framework
    builds) gets a Dock icon once it drives the mouse and keyboard, and the icon keeps animating, so the screen never
    holds still and every wait runs to its cap (68% of actions on the macOS task VMs). The driver needs no icon, menu
    or window. Returns whether it could (AppKit is there whenever pyautogui runs on a Mac)."""
    try:
        import AppKit
        app = AppKit.NSApplication.sharedApplication()
        return bool(app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyProhibited))
    except Exception:  # noqa: BLE001 - no AppKit: nothing to hide
        return False


class LocalEnv(Env):
    """This computer's primary screen. `mac_cmd` sends Ctrl shortcuts as Cmd on macOS, since models learn Linux and
    Windows shortcuts (ctrl+c, ctrl+s)."""

    shell = staticmethod(local_shell)

    def __init__(self, mac_cmd: bool = True, fixed_wait_s: float | None = None, *, gui: Any = None, grabber: Any = None,
                 paste: Any = "auto"):
        if sys.platform == "darwin" and gui is None:
            no_dock_icon()
        self.gui = gui or _need("pyautogui")
        self.gui.PAUSE = 0.02                     # pyautogui sleeps 0.1 s after every call by default
        self.sct = grabber or _need("mss").mss()
        self.mon = self.sct.monitors[1]           # the primary screen, where pyautogui's (0, 0) is
        self.size = tuple(self.gui.size())        # logical size: what pyautogui clicks in
        self.mac = sys.platform == "darwin"
        self.mac_cmd = mac_cmd
        # how text is typed on macOS: pasted (the default), or key by key with paste=None
        self.paste = self._paste_mac if paste == "auto" else paste
        self._clip: bytes | None = None           # clipboard contents to put back before the next action
        self.mackeys: Any = None                  # explicit-flag key events: only with the real pyautogui on a Mac
        self.macmouse: Any = None                 # explicit-count double and triple clicks, likewise
        if self.mac and gui is None:
            try:
                self.mackeys, self.macmouse = MacKeys(), MacMouse()
            except Exception:                     # no Quartz: pyautogui's own handling
                self.mackeys = self.macmouse = None
        super().__init__(self._png, self._exec, fixed_wait_s=fixed_wait_s)
        self.waiter = Waiter(self._digest, rules=LOCAL_RULES)

    def _grab(self):
        return self.sct.grab(self.mon)

    def _digest(self) -> str:
        return hashlib.blake2b(bytes(self._grab().raw), digest_size=16).hexdigest()

    def _png(self) -> bytes:
        from PIL import Image
        g = self._grab()
        im = Image.frombytes("RGB", g.size, g.rgb)
        if im.size != self.size:
            im = im.resize(self.size, Image.BILINEAR)
        buf = io.BytesIO()
        im.save(buf, "PNG", compress_level=1)
        return buf.getvalue()

    def key(self, name: str) -> str:
        if name == "super":
            return "command" if self.mac else "win"
        if name == "ctrl" and self.mac and self.mac_cmd:
            return "command"
        if name in KEYS:
            return KEYS[name]
        if len(name) > 1 and name[0] in "Ff" and name[1:].isdigit():
            return name.lower()
        return name if len(name) == 1 else name.lower()

    def _type(self, text: str) -> None:
        if self.mac and self.paste is not None:
            # On macOS, text typed key by key is rewritten by autocorrect ("eggs" becomes "Eggs", "- " a dash), can
            # lose letters, and leaves a correction bubble open. Pasted text arrives exactly as given. The text is
            # pasted in one piece (one paste per line let an app read the clipboard after the next line was already
            # on it), and Return is pressed for each newline it ends with, so "query\n" still submits a field.
            # A date or time field (Calendar's hour, AM/PM) takes key presses and ignores a paste, and autocorrect
            # leaves a number or a letter or two alone: those are typed.
            body = text.rstrip("\n")
            if body and KEYED.fullmatch(body):
                self._keys(body)
            elif body:
                self.paste(body)
            for _ in range(len(text) - len(body)):
                self._chord(["enter"])
            return
        if text.isascii():
            self.gui.write(text, interval=0.005)
            return
        try:                                      # pyautogui types ASCII only; paste anything else
            import pyperclip
        except ImportError:
            self.gui.write(text, interval=0.005)
            return
        pyperclip.copy(text)
        self.gui.hotkey("command" if self.mac else "ctrl", "v")

    def _keys(self, text: str) -> None:
        """Type `text` one key press at a time."""
        if self.mackeys is None or any(self.mackeys.map.get(c) is None for c in text):
            self.gui.write(text, interval=0.005)
            return
        for c in text:
            self.mackeys.chord([c])

    def _chord(self, names: list[str]) -> None:
        if not names or (self.mackeys is not None and self.mackeys.chord(names)):
            return
        if len(names) > 1:
            self.gui.hotkey(*names)
        else:
            self.gui.press(names[0])

    def _paste_mac(self, text: str) -> None:
        """Put `text` on the clipboard and paste it. The text that was on the clipboard is put back before the next
        action, not now: the app reads the clipboard when it gets to the keys, which can be later than any wait
        here (other clipboard kinds, such as an image, are not restored)."""
        try:
            before = subprocess.run(["pbpaste"], capture_output=True, timeout=5).stdout
            subprocess.run(["pbcopy"], input=text.encode(), timeout=5, check=True)
        except (OSError, subprocess.SubprocessError):
            self.gui.write(text, interval=0.005)      # no clipboard tools: type it
            return
        if self._clip is None:                        # several pastes in a row: keep the oldest contents
            self._clip = before
        self._chord(["command", "v"])
        time.sleep(0.4)

    def _restore_clipboard(self) -> None:
        if self._clip is not None:
            try:
                subprocess.run(["pbcopy"], input=self._clip, timeout=5)
            except (OSError, subprocess.SubprocessError):
                pass
            self._clip = None

    def _exec(self, a: Action) -> None:
        g, t = self.gui, a.type
        if t != "type":
            self._restore_clipboard()
        w, h = self.size                          # keep the agent's own pointer out of the corners: a corner is the
        for kx, ky in (("x", "y"), ("x2", "y2")):  # person's stop signal (pyautogui's fail-safe), not the model's
            x, y = getattr(a, kx, None), getattr(a, ky, None)
            if x is not None and y is not None and x in (0, w - 1) and y in (0, h - 1):
                setattr(a, kx, min(max(x, 1), w - 2))
                setattr(a, ky, min(max(y, 1), h - 2))
        if t == "click" and a.keys:               # a click with keys held (shift or cmd adds to a selection)
            held = [self.key(k) for k in a.keys.split("+") if k]
            if self.mackeys is not None and all(k in MacKeys.MODS for k in held):
                self.mackeys.hold(held, True)
                g.click(a.x, a.y)
                self.mackeys.hold(held, False)
            else:
                for k in held:
                    g.keyDown(k)
                g.click(a.x, a.y)
                for k in reversed(held):
                    g.keyUp(k)
        elif t == "click":
            g.click(a.x, a.y)
        elif t in ("double_click", "triple_click"):
            n = 2 if t == "double_click" else 3
            if self.macmouse is not None:
                g.moveTo(a.x, a.y)
                self.macmouse.click(a.x, a.y, n)
            elif n == 2:
                g.doubleClick(a.x, a.y)
            else:
                g.click(a.x, a.y, clicks=3, interval=0.06)
        elif t == "right_click":
            g.rightClick(a.x, a.y)
        elif t == "type":
            self._type(a.text or "")
        elif t == "key":
            for combo in (a.keys or "").split():
                self._chord([self.key(k) for k in combo.split("+") if k])
        elif t == "scroll":
            n, d = a.amount or 3, a.direction or "down"
            if a.x is not None and a.y is not None:
                g.moveTo(a.x, a.y)
            if d in ("up", "down"):
                g.scroll(n if d == "up" else -n)
            else:
                g.hscroll(n if d == "right" else -n)
        elif t == "drag":
            g.moveTo(a.x, a.y)
            g.dragTo(a.x2, a.y2, duration=0.3, button="left")
        elif t == "wait":
            time.sleep(1.0)


def local(mac_cmd: bool = True, fixed_wait_s: float | None = None) -> LocalEnv:
    """This computer's screen, mouse and keyboard, for `Harness.run`."""
    return LocalEnv(mac_cmd=mac_cmd, fixed_wait_s=fixed_wait_s)
