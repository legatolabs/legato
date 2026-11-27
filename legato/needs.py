"""Experimental: start the next action as soon as what it needs is true, not when the whole screen has settled.

When the action after this one is a key or text, it does not need the screen drawn. It needs the keyboard to be
where the plan means it to be: the program the click opened is in front, the field the shortcut went to has the
focus with its old contents selected, the text just typed is in the field. Those are read from the computer (UI
Automation on Windows) and raced against the ordinary wait:

    obs, how = act_then(env, action, nxt, reader)        # how: "ready", "settled", "capped", or None (not raced)

The input is sent once. If what the next action needs comes true first, the wait ends ("ready"). If the screen
settles first, or the cap runs out, the result is the ordinary one ("settled", "capped"): never later than before.
Nothing here asks the planner for anything; the rules use the action, the one after it, and what was under the
pointer or had the focus before it. An action followed by a click, a drag or a scroll is not raced: those need pixels.
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from legato.actions import Action
from legato.settle import LOCAL_RULES, quiet_enough, rule_kind

EDIT, COMBO, DOCUMENT = 50004, 50003, 50030          # UI Automation control types
TASKBAR = ("Shell_TrayWnd", "Shell_SecondaryTrayWnd")
NOISE = {"pinned", "running", "window", "windows", "item", "items"}
ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍﻿"))
INERT = {"backspace", "delete", "home", "end", "left", "right", "up", "down", "tab", "shift+tab", "ctrl+a"}


@dataclass
class Focus:
    """The control with the keyboard focus: an identity that changes when the focus moves, its kind, the process and
    top-level window it belongs to, and for a text field its value and whether all of it is selected."""
    ident: tuple = ()
    kind: int = 0
    pid: int = 0
    value: str | None = None
    all_selected: bool = False
    rect: tuple = (0, 0, 0, 0)


@dataclass
class Before:
    """The computer just before an action: the window in front, the focus, and what was under the pointer."""
    hwnd: int = 0
    pid: int = 0
    title: str = ""
    focus: Focus = field(default_factory=Focus)
    target: str = ""              # the name of the control under a click
    target_in_taskbar: bool = False


def clean(s: str) -> str:
    return str(s or "").translate(ZERO_WIDTH).lower()


def app_words(name: str) -> list[str]:
    """The words of a taskbar button's name that say which program it is ("Microsoft Edge - 1 running window")."""
    return [w for w in re.findall(r"[a-z]{3,}", clean(name).split(" - ")[0]) if w not in NOISE]


def need(action: Action, nxt: Any, before: Before, reader: Any) -> tuple[str, Callable[[], bool]] | None:
    """(rule, check) for what `nxt` needs once `action` has worked, or None when nothing readable says so."""
    if not isinstance(nxt, Action) or nxt.type not in ("key", "type"):
        return None
    if action.type == "click" and before.target_in_taskbar and app_words(before.target):
        words = app_words(before.target)

        def program_in_front() -> bool:
            hwnd, pid, title, cls = reader.front()
            if not hwnd or hwnd == before.hwnd or cls in TASKBAR:
                return False
            if not all(w in clean(title) for w in words):
                return False
            return reader.focus().pid == pid          # and it has the keyboard, not a window still on its way
        return "program in front", program_in_front
    if action.type == "key" and nxt.type == "type":
        combo = "+".join(k.strip().lower() for k in str(action.keys or "").split("+") if k.strip())
        if combo in INERT or "+" not in combo:
            return None                               # only a shortcut moves the focus to a field

        def field_ready() -> bool:
            hwnd, pid, _title, _cls = reader.front()
            if hwnd != before.hwnd:                   # another window took the keyboard: not the intended field
                return False
            f = reader.focus()
            if f.pid != pid or f.ident == before.focus.ident or f.kind not in (EDIT, COMBO):
                return False
            return f.value == "" or f.all_selected    # typing replaces what was there, or there was nothing
        return "field focused", field_ready
    if action.type == "type" and nxt.type == "key" and action.text and "\n" not in action.text:
        text = action.text

        def entered() -> bool:
            f = reader.focus()
            return f.pid == before.pid and f.value is not None and text in f.value
        return "text entered", entered
    return None


def act_then(env: Any, action: Action, nxt: Any, reader: Any, *, fixed_s: float | None = None,
             digest: Callable[[], Any] | None = None) -> tuple[Any, str | None, str | None]:
    """Send `action` once on `env` and wait until what `nxt` needs is true, raced against the ordinary wait (or
    against `fixed_s`, the short pause a quick action takes). Returns (observation, rule, how); rule and how are
    None when the action was not raced and went the ordinary way."""
    digest = digest or getattr(env, "_digest", None)
    if reader is None or digest is None or action.done:
        return _ordinary(env, action, fixed_s), None, None
    try:
        before = reader.before(action)
        found = need(action, nxt, before, reader)
    except Exception:  # noqa: BLE001 - a reader that fails says nothing: the ordinary wait
        found = None
    if found is None:
        return _ordinary(env, action, fixed_s), None, None
    rule, check = found
    r = LOCAL_RULES.get(rule_kind(action.type)) or LOCAL_RULES["default"]
    cap = fixed_s if fixed_s is not None else float(r.get("cap", 2.0))
    st: dict = {}

    def probe() -> str | None:
        now = time.monotonic()
        try:
            if check():
                return "ready"
        except Exception:  # noqa: BLE001 - unreadable this time: keep waiting the ordinary way
            pass
        if fixed_s is not None:
            return None                               # the short pause has no settle test: it ends at its time
        d = digest()
        if "t0" not in st:
            st.update(t0=now, last=d, t_change=now, same=1)
        elif d != st["last"]:
            st.update(last=d, t_change=now, same=1)
        else:
            st["same"] += 1
        return "settled" if quiet_enough(now, st["t0"], st["t_change"], st["same"], r["floor"], r["quiet"],
                                         r["min_same"]) else None

    t = time.monotonic()
    try:
        how = env.act(action, read=probe, until=lambda v: v is not None, timeout_s=max(cap, 0.02))
    except TimeoutError:
        how = "fixed" if fixed_s is not None else "capped"
    sent = dict(env.last or {})
    obs = env.observe()                               # every action keeps its screenshot, as before
    took = time.monotonic() - t
    act_s, obs_s = sent.get("act_s", 0.0), (env.last or {}).get("obs_s", 0.0)
    env.last = {"act_s": act_s, "obs_s": obs_s, "settle_s": max(0.0, took - act_s - obs_s)}
    return obs, rule, how


def _ordinary(env: Any, action: Action, fixed_s: float | None):
    if fixed_s is None:
        return env.act(action)
    before, env.fixed_wait_s = env.fixed_wait_s, fixed_s
    try:
        return env.act(action)
    finally:
        env.fixed_wait_s = before


class WindowsReader:
    """What `need` reads, on Windows, through UI Automation (needs the comtypes package)."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        import comtypes.client
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as U
        self.U, self.w, self.ct = U, wintypes, ctypes
        self.auto = comtypes.client.CreateObject(U.CUIAutomation, interface=U.IUIAutomation)
        self.user = ctypes.windll.user32
        self.user.GetForegroundWindow.restype = wintypes.HWND
        self.user.WindowFromPoint.restype = wintypes.HWND
        self.user.WindowFromPoint.argtypes = [wintypes.POINT]
        self.user.GetAncestor.restype = wintypes.HWND

    def _text(self, hwnd, fn) -> str:
        buf = self.ct.create_unicode_buffer(1024)
        fn(self.w.HWND(hwnd), buf, len(buf))
        return buf.value

    def front(self) -> tuple[int, int, str, str]:
        """(window, its process, its title, its class) of the window in front."""
        hwnd = self.user.GetForegroundWindow() or 0
        pid = self.w.DWORD()
        if hwnd:
            self.user.GetWindowThreadProcessId(self.w.HWND(hwnd), self.ct.byref(pid))
        return (hwnd, pid.value, self._text(hwnd, self.user.GetWindowTextW) if hwnd else "",
                self._text(hwnd, self.user.GetClassNameW) if hwnd else "")

    def focus(self) -> Focus:
        U = self.U
        try:
            el = self.auto.GetFocusedElement()
            if el is None:
                return Focus()
            f = Focus(ident=tuple(el.GetRuntimeId() or ()), kind=int(el.CurrentControlType), pid=int(el.CurrentProcessId))
            r = el.CurrentBoundingRectangle
            f.rect = (r.left, r.top, r.right, r.bottom)
        except Exception:  # noqa: BLE001 - the focus is between controls
            return Focus()
        try:
            p = el.GetCurrentPattern(U.UIA_ValuePatternId)
            if p is not None:
                f.value = str(p.QueryInterface(U.IUIAutomationValuePattern).CurrentValue or "")
        except Exception:  # noqa: BLE001
            pass
        if f.value:
            try:
                p = el.GetCurrentPattern(U.UIA_TextPatternId)
                sel = p.QueryInterface(U.IUIAutomationTextPattern).GetSelection() if p is not None else None
                if sel is not None and sel.Length == 1:
                    f.all_selected = str(sel.GetElement(0).GetText(-1) or "") == f.value
            except Exception:  # noqa: BLE001 - no selection to read: not known to be all selected
                pass
        return f

    def before(self, action: Action) -> Before:
        hwnd, pid, title, _cls = self.front()
        b = Before(hwnd=hwnd, pid=pid, title=title, focus=self.focus())
        if action.type == "click" and action.x is not None and action.y is not None:
            pt = self.w.POINT(int(action.x), int(action.y))
            under = self.user.WindowFromPoint(pt)
            root = self.user.GetAncestor(self.w.HWND(under), 2) if under else None        # GA_ROOT
            b.target_in_taskbar = bool(root) and self._text(root, self.user.GetClassNameW) in TASKBAR
            try:
                el = self.auto.ElementFromPoint(self.U.tagPOINT(int(action.x), int(action.y)))
                b.target = str(el.CurrentName or "") if el is not None else ""
            except Exception:  # noqa: BLE001 - nothing readable under the pointer
                b.target = ""
        return b


def reader() -> Any:
    """The reader for this computer, or None where there is none yet (the actions then wait the ordinary way)."""
    if sys.platform == "win32":
        try:
            return WindowsReader()
        except Exception:  # noqa: BLE001 - comtypes is not installed, or UI Automation is not there
            return None
    return None
