"""Background mode (macOS): the agent works in one app's window while the person keeps using the Mac.

    env = legato.local(background="TextEdit")
    agent.run("Write a short note and save it as note", env)

The agent works in a window of its own: a new window (a browser's New Window) or a new document (TextEdit's New),
opened through the app's menu when the desktop opens, and the windows that appear from its own actions (a document
it opens, a sheet). It never steers the person's other windows or tabs. Only an app with neither (Calculator, Chess,
Notes) is worked in its front window, and `describe()` says so. It sees only its window, captured even when other
windows cover it, and its coordinates are points in that window. Nothing it does moves the person's pointer, changes
the app in front or touches the clipboard:

- **Typing and shortcuts** are key events posted to the app's process (`CGEventPostToPid`), not to the system.
- **Clicks** go through Accessibility, because a mouse click posted to a process lands nowhere (tested on TextEdit,
  Safari and Chrome, issue #33). The element at the click is pressed (buttons, links, checkboxes, menu items),
  focused (text fields), selected (rows, cells), or, in text, the insertion point is placed at that character.
  Double and triple clicks in text select the word and the line.
- **Scrolling** moves the scroll bar of the area under the point.
- **Reading** (`read()`, the MCP server's `desktop_read`): the window's text from its Accessibility tree, as a short
  outline, so a program can check what it says without a screenshot.

What has no Accessibility equivalent is not run, and the model is told why (`legato.safety.Unavailable`): drags, a
click on something that can't be pressed, focused or selected (a canvas, an empty area), and anything outside the
app (the Dock, the menu bar, Spotlight, other apps). Hiding the app, and the window's close, minimize and zoom
buttons, are refused too: the agent needs the window as it is. The person should not use the same app while the
agent does: its keys go to whatever has focus in that app.

Needs Accessibility and Screen Recording permission for the app that runs Python, and
pyobjc-framework-ApplicationServices (in the `local` extra on macOS).
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import threading
import time
from typing import Any

from legato import safety
from legato.actions import Action
from legato.env import DesktopError, DesktopStartError, Env
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
# shortcuts the system handles, not the app: posted to the app they do nothing (cmd+space then typed "Terminal" into
# the document), so they are refused: (flags, key) -> what it is
SYSTEM_KEYS = {(1 << 20, "space"): "Spotlight", (1 << 18, "space"): "Spotlight or the input source",
               ((1 << 20) | (1 << 19), "space"): "Finder search", (1 << 20, "tab"): "the app switcher",
               ((1 << 20) | (1 << 17), "tab"): "the app switcher", ((1 << 20) | (1 << 19), "esc"): "Force Quit",
               ((1 << 20) | (1 << 18), "q"): "Lock Screen", ((1 << 20) | (1 << 18), "f"): "full screen",
               ((1 << 20) | (1 << 19), "d"): "the Dock", (1 << 18, "up"): "Mission Control",
               (1 << 18, "down"): "App Exposé", (1 << 18, "left"): "switching Spaces",
               (1 << 18, "right"): "switching Spaces"}
# a cmd+N menu item that makes a fresh window ("New Window", "New Finder Window") or document ("New", "New Document");
# others ("New Note", "New Game", "New Playlist") would change the person's data, so they are not pressed
FRESH_WINDOW = re.compile(r"new(\s+\w+)?\s+window", re.I)
FRESH_DOCUMENT = re.compile(r"new((\s+\w+)?\s+document)?", re.I)
# which desktop works in which window, across the desktops of this process (two MCP desktops in one app); other
# Legato processes' windows are in _windows_dir() (_mark, _theirs)
_CLAIMS: dict[tuple[int, int], int] = {}
_MAKING: dict[int, int] = {}              # pid -> desktops opening a window there right now
_LOCK = threading.Lock()
# the title bar's buttons (the window's attribute and the button's subrole have the same name)
WINDOW_BUTTONS = {"AXCloseButton": "close", "AXMinimizeButton": "minimize", "AXZoomButton": "zoom",
                  "AXFullScreenButton": "full screen"}
# read(): the attributes of each element, short role names, and containers that get no line of their own
READ_ATTRS = ["AXRole", "AXTitle", "AXValue", "AXDescription", "AXPlaceholderValue", "AXEnabled", "AXFocused",
              "AXSelected", "AXChildren"]
ROLE_NAMES = {"AXStaticText": "text", "AXTextField": "textbox", "AXTextArea": "textbox",
              "AXSecureTextField": "password", "AXWebArea": "page", "AXPopUpButton": "popup", "AXCheckBox": "checkbox",
              "AXRadioButton": "radio", "AXMenuButton": "menubutton", "AXComboBox": "combobox"}
QUIET_ROLES = {"AXGroup", "AXGenericElement", "AXSplitGroup", "AXScrollArea", "AXLayoutArea", "AXLayoutItem",
               "AXUnknown", "AXMatte", "AXGrowArea", "AXSplitter", "AXScrollBar", "AXValueIndicator"}
CONTROL_ROLES = {"AXButton", "AXLink", "AXCheckBox", "AXRadioButton", "AXPopUpButton", "AXMenuButton", "AXMenuItem",
                 "AXTab", "AXDisclosureTriangle", "AXComboBox", "AXSlider"}
FORMAT_ROLES = {"AXCheckBox", "AXButton", "AXPopUpButton", "AXMenuButton", "AXColorWell", "AXComboBox",
                "AXRadioButton", "AXMenuItem"}
CHROMIUM = re.compile(r"chrom|brave|edge|vivaldi|opera|arc\b", re.I)    # their pages ignore background presses
# Background mode keeps to a window of its own in Safari. In other browsers it can't yet be sure to: in Firefox (24 Nov
# 2026) a run's screenshots and keys reached the person's own Firefox window and changed its selected tab, although
# the new window it worked in was its own. So any other browser is refused, unless window="front" asks for the front
# window on purpose. Known by name or bundle id, else by an Info.plist that opens http links.
OTHER_BROWSERS = re.compile(r"chrom|firefox|mozilla|brave|\bedge(mac)?\b|vivaldi|\bopera\b|\barc\b|thebrowser|"
                            r"librewolf|waterfox|floorp|zen[- ]browser|tor ?browser|orion|duckduckgo|mullvad|sigmaos",
                            re.I)


def other_browser(app: str) -> bool:
    """Is `app` (a name such as "Firefox", or a bundle id) a web browser other than Safari?"""
    if app.strip().lower() in ("safari", "com.apple.safari"):
        return False
    if OTHER_BROWSERS.search(app):
        return True
    try:                                          # an unknown name: does the app say it opens web links?
        from AppKit import NSWorkspace
        from Foundation import NSBundle
        ws = NSWorkspace.sharedWorkspace()
        url = ws.URLForApplicationWithBundleIdentifier_(app) if "." in app and " " not in app else None
        path = url.path() if url is not None else ws.fullPathForApplication_(app)
        info = (NSBundle.bundleWithPath_(path).infoDictionary() or {}) if path else {}
        schemes = {str(x).lower() for t in (info.get("CFBundleURLTypes") or []) for x in (t.get("CFBundleURLSchemes") or [])}
        bid = str(info.get("CFBundleIdentifier") or "")
    except Exception:  # noqa: BLE001 - no AppKit (tests, Linux) or an odd bundle: go by the name alone
        return False
    return bool(schemes & {"http", "https"}) and bid != "com.apple.Safari"
SKIP_ROLES = {"AXRuler", "AXRulerMarker"}           # read() leaves these out with all they hold (TextEdit's ruler)
# Font changes go through the font manager to the first responder of the app's key window, and an app in the back has
# none: in TextEdit the format bar's Bold and Italic look switched on while the text stays Helvetica, and Format >
# Font > Bold is off (checked on 24 Nov through AXAttributedStringForRange). Underline and alignment do apply.
FONT_CONTROLS = {"bold", "italic", "typeface", "style", "font size", "bigger", "smaller", "show fonts", "font family"}


REAL_SUBROLES = {"AXStandardWindow", "AXDialog", "AXSystemDialog", "AXFloatingWindow"}


def _same(f: tuple | None, w: dict) -> bool:
    """Is Accessibility frame `f` the window `w` (screen points, within 2)?"""
    return f is not None and all(abs(a - b) <= 2 for a, b in zip(f, (w["x"], w["y"], w["w"], w["h"])))


def _url(v: Any) -> str:
    """An AXURL (an NSURL, or text) as text."""
    if v is None:
        return ""
    return str(v.absoluteString() if hasattr(v, "absoluteString") else v)


BROWSER_PAGES = ("about:", "chrome:", "chrome-search:", "favorites:", "safari-resource:", "topsites:", "edge:",
                 "brave:", "bookmarks:", "history:")      # a new window's own page, before it goes anywhere


def _is_page(now: str, asked: str) -> bool:
    """Is `now` the page `asked` for, or where it led (a redirect), rather than a new window's own page?"""
    if not now:
        return False
    if now.rstrip("/") == asked.rstrip("/"):
        return True
    return not now.startswith(BROWSER_PAGES)


def _str(v: Any) -> str:
    """A text attribute, trimmed ("" for anything that is not text)."""
    return str(v).strip() if isinstance(v, str) else ""


def _quote(s: str, cap: int = 2000) -> str:
    import json
    return json.dumps(s if len(s) <= cap else s[:cap] + "…", ensure_ascii=False)


def _stall_file():
    from pathlib import Path
    return Path.home() / ".cache" / "legato" / "capture-stalled"


def _windows_dir():
    from pathlib import Path
    return Path.home() / ".cache" / "legato" / "windows"


def _alive(pid: int) -> bool:
    import os
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:                               # someone else's process: alive
        return True
    return True


_SWEPT: list = []


def _mark(name: str) -> None:
    """Note for other Legato processes that this one owns `name` ("<app pid>-<window id>") or is opening a window in
    an app ("making-<app pid>-<this pid>"). Two Legato processes in one Safari took each other's new windows."""
    import os
    try:
        d = _windows_dir()
        d.mkdir(parents=True, exist_ok=True)
        if not _SWEPT:                            # once a process: drop the notes of processes that have ended
            _SWEPT.append(True)
            for f in d.iterdir():
                try:
                    if not _alive(int(f.read_text() or 0)):
                        f.unlink()
                except (OSError, ValueError):
                    pass
        (d / name).write_text(str(os.getpid()))
    except OSError:
        pass


def _unmark(name: str) -> None:
    try:
        (_windows_dir() / name).unlink()
    except OSError:
        pass


def _theirs(app_pid: int, wid: int, making: bool = True) -> bool:
    """Has another live Legato process taken window `wid`, or (making) is one opening a window in this app right now
    (its new window may be this one)?"""
    import os
    me, d = os.getpid(), _windows_dir()
    try:
        names = [f"{app_pid}-{wid}"] + ([p.name for p in d.glob(f"making-{app_pid}-*")] if making else [])
    except OSError:
        return False
    for name in names:
        try:
            f = d / name
            owner = int(f.read_text() or 0)
            if name.startswith("making-") and time.time() - f.stat().st_mtime > 15:
                continue                          # a marker left by a process that died while opening
        except (OSError, ValueError):
            continue
        if owner and owner != me and _alive(owner):
            return True
    return False


def _note_stall() -> None:
    """Remember that window capture stalled, so the next Legato process skips the API's 3 s wait for a while."""
    try:
        f = _stall_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(str(time.time()))
    except OSError:
        pass


def _stall_left(window_s: float) -> float:
    """Seconds left of a recent stall's window (0 if none, or if it can't be read)."""
    try:
        return float(_stall_file().read_text()) + window_s - time.time()
    except (OSError, ValueError):
        return 0.0


def _within(seconds: float, fn):
    """fn() if it returns within `seconds`, else None (it finishes on its own in a background thread)."""
    import threading
    box: list = []
    t = threading.Thread(target=lambda: box.append(fn()), daemon=True)
    t.start()
    t.join(seconds)
    return box[0] if box else None


def _screencapture(window_id: int, rect: tuple[float, float, float, float],
                   hosts: list[tuple[float, float, float, float]] = (), scale: float | None = None) -> bytes | None:
    """PNG of window `window_id` at `rect` (screen points) with macOS's `screencapture`, one pixel per point, or None.

    For a sheet, `screencapture` takes the whole window it hangs from (seen with Chess's New Game sheet): if the image
    is the size of one of `hosts` (the app's windows around `rect`) at the display's `scale`, rect's part is cut out
    of it, so the image still matches the coordinates the model gets."""
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
        x, y, w, h = rect
        size = (int(w), int(h))

        def fits(fw: float, fh: float) -> bool:
            return abs(im.width - fw * scale) <= 2 and abs(im.height - fh * scale) <= 2
        if scale and not fits(w, h):
            host = next(((hx, hy, hw, hh) for hx, hy, hw, hh in hosts if fits(hw, hh)), None)
            if host is not None:
                im = im.crop((round((x - host[0]) * scale), round((y - host[1]) * scale),
                              round((x - host[0] + w) * scale), round((y - host[1] + h) * scale)))
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
        """launch_s: how long to wait for the app to start when it is not running (DesktopStartError after it)."""
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
        # NSWorkspace's list is refreshed only by the main run loop, so in a long-lived process (the MCP server runs
        # tools on worker threads) it can miss an app opened after the first look. Asking by bundle id is always fresh.
        if bid := self._bundle_id():
            from AppKit import NSRunningApplication
            for a in NSRunningApplication.runningApplicationsWithBundleIdentifier_(bid) or []:
                return int(a.processIdentifier())
        return None

    def _bundle_id(self) -> str | None:
        if self.flag == "-b":
            return self.name
        if not hasattr(self, "_bid"):
            from Foundation import NSBundle
            path = self.ws.fullPathForApplication_(self.name)
            bundle = NSBundle.bundleWithPath_(path) if path else None
            self._bid = bundle.bundleIdentifier() if bundle is not None else None
        return self._bid

    def _launch(self, wait_s: float) -> int:
        end = time.monotonic() + wait_s
        try:                                                                  # -g: not in front
            r = subprocess.run(["open", "-g", self.flag, self.name], capture_output=True, text=True,
                               timeout=max(0.5, min(30.0, wait_s)))
        except subprocess.TimeoutExpired:
            raise DesktopStartError(f"{self.name} did not open within {wait_s:g} s") from None
        if r is not None and getattr(r, "returncode", 0) != 0:                # no such app: say so now, not in 30 s
            why = (getattr(r, "stderr", "") or "").strip().splitlines()
            raise DesktopStartError(f"{self.name} could not be opened: {why[-1] if why else 'is it installed?'}")
        while time.monotonic() < end:
            if pid := self._find():
                self.window_until = end          # a cold start: its first window may come seconds after the process
                return pid
            _pump(0.2)
        raise DesktopStartError(f"{self.name} did not open within {wait_s:g} s (is it installed?)")

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
        """The app's on-screen windows, front to back: {"id", "x", "y", "w", "h", "layer", "name"}."""
        q = self.Q
        info = q.CGWindowListCopyWindowInfo(q.kCGWindowListOptionOnScreenOnly, q.kCGNullWindowID) or []
        out = []
        for w in info:
            if w.get("kCGWindowOwnerPID") != self.pid or w.get("kCGWindowAlpha", 1) <= 0:
                continue
            b = w.get("kCGWindowBounds") or {}
            out.append({"id": int(w["kCGWindowNumber"]), "x": float(b.get("X", 0)), "y": float(b.get("Y", 0)),
                        "w": float(b.get("Width", 0)), "h": float(b.get("Height", 0)),
                        "layer": int(w.get("kCGWindowLayer", 0)), "name": str(w.get("kCGWindowName") or "")})
        return out

    API_WAIT_S = 3.0          # longer than this, and macOS's window capture is stalled (it gives up only after 30 s)
    API_OFF_S = 300.0         # then use `screencapture` for this long before trying the API again

    def capture(self, ids: list[int], rect: tuple[float, float, float, float]) -> bytes:
        """PNG of windows `ids` composited in `rect` (screen points), one pixel per point, other apps left out.
        `ids` is the window first, then what is on top of it (menus, popovers), front to back.

        macOS's window capture can stall for 30 s and return nothing while its capture service is busy (seen with a
        screen recorder running and several captures at once), while `screencapture` still works. So the API call
        gets API_WAIT_S, and after a stall the front window is taken with `screencapture` for API_OFF_S."""
        q = self.Q
        if not hasattr(self, "_api_off_until"):        # a stall another Legato process saw in the last minutes
            self._api_off_until = time.monotonic() + max(0.0, _stall_left(self.API_OFF_S))
        if time.monotonic() >= self._api_off_until:
            # the API draws the first window in its list on top: what covers the window goes first, or it is hidden
            img = _within(self.API_WAIT_S, lambda: q.CGWindowListCreateImageFromArray(
                q.CGRectMake(*rect), ids[1:] + ids[:1],
                q.kCGWindowImageBoundsIgnoreFraming | q.kCGWindowImageNominalResolution))
            if img is not None:
                from AppKit import NSBitmapImageRep, NSPNGFileType
                rep = NSBitmapImageRep.alloc().initWithCGImage_(img)
                return bytes(rep.representationUsingType_properties_(NSPNGFileType, None))
            self._api_off_until = time.monotonic() + self.API_OFF_S
            _note_stall()
        x, y, w, h = rect
        hosts = [(v["x"], v["y"], v["w"], v["h"]) for v in self.windows()
                 if v["layer"] == 0 and v["id"] != ids[0] and v["x"] <= x and v["y"] <= y
                 and v["x"] + v["w"] >= x + w and v["y"] + v["h"] >= y + h]
        png = _screencapture(ids[0], rect, hosts, self.scale_at(x + w / 2, y + h / 2) if hosts else None)
        if png is None:
            raise DesktopError("the window could not be captured: check Screen Recording for this app in System "
                               "Settings; if it is on, macOS's screen capture is busy (quit other screen recorders) "
                               "or needs a restart")
        return png

    def scale_at(self, x: float, y: float) -> float | None:
        """Pixels per point of the display at screen point (x, y), or None."""
        q = self.Q
        try:
            err, displays, n = q.CGGetDisplaysWithPoint(q.CGPointMake(x, y), 1, None, None)
            if err or not n:
                return None
            mode = q.CGDisplayCopyDisplayMode(displays[0])
            return q.CGDisplayModeGetPixelWidth(mode) / q.CGDisplayModeGetWidth(mode)
        except Exception:  # noqa: BLE001 - then the image is taken as it comes
            return None

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

    def attrs(self, el: Any, names: list[str]) -> dict:
        """Several attributes in one round trip (a walk over a web page is mostly these). Missing ones are left out."""
        AS = self.AS
        err, vals = AS.AXUIElementCopyMultipleAttributeValues(el, names, 0, None)
        if err != 0 or vals is None:
            return {}
        return {n: v for n, v in zip(names, vals) if v is not None
                and not (isinstance(v, AS.AXValueRef) and AS.AXValueGetType(v) == AS.kAXValueAXErrorType)}

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
    """A window of one app on this Mac, driven without taking the screen (see the module docstring).

    `app` is the app's name ("TextEdit") or bundle id ("com.apple.Safari"); it is started in the background if it is
    not running. window="own" (the default) opens a window or document of its own through the app's menu when it can
    (`describe()` says which); window="front" works in the app's front window, the person's, as before. `close()`
    closes the browser windows it opened. `mac` is the macOS layer (`MacApp`), for tests. `confirm` works as in
    `legato.local`: risky keys and typed commands wait for the person's yes. The person's own stop signals (the mouse
    in a corner, Escape held) don't apply here, since the mouse and keyboard stay theirs: stop the run from the program
    running it."""

    def __init__(self, app: str, mac_cmd: bool = True, fixed_wait_s: float | None = None, *,
                 confirm: safety.Confirm | str | None = "auto", mac: Any = None, launch_s: float = 30.0,
                 window: str = "own"):
        if mac is None and sys.platform != "darwin":
            raise DesktopStartError("background mode is macOS only: use legato.local() or a sandbox elsewhere")
        if window == "own" and other_browser(app):
            raise DesktopStartError(
                f"background mode works in Safari, not {app} yet: in {app} its keys and screenshots can reach your own "
                f"{app} windows instead of the one it opens. Use Safari (--app Safari, or app=\"Safari\"), or the "
                "whole screen (--screen)")
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
        self.m = mac or MacApp(app, launch_s=launch_s)
        self.app = app
        self.win: dict | None = None          # the window the latest screenshot showed
        self._kinds: dict[int, bool] = {}     # window id: is it a real window (True) or a bubble (False)
        self._bubbles: set[int] = set()       # the bubbles in front at the latest look
        self.opened: list = []                # browser windows this desktop opened, which close() closes
        self.own: list[int] = []              # the windows this desktop works in (window ids), oldest first
        self.known: set[int] = set()          # the app's other windows: the person's, or other desktops'
        self.kind = "attached"                # "window" or "document" (its own), or "attached" (the front one)
        self.fresh: Any = None                # its own browser window, before any page was loaded in it
        self._ax: dict[int, Any] = {}         # window id -> its Accessibility element, for the windows it opened
        self._acting = False                  # windows that appear while it acts are its own
        Env.__init__(self, self._png, self._exec, fixed_wait_s=fixed_wait_s)
        self.waiter = Waiter(self._digest, rules=LOCAL_RULES)
        self.last_front: dict = {}
        if window == "own":
            self._open_own()

    # ------------------------------------------------------------ its own window
    def _open_own(self) -> None:
        """Open a window of its own: the app's cmd+N menu item when it makes a fresh window or document. An app that
        just started gets a moment for its menu bar (a restored window of the person's must not be taken instead)."""
        until = getattr(self.m, "window_until", 0.0)
        item = self._menu_item("n", 0)
        while item is None and time.monotonic() < min(until, time.monotonic() + 10.0):
            time.sleep(0.25)
            item = self._menu_item("n", 0)
        title = _str(self.m.attr(item, "AXTitle")) if item is not None else ""
        kind = "window" if FRESH_WINDOW.fullmatch(title) else "document" if FRESH_DOCUMENT.fullmatch(title) else ""
        if not kind or self.m.attr(item, "AXEnabled") is False:
            return                                # attached: its front window, named by describe()
        made = self._make_window(item)
        if made is not None:
            self.kind = kind
            if kind == "window":
                self.opened.append(made)
                self.fresh = made

    def _make_window(self, item: Any) -> Any:
        """Press `item` (New Window, New) and take the window it opens as this desktop's: its Accessibility element,
        or None. The app's windows before it are left alone from then on."""
        import os
        pid = getattr(self.m, "pid", 0)
        with _LOCK:
            _MAKING[pid] = _MAKING.get(pid, 0) + 1
        making = f"making-{pid}-{os.getpid()}"
        _mark(making)
        try:
            before = {w["id"] for w in self.m.windows()}
            had = list(self.m.attr(self.m.app, "AXWindows") or [])
            if not self.m.perform(item, "AXPress"):
                return None
            end = time.monotonic() + 5.0
            while time.monotonic() < end:
                new_ax = [w for w in self.m.attr(self.m.app, "AXWindows") or [] if w not in had]
                if new_ax:
                    frame = self.m.frame(new_ax[0])
                    for w in self.m.windows():
                        if w["id"] not in before and w["layer"] == 0 and (frame is None or _same(frame, w)) \
                                and not _theirs(pid, w["id"], making=False):
                            self.known |= before - set(self.own)
                            self._claim(w["id"])
                            self._ax[w["id"]] = new_ax[0]
                            return new_ax[0]
                time.sleep(0.05)
            return None
        finally:
            with _LOCK:
                _MAKING[pid] -= 1
                last = _MAKING[pid] == 0
            if last:
                _unmark(making)

    def _claim(self, wid: int, exclusive: bool = True) -> None:
        """Take window `wid` as its own. Exclusive (a window it opened, or one that appeared while it acted): no other
        desktop takes it. The front window an attached desktop works in is the person's, so it is not."""
        if exclusive:
            with _LOCK:
                _CLAIMS[(getattr(self.m, "pid", 0), wid)] = id(self)
            _mark(f"{getattr(self.m, 'pid', 0)}-{wid}")
        if wid not in self.own:
            self.own.append(wid)
        self.known.discard(wid)

    def _others(self, wid: int) -> bool:
        """Is window `wid` another desktop's, or about to be (one is opening a window in this app)?"""
        pid = getattr(self.m, "pid", 0)
        with _LOCK:
            if _CLAIMS.get((pid, wid), id(self)) != id(self) or _MAKING.get(pid, 0) > 0:
                return True
        return _theirs(pid, wid)                  # another Legato process's

    def describe(self) -> str:
        """Which window this desktop works in, for the person and the model: 'a new Safari window', 'a new TextEdit
        document ("Untitled 7")', or the front window it was attached to, named."""
        title = ""
        try:
            el = self._ax_window() if (self.win is not None or self.own) else None
            title = _str(self.m.attr(el, "AXTitle")) if el is not None else ""
        except DesktopError:
            pass
        named = f' ("{title}")' if title else ""
        if self.kind == "window":
            return f"a new {self.app} window of its own"
        if self.kind == "document":
            return f"a new {self.app} document{named}"
        return (f"{self.app}'s front window{named}, already open ({self.app} has no New Window or New Document, so "
                "the agent works in it)")

    # ------------------------------------------------------------ seeing
    def window(self) -> dict:
        """The front one of this desktop's own windows (layer 0). A window that appeared while it acted is its own (a
        document it opened, a sheet); the person's windows and other desktops' never are. Attached to an app with
        no window of its own (Calculator), it takes the app's front window on the first look, asking the app for one
        once if it has none on screen. A bubble drawn as a window of its own (TextEdit's autocorrect, Chrome's help
        bubbles) is passed over and drawn over the window under it."""
        for attempt in range(2):
            wins = [w for w in self.m.windows() if w["layer"] == 0 and w["w"] > 40 and w["h"] > 40]
            kinds = [self._is_real(w) for w in wins]
            self._bubbles = {w["id"] for w, k in zip(wins, kinds) if k is False}
            self._adopt(wins, kinds)
            mine = [w for w in wins if w["id"] in self.own]
            if mine:
                self.win = mine[0]
                self.size = (int(self.win["w"]), int(self.win["h"]))
                return self.win
            closed = DesktopError(f"the {self.app} window this desktop worked in was closed (or minimized); the "
                                  "app's other windows are the person's, so open a new desktop")
            if self.own and attempt == 1:
                raise closed
            if attempt == 0:
                unhide = getattr(self.m, "unhide", None)      # hidden with cmd+H: show it again, not in front
                if not (unhide and unhide()):
                    if self.own:
                        raise closed
                    self.m.reopen()
                # 5 s for an app that was running; an app Legato just opened gets the rest of its launch time
                until = max(time.monotonic() + 5.0, getattr(self.m, "window_until", 0.0))
                while time.monotonic() < until:
                    time.sleep(0.2)
                    if any(w["layer"] == 0 for w in self.m.windows()):
                        break
        raise DesktopError(f"{self.app} has no window on screen (is it minimized or on another Space?)")

    def _adopt(self, wins: list[dict], kinds: list) -> None:
        """Take as its own the windows that appeared while it acted, and the sheets of its windows; on the first look
        of an attached desktop, the app's front window."""
        if not self.own and self.kind == "attached":
            free = [(w, k) for w, k in zip(wins, kinds) if not self._others(w["id"])]
            front = next((w for w, k in free if k is not False), free[0][0] if free else None)
            if front is not None:
                self._claim(front["id"], exclusive=False)
                self.known |= {w["id"] for w in wins if w["id"] != front["id"]}
            return
        for w, k in zip(wins, kinds):
            wid = w["id"]
            if k is False or wid in self.own or wid in self.known:
                continue
            if self._others(wid):
                continue                          # another desktop's, or about to be: decided on a later look
            if self._acting or self._sheet_of_own(w):
                self._claim(wid)
            else:
                self.known.add(wid)               # appeared while it did nothing: the person's

    def _sheet_of_own(self, w: dict) -> bool:
        """Is `w` a sheet of the window it works in (a save sheet that came after its action had settled)?"""
        if self.win is None:
            return False
        for el in self.m.attr(self.m.app, "AXWindows") or []:
            if _same(self.m.frame(el), self.win):
                return any(self.m.attr(k, "AXRole") == "AXSheet" and _same(self.m.frame(k), w)
                           for k in self.m.attr(el, "AXChildren") or [])
        return False

    def _is_real(self, w: dict) -> bool | None:
        """True for a window or sheet of the app's own (Accessibility calls it a standard window, a dialog or a
        sheet), False for a bubble or popover drawn as a window, None when Accessibility can't tell. What its
        Accessibility window says is kept per window; "not listed" is asked again (a new window is listed late)."""
        if w["id"] in self._kinds:
            return self._kinds[w["id"]]
        listed = list(self.m.attr(self.m.app, "AXWindows") or [])
        if not listed:
            return None
        for el in listed:
            if _same(self.m.frame(el), w):
                sub = self.m.attr(el, "AXSubrole")
                if sub is None:
                    return None
                self._kinds[w["id"]] = sub in REAL_SUBROLES
                return self._kinds[w["id"]]
            if any(self.m.attr(k, "AXRole") == "AXSheet" and _same(self.m.frame(k), w)
                   for k in self.m.attr(el, "AXChildren") or []):
                self._kinds[w["id"]] = True
                return True
        return False                             # the app lists its windows, and not this one: a bubble

    def _capture(self) -> tuple[dict, bytes]:
        w = self.window()
        try:
            return w, self._take(w)
        except RuntimeError:
            if any(v["id"] == w["id"] for v in self.m.windows()):
                raise
            w = self.window()                    # the window closed between the look and the capture: look again
            return w, self._take(w)

    def _take(self, w: dict) -> bytes:
        x0, y0, x1, y1 = w["x"], w["y"], w["x"] + w["w"], w["y"] + w["h"]
        # its sheets, popovers, menus and bubbles too: the app's other windows that overlap it and are not windows of
        # their own (a layer above, or a bubble in front of it)
        wins = self.m.windows()
        at = next((i for i, v in enumerate(wins) if v["id"] == w["id"]), len(wins))
        ids = [w["id"]] + [v["id"] for i, v in enumerate(wins)   # the window first (listed or not just now: a run
               if v["id"] != w["id"]                               # failed on an empty list), what's on top after
               and v["x"] < x1 and v["x"] + v["w"] > x0 and v["y"] < y1 and v["y"] + v["h"] > y0
               and (v["layer"] != 0 or (i < at and v["id"] in self._bubbles))]
        return self.m.capture(ids, (w["x"], w["y"], w["w"], w["h"]))

    def _png(self) -> bytes:
        return self._capture()[1]

    def _digest(self) -> str:
        w, png = self._capture()
        h = hashlib.blake2b(png, digest_size=16)
        h.update(repr((w["id"], w["x"], w["y"], w["w"], w["h"])).encode())
        return h.hexdigest()

    def read(self, max_chars: int = 8000) -> str:
        """The window's text from its Accessibility tree: one line per element that says something, indented under
        what holds it, such as `- button "+1"` or `- text "Count: 1"`, with states in brackets ([focused],
        [disabled], [checked], [selected]). In a browser, the page only (its title and address first, the browser's
        own toolbar left out). At most `max_chars` characters, the note saying it was cut included."""
        self.window()
        win = self._ax_window()
        if win is None:
            raise DesktopError(f"{self.app}'s window has no Accessibility tree to read")
        page = self._find_role(win, "AXWebArea")
        root = page if page is not None else win
        a = self._attrs(root, ["AXTitle", "AXURL"])
        title = _str(a.get("AXTitle")) or _str(self.m.attr(win, "AXTitle")) or self.app
        head = f"{'page' if page is not None else 'window'} {_quote(title)}"
        if page is not None and (url := _url(a.get("AXURL"))):
            head += f" {url}"
        return self._outline(root, head, max(1, int(max_chars)))

    def _attrs(self, el: Any, names: list[str]) -> dict:
        if hasattr(self.m, "attrs"):
            return self.m.attrs(el, names)
        return {n: v for n in names if (v := self.m.attr(el, n)) is not None}

    def _find_role(self, root: Any, role: str, limit: int = 3000) -> Any:
        """The first element with `role` under `root`, breadth first (a browser's page is near the top)."""
        todo, seen = [root], 0
        while todo and seen < limit:
            node = todo.pop(0)
            seen += 1
            a = self._attrs(node, ["AXRole", "AXChildren"])
            if a.get("AXRole") == role:
                return node
            todo.extend(a.get("AXChildren") or [])
        return None

    def _outline(self, root: Any, head: str, max_chars: int, max_nodes: int = 6000, max_s: float = 8.0) -> str:
        lines, size, seen, cut, last = [head], len(head), 0, "", ""
        note = f"[cut at {max_chars} characters: ask for more with max_chars]"
        if len(note) * 3 > max_chars:
            note = "[cut]"
        end = time.monotonic() + max_s
        a = self._attrs(root, ["AXChildren"])
        todo = [(k, 0, "") for k in reversed(list(a.get("AXChildren") or []))]
        while todo:
            if seen >= max_nodes or time.monotonic() > end:
                cut = f"[stopped after {seen} elements]"
                break
            node, depth, above = todo.pop()
            seen += 1
            a = self._attrs(node, READ_ATTRS)
            role = str(a.get("AXRole") or "")
            if role in SKIP_ROLES:
                continue
            kids = list(a.get("AXChildren") or [])
            label = _str(a.get("AXTitle")) or _str(a.get("AXDescription"))
            value = a.get("AXValue")
            if role == "AXStaticText":
                label, value = _str(value) or label, None
                if not label or label in (above, last):
                    continue                      # empty, or the text of what holds it or of the line before
            elif not label and kids and role not in QUIET_ROLES and len(kids) <= 8:
                texts = [self._attrs(k, ["AXRole", "AXValue"]) for k in kids]
                if all(t.get("AXRole") == "AXStaticText" for t in texts):       # a heading or link: its text
                    label, kids = " ".join(s for t in texts if (s := _str(t.get("AXValue")))), []
            line = self._line(role, label, value, a, bool(kids))
            if line is None:                      # an element that says nothing: its children take its place
                todo.extend((k, depth, above) for k in reversed(kids))
                continue
            line = "  " * depth + line
            if size + len(line) + 1 + len(note) + 1 > max_chars:
                cut = note
                break
            lines.append(line)
            size += len(line) + 1
            last = label
            todo.extend((k, depth + 1, label) for k in reversed(kids))
        if len(lines) == 1 and not cut:
            lines.append("(no text: the window's Accessibility tree is empty; use a screenshot)")
        text = "\n".join(lines + ([cut] if cut else []))
        if len(text) > max_chars:                 # the first line alone is longer (a long address)
            text = text[:max_chars - 6] + "\n[cut]" if max_chars > 12 else text[:max_chars]
        return text

    @staticmethod
    def _line(role: str, label: str, value: Any, a: dict, kids: bool) -> str | None:
        """One element as `- role "label": value [states]`, or None when it says nothing: a container without a
        label, or an element with no label, value, state or children (an icon)."""
        if role in QUIET_ROLES and not label:
            return None
        name = ROLE_NAMES.get(role) or (role[2:].lower() if role.startswith("AX") else role.lower()) or "element"
        out = f"- {name}" + (f" {_quote(label)}" if label else "")
        states = []
        if role in TEXT_ROLES:
            out += f": {_quote(_str(value))}"
            if not _str(value) and _str(a.get("AXPlaceholderValue")):
                states.append(f"placeholder {_quote(_str(a.get('AXPlaceholderValue')))}")
        elif role in ("AXCheckBox", "AXRadioButton", "AXSwitch") and isinstance(value, (bool, int, float)):
            states.append("checked" if value else "not checked")
        elif role == "AXHeading":
            pass                                  # its value is its level
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            out += f": {value:g}"
        elif _str(value) and _str(value) != label:
            out += f": {_quote(_str(value))}"
        if a.get("AXFocused") is True:
            states.append("focused")
        if a.get("AXEnabled") is False:
            states.append("disabled")
        if a.get("AXSelected") is True:
            states.append("selected")
        if not kids and not states and out == f"- {name}":
            return None
        return out + (f" [{', '.join(states)}]" if states else "")

    def stop_requested(self) -> str | None:
        return None

    # ------------------------------------------------------------ acting
    def act(self, action: Action):
        if action.type == "key" and action.keys and HIDES.fullmatch(action.keys.lower().replace("ctrl", "cmd")):
            raise safety.Unavailable(f"{action.keys} would hide or minimize {self.app}'s window, the one this agent "
                                     "works in")
        if action.type == "key" and action.keys:
            for combo in action.keys.split():
                if what := self._system_key(combo):
                    raise safety.Unavailable(f"key {combo}: that is {what}, which the system handles, not {self.app}; "
                                             f"in the background it would only reach {self.app}, and what is typed "
                                             f"next would land in the window. This desktop works in {self.app}'s "
                                             "window only: use its menus and shortcuts, and another desktop for "
                                             "another app")
        before = self.m.front_pid()
        self._acting = True                       # a window that appears now is its own (a document it opens)
        try:
            if action.type in ("key", "type") and self._focus_own() is False:
                raise safety.Unavailable(f"{action.describe()}: another {self.app} window has the keyboard right now "
                                         "(the person's, or another agent's), so the keys were not sent; try again")
            return super().act(action)
        finally:
            self._acting = False
            self.give_back_front(before)

    def _system_key(self, combo: str) -> str | None:
        flags, keys = 0, []
        for n in (self.key(k) for k in combo.split("+") if k):
            if n in FLAGS:
                flags |= FLAGS[n]
            elif n in ("command", "cmd", "win"):
                flags |= FLAGS["command"]
            else:
                keys.append(n.lower())
        return SYSTEM_KEYS.get((flags, keys[0])) if len(keys) == 1 else None

    def _focus_own(self) -> bool | None:
        """Make its window the app's main window, so keys posted to the app (and menu items) reach it rather than
        another window of the app (the person's, or another desktop's): checked on Safari with two windows. False
        when another window still has the keyboard after that (something else made it main), None when it can't be
        told."""
        try:
            self.window()
            el = self._ax_window()
        except DesktopError:
            return None
        if el is None:
            return None
        for _ in range(3):
            if self.m.attr(el, "AXMain") is not True:
                self.m.set(el, "AXMain", True)
            focused = self.m.attr(self.m.app, "AXFocusedWindow")
            if focused is None or focused == el or self._sheet_on(focused, el):
                return True
            time.sleep(0.05)
        return False

    def _sheet_on(self, el: Any, win: Any) -> bool:
        return self.m.attr(el, "AXRole") == "AXSheet" and self.m.attr(el, "AXParent") == win

    def give_back_front(self, before: int | None) -> None:
        """If the app took the front (some apps and browsers do on their own), give it back to the person's app."""
        if before is not None and before != self.m.pid and self.m.front_pid() == self.m.pid:
            self.m.activate(before)
            self.last["front_taken"] = f"{self.app} brought itself to the front; the front was given back"

    def open_url(self, url: str, wait_s: float = 15.0) -> None:
        """Open `url` in this desktop's own browser window (a new one if it has none, or has a page in it already),
        keeping the person's app in front, and return once the page has loaded and has the keyboard focus. The window
        comes from the browser's New Window menu item and the page from its address field, both through
        Accessibility (no Apple Events permission), so the person's tabs stay as they were and out of sight, and a
        query string is kept. `close()` closes the window. If that fails, the URL goes to its window as a tab
        (`open -g`)."""
        before = self.m.front_pid()
        try:
            win, self.fresh = self.fresh, None
            if win is None:
                item = self._menu_item("n", 0)
                if item is not None and FRESH_WINDOW.fullmatch(_str(self.m.attr(item, "AXTitle"))) \
                        and self.m.attr(item, "AXEnabled") is not False:
                    win = self._make_window(item)
                    if win is not None:
                        self.opened.append(win)
                        self.kind = "window"
            if win is not None and self._load(win, url, wait_s):
                return
            self._focus_own()                     # its own window takes the tab, not the person's
            self.m.open_url(url)
            for _ in range(20):                   # a browser that takes the front does so within a second or two
                time.sleep(0.1)
                if self.m.front_pid() == self.m.pid:
                    break
        finally:
            self.give_back_front(before)

    def _load(self, win: Any, url: str, wait_s: float) -> bool:
        """Type `url` into `win`'s address field and confirm it; True once its page has loaded and has the focus (so
        clicks and keys reach the page at once, not the address field)."""
        field = self._find_role(win, "AXTextField", limit=500)
        if field is None:
            return False
        self.m.set(field, "AXFocused", True)
        if not (self.m.set(field, "AXValue", url) and self.m.perform(field, "AXConfirm")):
            return False
        t0 = time.monotonic()
        while time.monotonic() - t0 < wait_s:
            page = self._find_role(win, "AXWebArea", limit=500)
            a = self._attrs(page, ["AXURL", "AXElementBusy", "AXLoaded"]) if page is not None else {}
            now = _url(a.get("AXURL"))
            if _is_page(now, url):
                if a.get("AXElementBusy") is not True and a.get("AXLoaded") is not False:
                    self.m.set(page, "AXFocused", True)
                    return True
            elif time.monotonic() - t0 > 4.0:
                return False                      # still the new window's own page: the field did not take it
            time.sleep(0.05)
        return False

    def close(self) -> None:
        """Close the browser windows it opened (their close button, through Accessibility) and let go of its windows.
        A document it opened stays open, with what the agent wrote in it, and so does the app."""
        while self.opened:
            win = self.opened.pop()
            button = self.m.attr(win, "AXCloseButton")
            if button is not None:
                self.m.perform(button, "AXPress")
        with _LOCK:
            for wid in self.own:
                if _CLAIMS.pop((getattr(self.m, "pid", 0), wid), None) is not None:
                    _unmark(f"{getattr(self.m, 'pid', 0)}-{wid}")

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
        text = text.replace("\r\n", "\n")
        left = self._insert(text)
        self._keys(left)

    def _insert(self, text: str) -> str:
        """Put `text` in the focused text area or field through Accessibility, exactly as given: as key presses, an
        app's automatic capitalisation, spelling and quotes change it (TextEdit wrote "alpha" as "Alpha", and a run
        spent 2.5 minutes retyping a list against it). A field's newlines stay Return presses, which submit it. Not
        in a browser (a web page's scripts listen for keys), a secure field, or an app whose text doesn't change when
        asked (it then gets keys). Returns what is left to type as keys."""
        if not text or self.app.strip().lower() in ("safari", "com.apple.safari") or CHROMIUM.search(self.app):
            return text
        el = self.m.attr(self.m.app, "AXFocusedUIElement")
        role = self.m.attr(el, "AXRole") if el is not None else None
        if role not in ("AXTextArea", "AXTextField", "AXComboBox", "AXSearchField") or self._owns(el) is False:
            return text
        parts = [text] if role == "AXTextArea" else text.split("\n")
        for i, part in enumerate(parts):
            if i:
                self.m.key(KEYCODES["enter"])
            if not part:
                continue
            before = self.m.attr(el, "AXValue")
            if not isinstance(before, str) or not self.m.set(el, "AXSelectedText", part) \
                    or self.m.attr(el, "AXValue") == before:
                return "\n".join(parts[i:])          # it didn't take: keys from here
        return ""

    def _keys(self, text: str) -> None:
        for ch in text:
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

    def _menu_item(self, key: str, flags: int) -> Any:
        """The app's menu item whose shortcut is `flags`+`key`, or None."""
        if len(key) != 1:
            return None
        want = ((1 if flags & FLAGS["shift"] or key.isupper() else 0) | (2 if flags & FLAGS["alt"] else 0)
                | (4 if flags & FLAGS["ctrl"] else 0))
        todo, seen = list(self.m.attr(self.m.app, "AXMenuBar") and [self.m.attr(self.m.app, "AXMenuBar")] or []), 0
        while todo and seen < 4000:
            node = todo.pop()
            seen += 1
            ch = self.m.attr(node, "AXMenuItemCmdChar")
            if ch and str(ch).lower() == key.lower() and int(self.m.attr(node, "AXMenuItemCmdModifiers") or 0) == want:
                return node
            todo.extend(self.m.attr(node, "AXChildren") or [])
        return None

    def _menu(self, key: str, flags: int) -> bool:
        """Press the app's menu item whose shortcut is `flags`+`key` (Select All for cmd+a). An app in the back
        handles key presses posted to it, but its menus never see them. False when no item has that shortcut."""
        node = self._menu_item(key, flags)
        if node is None:
            return False
        if self.m.attr(node, "AXEnabled") is False:
            item = self.m.attr(node, "AXTitle")
            if self._toolbar_instead(_str(item)):
                return True
            if _str(item).lower() in FONT_CONTROLS:
                raise safety.Unavailable(f"key {key}: {self._no_format(item)}")
            if re.search(r"save|export|print|duplicate|revert", _str(item), re.I):
                raise safety.Unavailable(f"key {key}: {item} is turned off while the app is in the back, and stays off: "
                                         "saving, exporting and printing aren't possible in background mode, by key, "
                                         "menu or button. If the task needs it, call fail and say so")
            raise safety.Unavailable(f"key {key}: its menu item ({item}) is turned off while the app is in the back; "
                                     "do it another way")
        return self.m.perform(node, "AXPress")

    def _toolbar_instead(self, item: str) -> bool:
        """Press the window's toolbar button for a menu item that is off in the back: Safari's Reload Page (cmd+R),
        Back and Forward are, while its toolbar's Reload, Back and Forward buttons still work. False if none."""
        word = (item.lower().split() or [""])[0]
        if word not in ("reload", "back", "forward", "stop"):
            return False
        try:
            win = self._ax_window()
        except DesktopError:
            return False
        todo, seen = [win] if win is not None else [], 0
        while todo and seen < 600:
            node = todo.pop(0)
            seen += 1
            a = self._attrs(node, ["AXRole", "AXTitle", "AXDescription", "AXChildren"])
            if a.get("AXRole") == "AXWebArea":
                continue                          # the page's own buttons are not the browser's
            label = f"{_str(a.get('AXDescription'))} {_str(a.get('AXTitle'))}".lower()
            if a.get("AXRole") == "AXButton" and re.search(rf"\b{word}\b", label) and "AXPress" in self.m.actions(node):
                return self.m.perform(node, "AXPress")
            todo.extend(a.get("AXChildren") or [])
        return False

    def _no_format(self, what: Any) -> str:
        return (f"{what or 'font'}: changing the font (bold, italic, typeface, size) isn't possible in the background, "
                f"since {self.app} applies it only while it is the app in front: its controls may look switched on "
                "while the text stays as it was. Don't report it as done: say it was not possible here (it needs the "
                "whole screen)")

    def _format_control(self, el: Any) -> str | None:
        """The name of a font control (TextEdit's format bar: bold, italic, font size...), else None."""
        if el is None or self.m.attr(el, "AXRole") not in FORMAT_ROLES:
            return None
        for name in ("AXDescription", "AXTitle"):
            v = _str(self.m.attr(el, name)).lower()
            if v in FONT_CONTROLS:
                return None if self._in_web_page(el) else v     # a web editor's own buttons do work
        return None

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
        el = self._hit(x, y)
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

    def _hit(self, x: float, y: float) -> Any:
        """The element at screen point (x, y) in its own window. The app's hit test answers for the app's front window
        there, which can be another one (the person's, or another desktop's: a click on desktop 1 pressed desktop 2's
        button): then its window is raised among the app's windows (the app stays in the back) and asked again."""
        el = self.m.element_at(x, y)
        if el is None or self._owns(el) is not False:
            return el
        win = self._ax_window()
        if win is not None and "AXRaise" in self.m.actions(win):
            self.m.perform(win, "AXRaise")            # takes effect within 0.1 s (Safari, two windows)
            end = time.monotonic() + 0.6
            while True:
                el = self.m.element_at(x, y)
                if el is None or self._owns(el) is not False:
                    return el
                if time.monotonic() > end:
                    break
                time.sleep(0.03)
        raise safety.Unavailable("another window covers that point of this desktop's window, so nothing was done "
                                 "there")

    def _owns(self, el: Any) -> bool | None:
        """Is `el` in the window this desktop works in? None when it can't be told."""
        top = self.m.attr(el, "AXWindow") or self.m.attr(el, "AXTopLevelUIElement")
        mine = self._ax_window() if top is not None else None
        if top is None or mine is None:
            return None
        if top == mine:
            return True
        # a sheet of its window: the sheet's own parent is the window
        return self.m.attr(top, "AXRole") == "AXSheet" and self.m.attr(top, "AXParent") == mine

    def _in_web_page(self, el: Any) -> bool:
        node = el
        for _ in range(20):
            node = self.m.attr(node, "AXParent")
            if node is None:
                return False
            if self.m.attr(node, "AXRole") == "AXWebArea":
                return True
        return False

    def _ax_window(self) -> Any:
        """The Accessibility element of the window the screenshots show: the one it opened, else the app's window with
        the same frame (and title, when two have the same frame), else the window a sheet in front hangs from. Never
        a window of the person's in its place."""
        w = self.win or self.window()
        made = self._ax.get(w["id"])
        if made is not None and _same(self.m.frame(made), w):
            return made
        wins = list(self.m.attr(self.m.app, "AXWindows") or [])
        same = [el for el in wins if _same(self.m.frame(el), w)]
        if len(same) > 1 and w.get("name"):
            same = [el for el in same if _str(self.m.attr(el, "AXTitle")) == w["name"]] or same[:1]
        if same:
            return same[0]
        return next((el for el in wins if any(self.m.attr(k, "AXRole") == "AXSheet" and _same(self.m.frame(k), w)
                                                for k in self.m.attr(el, "AXChildren") or [])), None)

    def _window_button(self, x: float, y: float, el: Any = None) -> str | None:
        """"close", "minimize", "zoom" or "full screen" when screen point (x, y), or the element `el` hit there, is
        that button of the window's title bar. The buttons come from the window's own attributes, so they are found
        wherever the app draws them."""
        if el is not None and (sub := self.m.attr(el, "AXSubrole")) in WINDOW_BUTTONS:
            parent = self.m.attr(el, "AXParent")
            if parent is not None and self.m.attr(parent, "AXRole") == "AXWindow":    # not a tab's close button
                return WINDOW_BUTTONS[sub]
        win = self._ax_window()
        for name, what in WINDOW_BUTTONS.items():
            b = self.m.attr(win, name) if win is not None else None
            f = self.m.frame(b) if b is not None else None
            if f is not None and f[0] - 2 <= x < f[0] + f[2] + 2 and f[1] - 2 <= y < f[1] + f[3] + 2:
                return what
        return None

    def _click(self, t: str, x: float, y: float, a: Action) -> None:
        what = a.describe()
        kind, el = self._target(x, y)
        if fmt := self._format_control(el):
            raise safety.Unavailable(f"{what}: {self._no_format(fmt)}")
        if button := self._window_button(x, y, el):
            # pressing them from the back left TextEdit not answering; and the agent needs the window as it is
            raise safety.Unavailable(f"{what}: that is the window's {button} button, which background mode doesn't "
                                     f"press: {self.app}'s window has to stay as it is. When the task is done, say so "
                                     "instead")
        self.target = (kind, None if el is None else f"{self.m.attr(el, 'AXRole')} {self.m.attr(el, 'AXTitle') or ''}"
                       f" {self.m.frame(el) if hasattr(self.m, 'frame') else ''}".strip())
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
            if role in CONTROL_ROLES and CHROMIUM.search(self.app) and self._in_web_page(el):
                name = ROLE_NAMES.get(role) or role[2:].lower()
                who = "Chrome" if re.search("chrom", self.app, re.I) else self.app
                raise safety.Unavailable(f"{what}: {who} ignores background clicks on web pages, so the {name} there "
                                         "was not pressed. Use the keyboard (Tab to it, then space or Return), or open "
                                         'the page in Safari: desktop_open(app="Safari", url=...)')
            there = "nothing at that point can" if role is None else f"the {role} at that point can't"
            raise safety.Unavailable(f"{what}: {there} be pressed, focused or selected in background mode; click a "
                                     "button, link, field or row, or use the keyboard")
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
        el = self._hit(sx, sy)
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
        win = self._ax_window()
        if win is None and self.kind == "attached":     # its window has no frame to match: the app's front one
            win = next(iter(self.m.attr(self.m.app, "AXWindows") or []), None)
        todo, seen = [win] if win is not None else [], 0
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
               confirm: safety.Confirm | str | None = "auto", launch_s: float = 30.0) -> BackgroundEnv:
    """`app`'s front window on this Mac, worked in the background (see the module docstring). launch_s: how long to
    wait for the app to start if it is not running. Raises legato.DesktopStartError when it can't be opened."""
    return BackgroundEnv(app, mac_cmd=mac_cmd, fixed_wait_s=fixed_wait_s, confirm=confirm, launch_s=launch_s)
