"""Background mode (macOS): the agent works in one app's window while the person keeps using the Mac.

    env = legato.local(background="TextEdit")
    agent.run("Write a short note and save it as note", env)

The agent sees only that app's front window, captured even when other windows cover it, and its coordinates are
points in that window. Nothing it does moves the person's pointer, changes the app in front or touches the
clipboard:

- **Typing and shortcuts** are key events posted to the app's process (`CGEventPostToPid`), not to the system.
- **Clicks** go through Accessibility, because a mouse click posted to a process lands nowhere (tested on TextEdit,
  Safari and Chrome, issue #33). The element at the click is pressed (buttons, links, checkboxes, menu items),
  focused (text fields), selected (rows, cells), or, in text, the insertion point is placed at that character.
  Double and triple clicks in text select the word and the line.
- **Scrolling** moves the scroll bar of the area under the point.

What has no Accessibility equivalent is not run, and the model is told why (`legato.safety.Unavailable`): drags, a
click on something that can't be pressed, focused or selected (a canvas, an empty area), and anything outside the
app (the Dock, the menu bar, Spotlight, other apps). The person should not use the same app while the agent does:
its keys go to whatever has focus in that app.

Needs Accessibility and Screen Recording permission for the app that runs Python, and
pyobjc-framework-ApplicationServices (in the `local` extra on macOS).
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import time
from typing import Any

from legato import safety
from legato.actions import Action
from legato.env import Env
from legato.local import LocalEnv
from legato.settle import LOCAL_RULES, Waiter

# macOS virtual key codes (ANSI layout) for the names LocalEnv.key() produces
KEYCODES = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11, "q": 12, "w": 13,
    "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25,
    "7": 26, "-": 27, "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35, "l": 37, "j": 38,
    "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44, "n": 45, "m": 46, ".": 47, "`": 50,
    "enter": 36, "tab": 48, "space": 49, "backspace": 51, "esc": 53, "delete": 117, "home": 115, "end": 119,
    "pageup": 116, "pagedown": 121, "left": 123, "right": 124, "down": 125, "up": 126,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98, "f8": 100, "f9": 101, "f10": 109,
    "f11": 103, "f12": 111,
}
FLAGS = {"command": 1 << 20, "shift": 1 << 17, "alt": 1 << 19, "ctrl": 1 << 18}
SHIFTED = '~!@#$%^&*()_+{}|:"<>?'
TEXT_ROLES = {"AXTextField", "AXTextArea", "AXComboBox", "AXSearchField", "AXSecureTextField"}
STOP_ROLES = {"AXWindow", "AXApplication", "AXWebArea", "AXScrollArea", "AXSheet", "AXDialog"}
PRESS = ("AXPress", "AXPick", "AXConfirm")
HIDES = re.compile(r"(cmd|command|super|meta)(\+(alt|option))?\+[hm]")   # hide the app, minimize the window


def _within(seconds: float, fn):
    """fn() if it returns within `seconds`, else None (it finishes on its own in a background thread)."""
    import threading
    box: list = []
    t = threading.Thread(target=lambda: box.append(fn()), daemon=True)
    t.start()
    t.join(seconds)
    return box[0] if box else None


def _screencapture(window_id: int, size: tuple[int, int]) -> bytes | None:
    """PNG of one window with macOS's `screencapture`, scaled to `size` (points), or None."""
    import io
    import os
    import tempfile
    from PIL import Image
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        r = subprocess.run(["screencapture", "-x", "-o", "-t", "png", f"-l{window_id}", path], capture_output=True,
                           timeout=15)
        data = open(path, "rb").read()
        if r.returncode != 0 or not data:
            return None
        im = Image.open(io.BytesIO(data))
        if size[0] > 0 and size[1] > 0 and im.size != size:
            im = im.resize(size, Image.LANCZOS)
        buf = io.BytesIO()
        im.convert("RGB").save(buf, "PNG")
        return buf.getvalue()
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return None
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _pump(seconds: float) -> None:
    """Wait while letting AppKit catch up. NSWorkspace's list of running apps is only refreshed by the run loop, so in
    a plain `time.sleep` loop an app opened after the first look is never seen (it was: "Safari did not start" with
    Safari running)."""
    from Foundation import NSDate, NSRunLoop
    NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(seconds))


class MacApp:
    """The few macOS calls background mode needs, for one app. Tests swap in a fake with the same methods.

    Coordinates here are screen points, origin at the top left of the main display. Elements are AXUIElements."""

    def __init__(self, app: str, launch_s: float = 30.0):
        try:
            import ApplicationServices as AS
            import Quartz
            from AppKit import NSWorkspace
        except ImportError as e:
            raise ImportError('background mode needs the Mac extras: pip install "legatolabs[local]"') from e
        self.AS, self.Q, self.ws = AS, Quartz, NSWorkspace.sharedWorkspace()
        self.name = app
        self.flag = "-b" if re.fullmatch(r"[\w-]+(\.[\w-]+)+", app) else "-a"     # a bundle id, or a name
        self.pid = self._find() or self._launch(launch_s)
        self.app = AS.AXUIElementCreateApplication(self.pid)
        # Chromium and Electron build their Accessibility tree only when asked: without it a click finds nothing
        AS.AXUIElementSetAttributeValue(self.app, "AXManualAccessibility", True)

    # ------------------------------------------------------------ the app and its window
    def _find(self) -> int | None:
        for a in self.ws.runningApplications():
            if self.name in (a.localizedName(), a.bundleIdentifier()):
                return int(a.processIdentifier())
        return None

    def _launch(self, wait_s: float) -> int:
        subprocess.run(["open", "-g", self.flag, self.name], capture_output=True, timeout=30)    # -g: not in front
        end = time.monotonic() + wait_s
        while time.monotonic() < end:
            if pid := self._find():
                return pid
            _pump(0.2)
        raise RuntimeError(f"{self.name} did not open within {wait_s:.0f} s (is it installed?)")

    def unhide(self) -> bool:
        """Show the app again if it was hidden (cmd+H), without bringing it to the front. True if it was hidden."""
        from AppKit import NSRunningApplication
        a = NSRunningApplication.runningApplicationWithProcessIdentifier_(self.pid)
        if a is not None and a.isHidden():
            a.unhide()
            return True
        return False

    def reopen(self) -> None:
        """Ask the app for a window, as clicking its Dock icon would, without bringing it to the front."""
        subprocess.run(["open", "-g", self.flag, self.name], capture_output=True,
                       timeout=30)

    def windows(self) -> list[dict]:
        """The app's on-screen windows, front to back: {"id", "x", "y", "w", "h", "layer"}."""
        q = self.Q
        info = q.CGWindowListCopyWindowInfo(q.kCGWindowListOptionOnScreenOnly, q.kCGNullWindowID) or []
        out = []
        for w in info:
            if w.get("kCGWindowOwnerPID") != self.pid or w.get("kCGWindowAlpha", 1) <= 0:
                continue
            b = w.get("kCGWindowBounds") or {}
            out.append({"id": int(w["kCGWindowNumber"]), "x": float(b.get("X", 0)), "y": float(b.get("Y", 0)),
                        "w": float(b.get("Width", 0)), "h": float(b.get("Height", 0)),
                        "layer": int(w.get("kCGWindowLayer", 0))})
        return out

    API_WAIT_S = 3.0          # longer than this, and macOS's window capture is stalled (it gives up only after 30 s)
    API_OFF_S = 300.0         # then use `screencapture` for this long before trying the API again

    def capture(self, ids: list[int], rect: tuple[float, float, float, float]) -> bytes:
        """PNG of windows `ids` composited in `rect` (screen points), one pixel per point, other apps left out.

        macOS's window capture can stall for 30 s and return nothing while its capture service is busy (seen with a
        screen recorder running and several captures at once), while `screencapture` still works. So the API call
        gets API_WAIT_S, and after a stall the front window is taken with `screencapture` for API_OFF_S (its sheets
        and menus are then left out)."""
        q = self.Q
        if time.monotonic() >= getattr(self, "_api_off_until", 0.0):
            img = _within(self.API_WAIT_S, lambda: q.CGWindowListCreateImageFromArray(
                q.CGRectMake(*rect), ids, q.kCGWindowImageBoundsIgnoreFraming | q.kCGWindowImageNominalResolution))
            if img is not None:
                from AppKit import NSBitmapImageRep, NSPNGFileType
                rep = NSBitmapImageRep.alloc().initWithCGImage_(img)
                return bytes(rep.representationUsingType_properties_(NSPNGFileType, None))
            self._api_off_until = time.monotonic() + self.API_OFF_S
        png = _screencapture(ids[0], (int(rect[2]), int(rect[3])))
        if png is None:
            raise RuntimeError("the window could not be captured: check Screen Recording for this app in System "
                               "Settings; if it is on, macOS's screen capture is busy (quit other screen recorders) "
                               "or needs a restart")
        return png

    def front_pid(self) -> int | None:
        q = self.Q
        info = q.CGWindowListCopyWindowInfo(q.kCGWindowListOptionOnScreenOnly, q.kCGNullWindowID) or []
        return next((w.get("kCGWindowOwnerPID") for w in info
                     if w.get("kCGWindowLayer") == 0 and w.get("kCGWindowAlpha", 1) > 0), None)

    def activate(self, pid: int) -> None:
        """Bring the app with `pid` back to the front: the person's app, after this one took the front."""
        from AppKit import NSRunningApplication
        a = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        bundle = a.bundleIdentifier() if a is not None else None
        if bundle:
            subprocess.run(["open", "-b", bundle], capture_output=True, timeout=10)

    def open_url(self, url: str) -> None:
        """Open `url` in this app (a browser) without asking for the front. Some browsers (Chrome) take it anyway:
        BackgroundEnv gives it back."""
        subprocess.run(["open", "-g", self.flag, self.name, url], capture_output=True, timeout=30)

    def pointer(self) -> tuple[float, float]:
        p = self.Q.CGEventGetLocation(self.Q.CGEventCreate(None))
        return (p.x, p.y)

    # ------------------------------------------------------------ keys
    def key(self, code: int, flags: int = 0, char: str | None = None) -> None:
        """One key press posted to the app only. With `char`, the event carries that text (any Unicode)."""
        q = self.Q
        for down in (True, False):
            ev = q.CGEventCreateKeyboardEvent(None, code, down)
            q.CGEventSetFlags(ev, flags)
            if char is not None:
                q.CGEventKeyboardSetUnicodeString(ev, len(char), char)
            q.CGEventPostToPid(self.pid, ev)
        time.sleep(0.004)

    # ------------------------------------------------------------ Accessibility
    def element_at(self, x: float, y: float) -> Any:
        err, el = self.AS.AXUIElementCopyElementAtPosition(self.app, x, y, None)
        return el if err == 0 else None

    def attr(self, el: Any, name: str) -> Any:
        err, v = self.AS.AXUIElementCopyAttributeValue(el, name, None)
        return v if err == 0 else None

    def settable(self, el: Any, name: str) -> bool:
        err, ok = self.AS.AXUIElementIsAttributeSettable(el, name, None)
        return err == 0 and bool(ok)

    def set(self, el: Any, name: str, value: Any) -> bool:
        if name == "AXSelectedTextRange":
            import CoreFoundation as CF
            value = self.AS.AXValueCreate(self.AS.kAXValueCFRangeType, CF.CFRangeMake(*value))
        return self.AS.AXUIElementSetAttributeValue(el, name, value) == 0

    def actions(self, el: Any) -> list[str]:
        err, names = self.AS.AXUIElementCopyActionNames(el, None)
        return list(names or []) if err == 0 else []

    def perform(self, el: Any, action: str) -> bool:
        return self.AS.AXUIElementPerformAction(el, action) == 0

    def index_at(self, el: Any, x: float, y: float) -> int | None:
        """The character index in a text element at screen point (x, y), or None (`self.why` says why)."""
        AS = self.AS
        try:
            pt = AS.AXValueCreate(AS.kAXValueCGPointType, self.Q.CGPointMake(float(x), float(y)))
            err, v = AS.AXUIElementCopyParameterizedAttributeValue(el, "AXRangeForPosition", pt, None)
            if err != 0 or v is None:
                self.why = f"AXRangeForPosition: error {err}"
                return None
            ok, r = AS.AXValueGetValue(v, AS.kAXValueCFRangeType, None)
            if not ok:
                self.why = "AXRangeForPosition: no range"
                return None
            return int(r.location if hasattr(r, "location") else r[0])
        except Exception as e:  # noqa: BLE001 - an app without it: the click focuses the field only
            self.why = f"AXRangeForPosition: {e!r}"
            return None

    def frame(self, el: Any) -> tuple[float, float, float, float] | None:
        p, s = self.attr(el, "AXPosition"), self.attr(el, "AXSize")
        if p is None or s is None:
            return None
        ok1, pt = self.AS.AXValueGetValue(p, self.AS.kAXValueCGPointType, None)
        ok2, sz = self.AS.AXValueGetValue(s, self.AS.kAXValueCGSizeType, None)
        return (pt.x, pt.y, sz.width, sz.height) if ok1 and ok2 else None

    def scroll_bar(self, el: Any, vertical: bool) -> Any:
        return self.attr(el, "AXVerticalScrollBar" if vertical else "AXHorizontalScrollBar")


class BackgroundEnv(LocalEnv):
    """One app's front window on this Mac, driven without taking the screen (see the module docstring).

    `app` is the app's name ("TextEdit") or bundle id ("com.apple.Safari"); it is started in the background if it is
    not running. `mac` is the macOS layer (`MacApp`), for tests. `confirm` works as in `legato.local`: risky keys and
    typed commands wait for the person's yes. The person's own stop signals (the mouse in a corner, Escape held)
    don't apply here, since the mouse and keyboard stay theirs: stop the run from the program running it."""

    def __init__(self, app: str, mac_cmd: bool = True, fixed_wait_s: float | None = None, *,
                 confirm: safety.Confirm | str | None = "auto", mac: Any = None):
        if mac is None and sys.platform != "darwin":
            raise RuntimeError("background mode is macOS only: use legato.local() or a sandbox elsewhere")
        self.mac = True                       # LocalEnv: Ctrl shortcuts are sent as Cmd
        self.mac_cmd = mac_cmd
        self.gui = None
        self.paste = None
        self._clip = None
        self._typed = ""
        self._failsafe = ()
        self.escape = None
        self.refocus = False
        self.mackeys = self.macmouse = None
        self.wait_scope, self.wait = "app", "screen"
        self.confirm = safety.default_confirm() if confirm == "auto" else confirm
        self.m = mac or MacApp(app)
        self.app = app
        self.win: dict | None = None          # the window the latest screenshot showed
        Env.__init__(self, self._png, self._exec, fixed_wait_s=fixed_wait_s)
        self.waiter = Waiter(self._digest, rules=LOCAL_RULES)
        self.last_front: dict = {}

    # ------------------------------------------------------------ seeing
    def window(self) -> dict:
        """The app's front window (layer 0), asking the app for one once if it has none on screen."""
        for attempt in range(2):
            wins = [w for w in self.m.windows() if w["layer"] == 0 and w["w"] > 40 and w["h"] > 40]
            if wins:
                self.win = wins[0]
                self.size = (int(self.win["w"]), int(self.win["h"]))
                return self.win
            if attempt == 0:
                unhide = getattr(self.m, "unhide", None)      # hidden with cmd+H: show it again, not in front
                if not (unhide and unhide()):
                    self.m.reopen()
                for _ in range(25):
                    time.sleep(0.2)
                    if any(w["layer"] == 0 for w in self.m.windows()):
                        break
        raise RuntimeError(f"{self.app} has no window on screen (is it minimized or on another Space?)")

    def _capture(self) -> tuple[dict, bytes]:
        w = self.window()
        x0, y0, x1, y1 = w["x"], w["y"], w["x"] + w["w"], w["y"] + w["h"]
        # its sheets, popovers and menus too: any of the app's windows that overlap the front one
        ids = [v["id"] for v in self.m.windows()
               if v["x"] < x1 and v["x"] + v["w"] > x0 and v["y"] < y1 and v["y"] + v["h"] > y0
               and (v["layer"] != 0 or v["id"] == w["id"])]
        ids.sort(key=lambda i: i != w["id"])          # the window first, what's on top of it after
        return w, self.m.capture(ids, (w["x"], w["y"], w["w"], w["h"]))

    def _png(self) -> bytes:
        return self._capture()[1]

    def _digest(self) -> str:
        w, png = self._capture()
        h = hashlib.blake2b(png, digest_size=16)
        h.update(repr((w["id"], w["x"], w["y"], w["w"], w["h"])).encode())
        return h.hexdigest()

    def stop_requested(self) -> str | None:
        return None

    # ------------------------------------------------------------ acting
    def act(self, action: Action):
        if action.type == "key" and action.keys and HIDES.fullmatch(action.keys.lower().replace("ctrl", "cmd")):
            raise safety.Unavailable(f"{action.keys} would hide or minimize {self.app}'s window, the one this agent "
                                     "works in")
        before = self.m.front_pid()
        try:
            return super().act(action)
        finally:
            self.give_back_front(before)

    def give_back_front(self, before: int | None) -> None:
        """If the app took the front (some apps and browsers do on their own), give it back to the person's app."""
        if before is not None and before != self.m.pid and self.m.front_pid() == self.m.pid:
            self.m.activate(before)
            self.last["front_taken"] = f"{self.app} brought itself to the front; the front was given back"

    def open_url(self, url: str) -> None:
        """Open `url` in this app (a browser), keeping the person's app in front."""
        before = self.m.front_pid()
        self.m.open_url(url)
        for _ in range(20):                       # a browser that takes the front does so within a second or two
            time.sleep(0.1)
            if self.m.front_pid() == self.m.pid:
                break
        self.give_back_front(before)

    def _screen(self, x: float, y: float) -> tuple[float, float]:
        w = self.win or self.window()
        return w["x"] + x, w["y"] + y

    def _exec(self, a: Action) -> None:
        t = a.type
        if t in ("click", "double_click", "triple_click", "right_click"):
            if a.x is None or a.y is None:
                raise safety.Unavailable(f"{t} needs x and y")
            self._click(t, *self._screen(a.x, a.y), a)
        elif t == "type":
            self._type(a.text or "")
        elif t == "key":
            for combo in (a.keys or "").split():
                self._chord([self.key(k) for k in combo.split("+") if k], combo)
        elif t == "scroll":
            self._scroll(a)
        elif t == "drag":
            raise safety.Unavailable("drag: background mode can't drag; use the keyboard or the app's menus")
        elif t == "wait":
            time.sleep(1.0)

    def _type(self, text: str) -> None:
        for ch in text.replace("\r\n", "\n"):
            if ch == "\n":
                self.m.key(KEYCODES["enter"])
            elif ch == "\t":
                self.m.key(KEYCODES["tab"])
            else:
                self.m.key(KEYCODES.get(ch.lower(), 0), 0, char=ch)

    def _chord(self, names: list[str], shown: str = "") -> None:
        flags, keys = 0, []
        for n in names:
            if n in FLAGS:
                flags |= FLAGS[n]
            elif n in ("command", "cmd", "win"):
                flags |= FLAGS["command"]
            else:
                keys.append(n)
        if not keys:
            raise safety.Unavailable(f"key {shown}: a modifier on its own does nothing in background mode")
        if flags & FLAGS["command"] and len(keys) == 1:
            k = keys[0].lower()
            if k in ("c", "x", "v") and not flags & ~(FLAGS["command"] | FLAGS["shift"] | FLAGS["alt"]):
                raise safety.Unavailable(f"key {shown}: copy, cut and paste use the clipboard, which is the person's "
                                         "in background mode; type the text instead")
            if k == "a" and flags == FLAGS["command"] and self._select_all():
                return
            if self._menu(keys[0], flags):
                return                            # a menu's shortcut: posted keys don't reach menus in the back
        for k in keys:
            code = KEYCODES.get(k.lower())
            if code is None:
                raise safety.Unavailable(f"key {shown}: {k!r} has no key code here")
            extra = FLAGS["shift"] if len(k) == 1 and (k.isupper() or k in SHIFTED) else 0
            self.m.key(code, flags | extra)

    def _select_all(self) -> bool:
        """Select all the text in the focused field. Select All's menu item is off while the app is in the back."""
        el = self.m.attr(self.m.app, "AXFocusedUIElement")
        if el is None or self.m.attr(el, "AXRole") not in TEXT_ROLES:
            return False
        return self.m.set(el, "AXSelectedTextRange", (0, len(str(self.m.attr(el, "AXValue") or ""))))

    def _menu(self, key: str, flags: int) -> bool:
        """Press the app's menu item whose shortcut is `flags`+`key` (Select All for cmd+a). An app in the back
        handles key presses posted to it, but its menus never see them. False when no item has that shortcut."""
        if len(key) != 1:
            return False
        want = ((1 if flags & FLAGS["shift"] or key.isupper() else 0) | (2 if flags & FLAGS["alt"] else 0)
                | (4 if flags & FLAGS["ctrl"] else 0))
        todo, seen = list(self.m.attr(self.m.app, "AXMenuBar") and [self.m.attr(self.m.app, "AXMenuBar")] or []), 0
        while todo and seen < 4000:
            node = todo.pop()
            seen += 1
            ch = self.m.attr(node, "AXMenuItemCmdChar")
            if ch and str(ch).lower() == key.lower() and int(self.m.attr(node, "AXMenuItemCmdModifiers") or 0) == want:
                if self.m.attr(node, "AXEnabled") is False:
                    raise safety.Unavailable(f"key {key}: its menu item ({self.m.attr(node, 'AXTitle')}) is "
                                             "turned off while the app is in the back; do it another way")
                return self.m.perform(node, "AXPress")
            todo.extend(self.m.attr(node, "AXChildren") or [])
        return False

    # clicks, through Accessibility
    def _kind(self, el: Any) -> str | None:
        role = self.m.attr(el, "AXRole")
        if role in TEXT_ROLES:
            return "text"
        if any(p in self.m.actions(el) for p in PRESS):
            return "press"
        if self.m.settable(el, "AXSelected"):
            return "select"
        return None

    def _in_page(self, el: Any, x: float, y: float) -> Any:
        """In a web page, the smallest element at (x, y) that can be pressed, focused or selected. Chrome's own hit
        test answers with an inline link whose frame spans the whole line, so a button beside it gets the link."""
        page, node = None, el
        for _ in range(15):
            if node is None:
                break
            if self.m.attr(node, "AXRole") == "AXWebArea":
                page = node
                break
            node = self.m.attr(node, "AXParent")
        if page is None:
            return None
        best, best_area, todo, seen = None, None, [page], 0
        while todo and seen < 5000:
            node = todo.pop()
            seen += 1
            f = self.m.frame(node)
            inside = f is not None and f[0] <= x < f[0] + f[2] and f[1] <= y < f[1] + f[3]
            if f is not None and not inside and self.m.attr(node, "AXRole") != "AXWebArea":
                continue                          # nothing under the point down here
            if inside and node is not page and self._kind(node) is not None:
                area = f[2] * f[3]
                if best_area is None or area < best_area:
                    best, best_area = node, area
            todo.extend(self.m.attr(node, "AXChildren") or [])
        return best

    def _target(self, x: float, y: float) -> tuple[str, Any]:
        """(what to do, element) for a click at screen (x, y): "text", "press", "select", or ("none", element)."""
        el = self.m.element_at(x, y)
        if el is None:
            return "none", None
        if (inner := self._in_page(el, x, y)) is not None:
            return self._kind(inner), inner
        hit, depth = el, 0
        while el is not None and depth < 8:
            role = self.m.attr(el, "AXRole")
            if role in TEXT_ROLES:
                return "text", el
            acts = self.m.actions(el)
            if any(p in acts for p in PRESS):
                return "press", el
            if self.m.settable(el, "AXSelected"):
                return "select", el
            if role in STOP_ROLES:
                break
            el, depth = self.m.attr(el, "AXParent"), depth + 1
        return "none", hit

    def _click(self, t: str, x: float, y: float, a: Action) -> None:
        kind, el = self._target(x, y)
        self.target = (kind, None if el is None else f"{self.m.attr(el, 'AXRole')} {self.m.attr(el, 'AXTitle') or ''}"
                       f" {self.m.frame(el) if hasattr(self.m, 'frame') else ''}".strip())
        what = a.describe()
        if t == "right_click":
            node = el
            for _ in range(8):
                if node is None:
                    break
                if "AXShowMenu" in self.m.actions(node):
                    self.m.perform(node, "AXShowMenu")
                    return
                node = self.m.attr(node, "AXParent")
            raise safety.Unavailable(f"{what}: nothing there has a menu in background mode")
        if kind == "text":
            self._click_text(t, el, x, y)
            return
        if kind == "none":
            role = self.m.attr(el, "AXRole") if el is not None else None
            raise safety.Unavailable(f"{what}: {'nothing' if role is None else role} at that point can be pressed, "
                                     "focused or selected in background mode; click a button, link, field or row, "
                                     "or use the keyboard")
        if a.keys:
            raise safety.Unavailable(f"{what}: a click with keys held isn't possible in background mode")
        if t == "triple_click":
            raise safety.Unavailable(f"{what}: a triple click is only possible in text in background mode")
        if kind == "press":
            acts = self.m.actions(el)
            self.m.perform(el, next(p for p in PRESS if p in acts))
            if t == "double_click":
                if "AXOpen" in acts:
                    self.m.perform(el, "AXOpen")
                else:
                    self.m.perform(el, next(p for p in PRESS if p in acts))
            return
        # select
        self.m.set(el, "AXSelected", True)
        if t == "double_click":
            acts = self.m.actions(el)
            if "AXOpen" in acts:
                self.m.perform(el, "AXOpen")
            else:
                raise safety.Unavailable(f"{what}: selected it, but opening it needs a double click, which isn't "
                                         "possible in background mode; press the app's Open shortcut (cmd+o or "
                                         "cmd+Down) instead")

    def _click_text(self, t: str, el: Any, x: float, y: float) -> None:
        """Focus a text element and place the insertion point at (x, y); a double click selects the word there, a
        triple click the line."""
        self.m.set(el, "AXFocused", True)
        i = self.m.index_at(el, x, y)
        if i is None:
            if t != "click":
                why = getattr(self.m, "why", "")
                raise safety.Unavailable(f"{t.replace('_', ' ')}: focused the field, but can't tell which "
                                         f"text is at that point in background mode ({why}); select with the keyboard")
            return
        if t == "click":
            self.m.set(el, "AXSelectedTextRange", (i, 0))
            return
        s = str(self.m.attr(el, "AXValue") or "")
        i = min(i, len(s))
        if t == "double_click":
            a = b = i
            while a > 0 and re.match(r"\w", s[a - 1]):
                a -= 1
            while b < len(s) and re.match(r"\w", s[b]):
                b += 1
        else:
            a = s.rfind("\n", 0, i) + 1
            b = s.find("\n", i)
            b = len(s) if b < 0 else b
        self.m.set(el, "AXSelectedTextRange", (a, b - a))

    def _scroll(self, a: Action) -> None:
        w = self.win or self.window()
        x, y = (a.x, a.y) if a.x is not None and a.y is not None else (w["w"] / 2, w["h"] / 2)
        sx, sy = self._screen(x, y)
        el = self.m.element_at(sx, sy)
        if el is None or self.m.attr(el, "AXRole") in ("AXWindow", "AXApplication"):
            el = self._page_at(sx, sy) or el      # Chrome's hit test can come back empty over its page
        hit = self.m.attr(el, "AXRole") if el is not None else None
        d, n = a.direction or "down", a.amount or 3
        vertical = d in ("up", "down")
        page_action = {"down": "AXScrollDownByPage", "up": "AXScrollUpByPage", "left": "AXScrollLeftByPage",
                       "right": "AXScrollRightByPage"}[d]
        areas, web = [], None
        for _ in range(12):
            if el is None:
                break
            bar = self.m.scroll_bar(el, vertical)
            if bar is not None and self.m.settable(bar, "AXValue"):
                v = float(self.m.attr(bar, "AXValue") or 0.0)
                step = 0.1 * n * (1 if d in ("down", "right") else -1)
                self.m.set(bar, "AXValue", min(1.0, max(0.0, v + step)))
                return
            role = self.m.attr(el, "AXRole")
            if role == "AXWebArea":
                web = web or el
                if vertical and self._scroll_page(el, d, n):
                    return
            if role == "AXScrollArea":
                areas.append(el)
            el = self.m.attr(el, "AXParent")
        page = web or self._page_at(sx, sy)       # Chrome's hit test can answer with an element outside its page
        if web is None and page is not None and vertical and self._scroll_page(page, d, n):
            return
        if page is not None and self.m.set(page, "AXFocused", True):
            # Chrome shows no scroll bar and nothing past the visible edge to Accessibility, but its page scrolls
            # with the arrow keys once it has the focus (about 40 points a press: three per wheel step)
            for _ in range(3 * n):
                self.m.key(KEYCODES[d])
            return
        for area in areas:                        # no scroll bar to move: a page at a time, if the area can
            if page_action in self.m.actions(area):
                self.m.perform(area, page_action)
                return
        raise safety.Unavailable(f"scroll {d}: nothing under that point ({hit or 'nothing'}) scrolls in background "
                                 "mode; try Page Down or the arrow keys")

    def _page_at(self, x: float, y: float) -> Any:
        """The web page (AXWebArea) in the app's windows that covers screen point (x, y), if any."""
        todo, seen = list(self.m.attr(self.m.app, "AXWindows") or [])[:1], 0
        while todo and seen < 3000:
            node = todo.pop()
            seen += 1
            if self.m.attr(node, "AXRole") == "AXWebArea":
                f = self.m.frame(node)
                if f is not None and f[0] <= x < f[0] + f[2] and f[1] <= y < f[1] + f[3]:
                    return node
                continue
            todo.extend(self.m.attr(node, "AXChildren") or [])
        return None


    def _scroll_page(self, page: Any, d: str, n: int) -> bool:
        """Scroll a web page that shows no scroll bar (Chrome): bring the element about `n` tenths of a screen past
        the visible edge into view. False when there is none."""
        view = self.m.frame(self.m.attr(page, "AXParent") or page) or self.m.frame(page)
        if view is None:
            return False
        top, bottom = view[1], view[1] + view[3]
        goal = bottom + 0.1 * n * view[3] if d == "down" else top - 0.1 * n * view[3]
        best, gap, todo, seen = None, None, list(self.m.attr(page, "AXChildren") or []), 0
        while todo and seen < 5000:
            node = todo.pop()
            seen += 1
            f = self.m.frame(node)
            if f is not None and f[3] > 0 and f[3] < view[3]:
                edge = f[1] + f[3] if d == "down" else f[1]
                past = edge > bottom + 1 if d == "down" else edge < top - 1
                if past and "AXScrollToVisible" in self.m.actions(node) and (gap is None or abs(edge - goal) < gap):
                    best, gap = node, abs(edge - goal)
            todo.extend(self.m.attr(node, "AXChildren") or [])
        if best is None:
            return False
        return self.m.perform(best, "AXScrollToVisible")


def background(app: str, mac_cmd: bool = True, fixed_wait_s: float | None = None, *,
               confirm: safety.Confirm | str | None = "auto") -> BackgroundEnv:
    """`app`'s front window on this Mac, worked in the background (see the module docstring)."""
    return BackgroundEnv(app, mac_cmd=mac_cmd, fixed_wait_s=fixed_wait_s, confirm=confirm)
