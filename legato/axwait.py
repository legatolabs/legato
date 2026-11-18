"""Waiting on the front app's Accessibility events instead of its pixels (macOS).

After an action, apps tell the system what changed: a window opened, focus moved, a field's value changed, a menu
opened. `AXWaiter.settle` ends once the front app has sent none of those for `quiet_s` (after the rule's floor), at
most the cap. A change of front app counts as an event. Notifications the app was already sending before the action
(a progress bar's value, a clock) are left out: `prime` reads what arrived since the last wait. With `pixels` (a
`Waiter(background=True)` over cell digests) the screen must also hold still outside the cells that move on their
own, so drawing that sends no event is still waited for. `prime` costs `observe_s` an action.

Needs pyobjc-framework-ApplicationServices and Quartz, and Accessibility access for this process.
"""
from __future__ import annotations

import time
from typing import Any, Callable

NOTES = ("AXFocusedUIElementChanged", "AXFocusedWindowChanged", "AXMainWindowChanged", "AXWindowCreated",
         "AXWindowMoved", "AXWindowResized", "AXWindowMiniaturized", "AXUIElementDestroyed", "AXCreated",
         "AXValueChanged", "AXTitleChanged", "AXMenuOpened", "AXMenuClosed", "AXMenuItemSelected",
         "AXSelectedChildrenChanged", "AXSelectedTextChanged", "AXSelectedRowsChanged", "AXRowCountChanged",
         "AXLayoutChanged", "AXResized", "AXMoved", "AXSheetCreated", "AXElementBusyChanged", "AXLoadComplete")
NOISY_ROLES = {"AXProgressIndicator", "AXBusyIndicator", "AXLevelIndicator", "AXValueIndicator"}


def front_pid(quartz: Any) -> int | None:
    """The owner of the topmost ordinary window: current in any process (NSWorkspace needs an AppKit run loop)."""
    info = quartz.CGWindowListCopyWindowInfo(quartz.kCGWindowListOptionOnScreenOnly, quartz.kCGNullWindowID) or []
    return next((w.get("kCGWindowOwnerPID") for w in info
                 if w.get("kCGWindowLayer") == 0 and w.get("kCGWindowAlpha", 1) > 0), None)


class AXWaiter:
    """The Waiter interface (`prime`, `settle`, `report`, `last`) over Accessibility events."""

    def __init__(self, rules: dict, pixels: Any = None, quiet_s: float = 0.25, replaced_sleep_s: float = 2.0,
                 clock: Callable[[], float] = time.monotonic, observe_s: float = 0.15, pixels_always: bool = True):
        import ApplicationServices as AS
        import CoreFoundation as CF
        import Quartz
        self.AS, self.CF, self.Q = AS, CF, Quartz
        self.rules, self.pixels, self.quiet_s, self.replaced, self.clock = rules, pixels, quiet_s, replaced_sleep_s, clock
        self.observe_s, self.prime_s = observe_s, 0.0
        # pixels_always=False ("ax|cells"): the pixels decide only when the app sent no event or the front app
        # changed (EXP-0047: Accessibility alone ended too early exactly when the app sent nothing)
        self.pixels_always = pixels_always
        self.background = True                    # Env.act records `last`
        self.n, self.waited, self.baseline, self.grabs, self.capped = 0, 0.0, 0.0, 0, 0
        self.last: dict = {}
        self.events: list = []                    # (time, notification, role)
        self.noise: dict = {}                     # (notification, role) -> times seen before an action
        self.pid: int | None = None
        self.observer = None
        import objc

        @objc.callbackFor(AS.AXObserverCreate)    # a C callback: PyObjC needs its signature
        def callback(observer, element, note, refcon):
            self._event(observer, element, note, refcon)
        self._cb = callback                       # kept: the observer calls back into it
        self.ax_ok: bool | None = None            # whether the last app watched accepted the notifications

    # ------------------------------------------------------------ the observer
    def _event(self, observer, element, note, refcon) -> None:
        role = ""
        try:
            err, value = self.AS.AXUIElementCopyAttributeValue(element, "AXRole", None)
            role = str(value) if not err and value else ""
        except Exception:  # noqa: BLE001
            pass
        self.events.append((self.clock(), str(note), role))

    def _watch(self, pid: int | None) -> None:
        if pid == self.pid:
            return
        if self.observer is not None:
            self.CF.CFRunLoopRemoveSource(self.CF.CFRunLoopGetCurrent(),
                                          self.AS.AXObserverGetRunLoopSource(self.observer), self.CF.kCFRunLoopDefaultMode)
            self.observer = None
        self.pid = pid
        if pid is None:
            return
        err, obs = self.AS.AXObserverCreate(pid, self._cb, None)
        if err or obs is None:
            return
        app = self.AS.AXUIElementCreateApplication(pid)
        try:                                      # Chromium and Electron apps build their tree only when asked
            self.AS.AXUIElementSetAttributeValue(app, "AXManualAccessibility", True)
        except Exception:  # noqa: BLE001
            pass
        added = [self.AS.AXObserverAddNotification(obs, app, note, None) for note in NOTES]
        self.ax_ok = any(e == 0 for e in added)   # -25211: this process has no Accessibility access
        self.CF.CFRunLoopAddSource(self.CF.CFRunLoopGetCurrent(), self.AS.AXObserverGetRunLoopSource(obs),
                                   self.CF.kCFRunLoopDefaultMode)
        self.observer = obs

    def _pump(self, seconds: float) -> None:
        end = self.clock() + seconds
        while True:
            left = end - self.clock()
            self.CF.CFRunLoopRunInMode(self.CF.kCFRunLoopDefaultMode, max(0.0, left), True)
            if self.clock() >= end:
                return

    def _relevant(self, e) -> bool:
        _, note, role = e
        if note == "AXValueChanged" and role in NOISY_ROLES:
            return False
        return (note, role) not in self.noise

    # ------------------------------------------------------------ Waiter
    def prime(self, poll_s: float = 0.05) -> None:
        """Before an action: drop what is left over from the last one, then watch `observe_s` with nothing happening;
        what the front app sends then (a progress bar, a clock) is left out of the waits after."""
        t = self.clock()
        self._watch(front_pid(self.Q))
        self._pump(0.0)
        self.events = []
        self._pump(self.observe_s)
        for _, note, role in self.events:
            self.noise[(note, role)] = self.noise.get((note, role), 0) + 1
        self.events = []
        if self.pixels is not None:
            self.pixels.prime(poll_s)
        self.prime_s = self.clock() - t

    def close(self) -> None:
        self._watch(None)

    def settle(self, kind: str = "default", cap_s: float | None = None, poll_s: float = 0.03):
        r = self.rules.get(kind) or self.rules["default"]
        cap = self.replaced if cap_s is None else cap_s
        t0 = self.clock()
        pix = self.pixels
        last_cells = pix._cells(pix.screenshot()) if pix is not None else None
        skip = (pix.moving or frozenset()) if pix is not None else frozenset()
        counts: dict = {}
        last_at: dict = {}
        pairs = 0
        t_ax, t_pix, same, n_ev, capped, pid0 = t0, t0, 1, 0, True, self.pid
        while self.clock() - t0 < cap:
            self._pump(poll_s)
            now = self.clock()
            pid = front_pid(self.Q)
            if pid != self.pid:
                self._watch(pid)
                t_ax = now
            fresh = [e for e in self.events if self._relevant(e)]
            self.events = []
            if fresh:
                n_ev += len(fresh)
                t_ax = max(t_ax, max(e[0] for e in fresh))
            if pix is not None:
                d = pix._cells(pix.screenshot())
                self.grabs += 1
                if d is None or last_cells is None or len(d) != len(last_cells):
                    t_pix, same = now, 1
                else:
                    pairs += 1
                    diff = [i for i, (a, b) in enumerate(zip(last_cells, d)) if a != b]
                    for i in diff:
                        counts[i], last_at[i] = counts.get(i, 0) + 1, now
                    if any(i not in skip for i in diff):
                        t_pix, same = now, 1
                    else:
                        same += 1
                last_cells = d if d is not None else last_cells
            ax_quiet = now - t_ax >= self.quiet_s
            pix_quiet = pix is None or (now - t_pix >= r["quiet"] and same >= r["min_same"])
            if not self.pixels_always and n_ev > 0 and self.pid == pid0:
                pix_quiet = True                  # the app told us what changed, and nothing else came forward
            if now - t0 >= r["floor"] and ax_quiet and pix_quiet:
                capped = False
                break
        w = self.clock() - t0
        if pix is not None and last_cells is not None:
            pix._learn(counts, last_at, pairs, self.clock(), r["quiet"], len(last_cells))
        self.capped += capped
        self.n, self.waited, self.baseline = self.n + 1, self.waited + w, self.baseline + cap
        self.last = {"s": round(w, 2), "capped": capped, "ax_events": n_ev, "front_changed": self.pid != pid0,
                     "prime_s": round(self.prime_s, 2), "ax_ok": self.ax_ok}
        return None

    def report(self) -> dict:
        return {"waits": self.n, "legato_wait_s": round(self.waited, 1), "hit_cap": self.capped}
