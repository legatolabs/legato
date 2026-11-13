"""The system prompt and the `computer` tool schema served with the model. They are the same ones the benchmarks used
(screenshot mode, 0-1000 coordinates), so the measured behaviour is what ships."""
from __future__ import annotations

from legato.actions import KINDS

SYSTEM = """You operate a Linux desktop ({w}x{h} pixels) to complete a task for the user. The app for the task is \
already open. Each turn you get the task, the log of your previous actions, and the current screen as a screenshot. \
Reply with exactly ONE call to the `computer` tool (one action). Actions:
- click / double_click / right_click on a point given as x, y on a 0-1000 scale of the screenshot's width and height \
(origin top-left)
- type: type text into the focused field (click the field first if needed)
- key: press keys, xdotool names, e.g. "ctrl+s", "Return", "Tab", "ctrl+a", "Delete", "Escape", "Down"
- scroll: x, y plus direction
- wait: wait for the screen to update
- done: the task is complete (files must be saved where the task says)
- fail: the task cannot be done
Work quickly: prefer keyboard shortcuts where they are reliable. Save files in their current format."""


def system_prompt(width: int, height: int) -> str:
    return SYSTEM.format(w=width, h=height)


def tool_schema() -> dict:
    props = {
        "action": {"type": "string", "enum": list(KINDS)},
        "text": {"type": "string", "description": "text for type"},
        "keys": {"type": "string", "description": "key combo(s) for key, e.g. ctrl+s"},
        "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
        "amount": {"type": "integer", "description": "scroll clicks"},
        "seconds": {"type": "number", "description": "for wait"},
        "x": {"type": "integer"},
        "y": {"type": "integer"},
        "x2": {"type": "integer", "description": "drag end x"},
        "y2": {"type": "integer", "description": "drag end y"},
    }
    return {"type": "function", "function": {
        "name": "computer", "description": "Perform one action on the desktop.",
        "parameters": {"type": "object", "properties": props, "required": ["action"]}}}
