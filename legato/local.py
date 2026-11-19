"""Drive the computer you are sitting at: your own macOS, Windows or Linux (X11) desktop.

    pip install "legatolabs[local]"
    env = legato.local()
    agent.run("Sort the files on my desktop into folders by type", env)

Screenshots come from `mss` and input from `pyautogui`. The screenshot is scaled to the screen's logical size (half
the pixel size on a Retina Mac), so the model's coordinates are the ones pyautogui clicks. The wait for the screen
polls it locally, without a network round trip. With `wait_scope="app"` (macOS) the wait watches only the windows of
the app in front, so another app animating elsewhere (a terminal spinner, a playing video) no longer holds every
action to the wait's cap.

The agent moves your real mouse and types on your real keyboard. Move the mouse into a corner of the screen, or on
macOS hold Escape, to stop it: the agent's next action raises `legato.safety.Stopped`, which ends `Harness.run` with
`stopped=True`. Actions on the short list in `legato.safety` (cmd+Delete, cmd+Q, a typed `rm` or `sudo`...) wait for
your yes in the terminal (under `legato mcp --local` on macOS, in a dialog). On macOS, give the terminal Screen
Recording and Accessibility permission first (`legato doctor` says whether they are on).
"""
from __future__ import annotations

import hashlib
import io
import re
import subprocess
import sys
import time
from typing import Any

from legato import safety
from legato.actions import Action
from legato.env import Env, Obs
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


def front_windows(quartz: Any) -> tuple[int | None, list[dict]]:
    """(pid, its on-screen windows) of the app in front: the owner of the topmost ordinary window (layer 0), with all
    of that app's windows, its menus and sheets included. Read from the window list, which is current in any process;
    NSWorkspace's frontmost app only updates in a process that runs an AppKit run loop."""
    info = quartz.CGWindowListCopyWindowInfo(quartz.kCGWindowListOptionOnScreenOnly, quartz.kCGNullWindowID) or []
    pid = next((w.get("kCGWindowOwnerPID") for w in info
                if w.get("kCGWindowLayer") == 0 and w.get("kCGWindowAlpha", 1) > 0), None)
    return pid, [w for w in info if pid is not None and w.get("kCGWindowOwnerPID") == pid]


class LocalEnv(Env):
    """This computer's primary screen. `mac_cmd` sends Ctrl shortcuts as Cmd on macOS, since models learn Linux and
    Windows shortcuts (ctrl+c, ctrl+s). `confirm(description) -> bool` is asked before a risky action
    (`legato.safety`): "auto" asks in the terminal when there is one, None never asks. `wait_scope` is what the wait
    for the screen watches: "screen" (all of it) or, on macOS, "app" (only the front app's windows; elsewhere it falls
    back to the screen)."""

    shell = staticmethod(local_shell)

    def __init__(self, mac_cmd: bool = True, fixed_wait_s: float | None = None, *, gui: Any = None, grabber: Any = None,
                 paste: Any = "auto", confirm: safety.Confirm | str | None = "auto", wait_scope: str = "screen",
                 quartz: Any = None, wait: str = "screen"):
        if wait_scope not in ("screen", "app"):
            raise ValueError('wait_scope must be "screen" or "app"')
        self.wait_scope = wait_scope
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
        self.confirm: safety.Confirm | None = safety.default_confirm() if confirm == "auto" else confirm
        self._typed = ""                          # text typed since the last Return or click: what a Return would run
        fs = getattr(self.gui, "FailSafeException", None)
        self._failsafe: tuple = (fs,) if isinstance(fs, type) and issubclass(fs, BaseException) else ()
        self.escape: Any = None                   # is Escape held on the real keyboard: the person's second stop
        self.refocus = self.mac and gui is None   # after a question in the terminal, bring the agent's app back
        if self.mac and gui is None:
            self.escape = safety.escape_held
            for problem in safety.permission_problems():
                print(f"legato: {problem}", file=sys.stderr)
            try:
                self.mackeys, self.macmouse = MacKeys(), MacMouse()
            except Exception:                     # no Quartz: pyautogui's own handling
                self.mackeys = self.macmouse = None
        self.quartz = quartz                      # for wait_scope="app"; imported on first use
        super().__init__(self._png, self._exec, fixed_wait_s=fixed_wait_s)
        # wait: "screen" (every pixel holds still), "cells" (leaving out the cells that move on their own), "ax" (the
        # front app's Accessibility events go quiet, macOS) or "ax+cells" (both)
        if wait not in ("screen", "cells", "ax", "ax+cells", "ax|cells"):
            raise ValueError('wait must be "screen", "cells", "ax", "ax+cells" or "ax|cells"')
        self.wait = wait
        cells = Waiter(self._cells, rules=LOCAL_RULES, background=True) if "cells" in wait else None
        if wait.startswith("ax"):
            from legato.axwait import AXWaiter
            self.waiter = AXWaiter(LOCAL_RULES, pixels=cells, pixels_always=wait != "ax|cells")
        else:
            self.waiter = cells or Waiter(self._digest, rules=LOCAL_RULES)

    def _cells(self, cell: int = 32) -> tuple:
        """A digest for each `cell`-point square of the screen, row by row (Waiter(background=True))."""
        g = self._grab()
        w, h = g.size
        c = max(1, round(cell * w / max(1, self.size[0])))     # cell points in the grab's pixels (Retina: 2x)
        cols, rows = -(-w // c), -(-h // c)
        raw = memoryview(g.raw)
        try:
            import numpy as np
            a = np.frombuffer(raw, dtype=np.uint8).reshape(h, w, 4)
            return tuple(hashlib.blake2b(a[r * c:(r + 1) * c, k * c:(k + 1) * c].tobytes(), digest_size=8).hexdigest()
                         for r in range(rows) for k in range(cols))
        except ImportError:
            hs = [hashlib.blake2b(digest_size=8) for _ in range(cols * rows)]
            for y in range(h):
                base, row = y * w * 4, (y // c) * cols
                for k in range(cols):
                    hs[row + k].update(raw[base + k * c * 4: base + min(w, (k + 1) * c) * 4])
            return tuple(x.hexdigest() for x in hs)

    def _grab(self):
        return self.sct.grab(self.mon)

    def _digest(self) -> str:
        if self.wait_scope == "app" and self.mac:
            d = self._app_digest()
            if d is not None:
                return d
        return hashlib.blake2b(bytes(self._grab().raw), digest_size=16).hexdigest()

    def _app_digest(self) -> str | None:
        """The front app's windows only: where they are and what they show. None when they can't be read."""
        try:
            q = self.quartz
            if q is None:
                import Quartz as q
                self.quartz = q
            pid, wins = front_windows(q)
            h = hashlib.blake2b(digest_size=16)
            h.update(repr((pid, sorted((w.get("kCGWindowNumber"), tuple(sorted(dict(w.get("kCGWindowBounds") or {}).items())))
                                       for w in wins))).encode())
            if wins:
                img = q.CGWindowListCreateImageFromArray(q.CGRectInfinite, [w["kCGWindowNumber"] for w in wins],
                                                         q.kCGWindowImageBoundsIgnoreFraming
                                                         | q.kCGWindowImageNominalResolution)
                if img is None:
                    return None
                h.update(bytes(q.CGDataProviderCopyData(q.CGImageGetDataProvider(img))))
            return h.hexdigest()
        except Exception:  # noqa: BLE001 - the whole screen still works
            return None

    def _png(self) -> bytes:
        from PIL import Image
        g = self._grab()
        im = Image.frombytes("RGB", g.size, g.rgb)
        if im.size != self.size:
            im = im.resize(self.size, Image.BILINEAR)
        buf = io.BytesIO()
        im.save(buf, "PNG", compress_level=1)
        return buf.getvalue()

    # ------------------------------------------------------------ the person's stop and yes
    def stop_requested(self) -> str | None:
        """Why the person wants the agent to stop, or None. Checked before every action, because key presses and
        pasted text on macOS do not go through pyautogui and so never reach its fail-safe."""
        if self.escape is not None and self.escape():
            return "Escape was held down"
        if getattr(self.gui, "FAILSAFE", None) is True:
            try:
                x, y = self.gui.position()
            except Exception:  # noqa: BLE001
                return None
            w, h = self.size
            if x in (0, w - 1) and y in (0, h - 1):
                return "the mouse was moved into a corner of the screen"
        return None

    def _risk(self, a: Action) -> tuple[str | None, str]:
        """(what to ask the person about `a`, or None; the typed text a later Return would run)."""
        t, typed = a.type, self._typed

        def quote(s: str) -> str:
            s = s.strip()
            return f'"{s[:120]}{"…" if len(s) > 120 else ""}"'
        if t == "type":
            typed = (typed + (a.text or "").replace("\r", "\n"))[-4000:]
            if "\n" not in typed:
                return None, typed
            run, _, rest = typed.rpartition("\n")
            why = safety.text_risk(run)
            return (f"type and run {quote(run)} ({why})" if why else None), rest
        if t == "key":
            for combo in (a.keys or "").split():
                names = [self.key(k) for k in combo.split("+") if k]
                if why := safety.keys_risk(names):
                    return f"press {combo} ({why})", typed
                if safety.is_enter(names):
                    if why := safety.text_risk(typed):
                        return f"press Return after typing {quote(typed)} ({why})", ""
                    typed = ""
            return None, typed
        if t in ("click", "double_click", "triple_click", "right_click", "drag"):
            return None, ""                       # the focus may have moved: a later Return runs something else
        return None, typed

    def _front(self) -> str | None:
        """The bundle id of the app in front (macOS), to bring it back after the person answers in the terminal."""
        if not self.refocus:
            return None
        try:
            asn = subprocess.run(["lsappinfo", "front"], capture_output=True, text=True, timeout=5).stdout.strip()
            out = subprocess.run(["lsappinfo", "info", "-only", "bundleid", asn], capture_output=True, text=True,
                                 timeout=5).stdout
            return out.split("=", 1)[1].strip().strip('"') or None
        except (OSError, subprocess.SubprocessError, IndexError):
            return None

    def _refocus(self, bundle: str | None) -> None:
        if bundle:
            try:
                subprocess.run(["open", "-b", bundle], capture_output=True, timeout=10)
                time.sleep(0.5)
            except (OSError, subprocess.SubprocessError):
                pass

    def act(self, action: Action) -> Obs:
        """Run `action`, unless the person has asked to stop (`safety.Stopped`) or says no to it (`safety.Denied`)."""
        if action.done:
            return super().act(action)
        if why := self.stop_requested():
            self._restore_clipboard()
            raise safety.Stopped(why)
        ask, typed = self._risk(action)
        if ask and self.confirm is not None:
            front = self._front()
            allowed = self.confirm(ask)
            self._refocus(front)                  # answering put the terminal or a dialog in front: put the app back
            if not allowed:
                raise safety.Denied(ask)
        self._typed = typed
        try:
            return super().act(action)
        except self._failsafe:
            self._restore_clipboard()
            raise safety.Stopped("the mouse was moved into a corner of the screen") from None

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
                try:
                    g.click(a.x, a.y)
                finally:                          # a stop in the middle must not leave the key held
                    self.mackeys.hold(held, False)
            else:
                for k in held:
                    g.keyDown(k)
                try:
                    g.click(a.x, a.y)
                finally:
                    was = getattr(g, "FAILSAFE", True)
                    g.FAILSAFE = False            # pyautogui checks its fail-safe before keyUp and would skip it
                    for k in reversed(held):
                        g.keyUp(k)
                    g.FAILSAFE = was
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


def local(mac_cmd: bool = True, fixed_wait_s: float | None = None, *,
          confirm: safety.Confirm | str | None = "auto", wait_scope: str = "screen", **options: Any) -> LocalEnv:
    """This computer's screen, mouse and keyboard, for `Harness.run`. `confirm(description) -> bool` is asked before
    a risky action (the list is in `legato.safety`). The default, "auto", asks in the terminal when stdin is one and
    asks nothing in an unattended run; `confirm=None` never asks; `confirm=legato.safety.deny` refuses them all.
    wait_scope="app" (macOS): the wait for the screen watches only the front app's windows. `options` go to LocalEnv
    (wait="screen" | "cells" | "ax" | "ax|cells": what decides that the screen has settled)."""
    return LocalEnv(mac_cmd=mac_cmd, fixed_wait_s=fixed_wait_s, confirm=confirm, wait_scope=wait_scope, **options)
