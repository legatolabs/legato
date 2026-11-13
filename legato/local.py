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
import sys
import time
from typing import Any

from legato.actions import Action
from legato.env import Env
from legato.settle import LOCAL_RULES, Waiter

# legato's parser emits xdotool key names; pyautogui has its own
KEYS = {"Return": "enter", "Escape": "esc", "Tab": "tab", "BackSpace": "backspace", "Delete": "delete",
        "space": "space", "Up": "up", "Down": "down", "Left": "left", "Right": "right", "Prior": "pageup",
        "Next": "pagedown", "Home": "home", "End": "end", "Insert": "insert", "ctrl": "ctrl", "alt": "alt",
        "shift": "shift"}


def _need(name: str):
    try:
        return __import__(name)
    except ImportError as e:
        raise ImportError(f'driving this computer needs {name}: pip install "legatolabs[local]"') from e
    except SystemExit as e:                       # pyautogui exits on import on Linux without tkinter
        raise ImportError(f"{name} could not start: {e}") from None


class LocalEnv(Env):
    """This computer's primary screen. `mac_cmd` sends Ctrl shortcuts as Cmd on macOS, since models learn Linux and
    Windows shortcuts (ctrl+c, ctrl+s)."""

    def __init__(self, mac_cmd: bool = True, fixed_wait_s: float | None = None, *, gui: Any = None, grabber: Any = None):
        self.gui = gui or _need("pyautogui")
        self.gui.PAUSE = 0.02                     # pyautogui sleeps 0.1 s after every call by default
        self.sct = grabber or _need("mss").mss()
        self.mon = self.sct.monitors[1]           # the primary screen, where pyautogui's (0, 0) is
        self.size = tuple(self.gui.size())        # logical size: what pyautogui clicks in
        self.mac = sys.platform == "darwin"
        self.mac_cmd = mac_cmd
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

    def _exec(self, a: Action) -> None:
        g, t = self.gui, a.type
        if t == "click":
            g.click(a.x, a.y)
        elif t == "double_click":
            g.doubleClick(a.x, a.y)
        elif t == "right_click":
            g.rightClick(a.x, a.y)
        elif t == "type":
            self._type(a.text or "")
        elif t == "key":
            for combo in (a.keys or "").split():
                names = [self.key(k) for k in combo.split("+") if k]
                if len(names) > 1:
                    g.hotkey(*names)
                elif names:
                    g.press(names[0])
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
