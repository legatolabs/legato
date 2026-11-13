"""Settle: wait exactly as long as the screen needs, never longer than the sleep it replaces.

Two places it can run:
- inside the sandbox (legato.env's E2B adapter). The action and the wait happen in one remote call, polling the
  local framebuffer every few ms. This is the fast path.
- client-side, from any screenshot function (`Waiter`). Each grab is a network round trip, so the rule is coarser.

The stop test is `quiet_enough`, checked as written with Z3 symbolic execution over IEEE doubles (the proof
harness is not published yet). It stops exactly when the floor, the quiet time and the repeat count are all met. The loop returns
within cap + one poll + one grab (k-induction). Quiet time counts from the first grab that observed the frame.
"""
from __future__ import annotations

import base64
import hashlib
import time
from typing import Any, Callable

# per action kind, for a sandbox that grabs its own framebuffer (ms per grab)
LOCAL_RULES = {
    "default": {"floor": 0.12, "quiet": 0.35, "min_same": 3, "cap": 2.0, "poll": 0.03},
    "click": {"floor": 0.15, "quiet": 0.40, "min_same": 3, "cap": 2.0, "poll": 0.03},
    "type": {"floor": 0.05, "quiet": 0.30, "min_same": 3, "cap": 2.0, "poll": 0.03},
    "key": {"floor": 0.12, "quiet": 0.40, "min_same": 3, "cap": 2.0, "poll": 0.03},
    "scroll": {"floor": 0.10, "quiet": 0.35, "min_same": 3, "cap": 2.0, "poll": 0.03},
    "wait": {"floor": 1.00, "quiet": 0.50, "min_same": 3, "cap": 3.0, "poll": 0.05},
}
# for a remote screenshot function (each grab is a round trip of ~0.1-0.3 s)
REMOTE_RULES = {
    "default": {"floor": 0.25, "quiet": 0.5, "min_same": 2},
    "type": {"floor": 0.15, "quiet": 0.4, "min_same": 2},
    "key": {"floor": 0.25, "quiet": 0.6, "min_same": 2},
    "reset": {"floor": 1.0, "quiet": 3.0, "min_same": 3},
}
KIND_OF = {"double_click": "click", "triple_click": "click", "right_click": "click", "drag": "click", "done": "default", "fail": "default"}


def rule_kind(action_type: str) -> str:
    return KIND_OF.get(action_type, action_type)


def quiet_enough(now, t0, t_change, same, floor, quiet, min_same) -> bool:
    if now - t0 >= floor and now - t_change >= quiet and same >= min_same:
        return True
    return False


def digest(shot: Any) -> str:
    if shot is None:
        return "none"
    if isinstance(shot, dict):
        shot = shot.get("screenshot", shot)
    if isinstance(shot, str):
        try:
            shot = base64.b64decode(shot, validate=False)
        except Exception:  # noqa: BLE001
            shot = shot.encode()
    if hasattr(shot, "tobytes"):
        shot = shot.tobytes()
    return hashlib.blake2b(bytes(shot), digest_size=8).hexdigest()


class Waiter:
    """Client-side settle over any screenshot function. `settle(kind)` returns the last screenshot."""

    def __init__(self, screenshot: Callable[[], Any], rules: dict | None = None, replaced_sleep_s: float = 2.0,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.screenshot, self.rules = screenshot, {**REMOTE_RULES, **(rules or {})}
        self.replaced, self.clock, self.sleep = replaced_sleep_s, clock, sleep
        self.n, self.waited, self.baseline, self.grabs, self.capped = 0, 0.0, 0.0, 0, 0

    def settle(self, kind: str = "default", cap_s: float | None = None, poll_s: float = 0.05):
        r = self.rules.get(kind) or self.rules["default"]
        cap = self.replaced if cap_s is None else cap_s
        t0 = self.clock()
        shot = self.screenshot()
        last = digest(shot)
        self.grabs += 1
        t_change, same = self.clock(), 1
        while self.clock() - t0 < cap:
            self.sleep(poll_s)
            shot = self.screenshot()
            d = digest(shot)
            self.grabs += 1
            now = self.clock()
            if d != last:
                t_change, same, last = now, 1, d
            else:
                same += 1
            if quiet_enough(now, t0, t_change, same, r["floor"], r["quiet"], r["min_same"]):
                break
        else:
            self.capped += 1
        w = self.clock() - t0
        self.n, self.waited, self.baseline = self.n + 1, self.waited + w, self.baseline + cap
        return shot

    def report(self) -> dict:
        return {"waits": self.n, "fixed_sleep_s": round(self.baseline, 1), "legato_wait_s": round(self.waited, 1),
                "saved_s": round(self.baseline - self.waited, 1),
                "saved_pct": round(100 * (1 - self.waited / self.baseline), 1) if self.baseline else 0.0,
                "hit_cap": self.capped, "screenshots": self.grabs}


def wrap(screenshot: Callable[[], Any], replaced_sleep_s: float = 2.0, rules: dict | None = None) -> Waiter:
    return Waiter(screenshot, rules, replaced_sleep_s)
