"""Actions: the one format the model, the API and the sandbox adapters share.

The model calls a `computer` tool with coordinates on a 0-1000 scale (Qwen-VL's native convention, pinned per model:
a 3-3 calibration tie once broke a benchmark run). `parse` turns whatever the model sent into an `Action` in pixels of
the client's screen. It is tolerant of what open models really send: numeric strings, [x, y] lists, "(x, y)" text,
{x, y} objects, and action aliases.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

KINDS = ("click", "double_click", "triple_click", "right_click", "type", "key", "scroll", "drag", "wait", "done", "fail")
ALIASES = {"left_click": "click", "leftclick": "click", "tap": "click", "doubleclick": "double_click",
           "left_double_click": "double_click", "tripleclick": "triple_click", "left_triple_click": "triple_click",
           "triple-click": "triple_click", "rightclick": "right_click", "left_click_drag": "drag",
           "keypress": "key", "key_press": "key", "hotkey": "key", "press": "key", "keys": "key",
           "type_text": "type", "write": "type", "input": "type", "terminate": "done", "finished": "done",
           "answer": "done", "sleep": "wait"}
KEYMAP = {"enter": "Return", "return": "Return", "esc": "Escape", "escape": "Escape", "tab": "Tab",
          "backspace": "BackSpace", "delete": "Delete", "del": "Delete", "space": "space", "up": "Up", "down": "Down",
          "left": "Left", "right": "Right", "pageup": "Prior", "page_up": "Prior", "pagedown": "Next",
          "page_down": "Next", "home": "Home", "end": "End", "ctrl": "ctrl", "control": "ctrl", "alt": "alt",
          "shift": "shift", "cmd": "super", "super": "super", "win": "super", "meta": "super"}
_NUM = re.compile(r"-?\d+(?:\.\d+)?")


@dataclass
class Action:
    type: str
    x: int | None = None
    y: int | None = None
    x2: int | None = None
    y2: int | None = None
    text: str | None = None
    keys: str | None = None
    direction: str | None = None
    amount: int | None = None

    @property
    def done(self) -> bool:
        return self.type in ("done", "fail")

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, d: dict) -> "Action":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__})

    def describe(self) -> str:
        t = self.type
        if t in ("click", "double_click", "triple_click", "right_click"):
            return f"{t.replace('_', ' ')} ({self.x}, {self.y})"
        if t == "type":
            s = self.text or ""
            return f'type "{s[:40]}{"…" if len(s) > 40 else ""}"'
        if t == "key":
            return f"key {self.keys}"
        if t == "scroll":
            return f"scroll {self.direction} {self.amount or 3}"
        if t == "drag":
            return f"drag ({self.x}, {self.y}) → ({self.x2}, {self.y2})"
        return t


def _pair(v):
    if isinstance(v, dict) and "x" in v and "y" in v:
        v = [v["x"], v["y"]]
    if isinstance(v, (list, tuple)):
        v = " ".join(str(n) for n in v)
    nums = _NUM.findall(str(v))
    return (float(nums[0]), float(nums[1])) if len(nums) >= 2 else None


def _xy(a: dict, end: bool = False):
    kx, ky = ("x2", "y2") if end else ("x", "y")
    if a.get(kx) is not None and a.get(ky) is not None and (p := _pair([a[kx], a[ky]])):
        return p
    names = ("end_coordinate", "end", "to", "target", "x2") if end else \
        ("coordinate", "coordinates", "point", "position", "coords", "start_coordinate", "x")
    for k in names:
        if a.get(k) is not None and (p := _pair(a[k])):
            return p
    return None


def norm_keys(k) -> str:
    if isinstance(k, (list, tuple)):
        k = "+".join(str(x) for x in k)
    parts = [p.strip() for p in re.split(r"\s*\+\s*", str(k)) if p.strip()]
    return "+".join(KEYMAP.get(p.lower(), p if len(p) > 1 else p) for p in parts)


def arguments(msg: dict) -> dict | None:
    """The tool-call arguments from an OpenAI-style assistant message (or a JSON object in its text)."""
    for tc in msg.get("tool_calls") or []:
        a = (tc.get("function") or {}).get("arguments")
        if isinstance(a, str):
            try:
                a = json.loads(a)
            except json.JSONDecodeError:
                continue
        if isinstance(a, dict):
            return a
    content = msg.get("content") or ""
    if isinstance(content, list):
        content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
    for m in re.finditer(r"\{.*?\}", content, flags=re.S):
        try:
            a = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(a, dict) and "action" in a:
            return a
    return None


def parse(args: dict | str | None, width: int, height: int, scale: float = 1000.0) -> tuple[Action | None, str]:
    """(Action in pixels, "") or (None, reason). Coordinates arrive on a 0-`scale` grid."""
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            return None, "arguments are not JSON"
    if not isinstance(args, dict):
        return None, "no tool call"
    kind = str(args.get("action") or args.get("type") or "").strip().lower()
    kind = ALIASES.get(kind, kind)
    if kind not in KINDS:
        return None, f"unknown action {kind!r}"

    def px(p):
        x, y = p
        return (max(0, min(width - 1, round(x * width / scale))), max(0, min(height - 1, round(y * height / scale))))

    a = Action(kind)
    if kind in ("click", "double_click", "triple_click", "right_click", "drag"):
        p = _xy(args)
        if p is None:
            return None, f"{kind} without coordinates"
        a.x, a.y = px(p)
        if kind == "drag":
            q = _xy(args, end=True)
            if q is None:
                return None, "drag without an end point"
            a.x2, a.y2 = px(q)
    elif kind == "type":
        t = args.get("text")
        if t is None:
            return None, "type without text"
        a.text = str(t)
    elif kind == "key":
        k = args.get("keys") or args.get("key") or args.get("text")
        if not k:
            return None, "key without keys"
        a.keys = norm_keys(k)
    elif kind == "scroll":
        a.direction = str(args.get("direction") or "down").lower()
        try:
            a.amount = int(args.get("amount") or 3)
        except (TypeError, ValueError):
            a.amount = 3
        if (p := _xy(args)) is not None:
            a.x, a.y = px(p)
    return a, ""
