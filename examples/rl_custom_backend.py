"""Your own RL environment for `legato rl collect`: a class with reset, observe, act and evaluate.

This one draws a screen with one button, moves the button every reset, and rewards the attempts that clicked it. It
needs no VM and no desktop: the "screen" is an image the class draws.

    echo '[{"task_id": "button", "instruction": "Click the green button, then finish."}]' > tasks.json
    export LEGATO_POLICY_KEY=...        # PowerShell: $env:LEGATO_POLICY_KEY = "..."
    legato rl collect --tasks tasks.json --backend examples/rl_custom_backend.py:ButtonDesktop \
        --policy-url https://openrouter.ai/api/v1 --model qwen/qwen3.8-27b --version v0 --out runs/button \
        --group 2 --desktops 2 --max-steps 4 --temperature 1.0

Each worker process imports this file and builds its own ButtonDesktop(**options), with options from
`--backend-opts '{"size": [800, 500]}'` (or `--backend-opts @opts.json`).
"""
import io
import random

from PIL import Image, ImageDraw

from legato.rl import Desktop


class ButtonDesktop(Desktop):
    reward_source = "ButtonDesktop: 1 if a click landed on the button"

    def __init__(self, size: tuple = (640, 400)):
        self.w, self.h = size
        self.box, self.hit = (0, 0, 0, 0), False

    def reset(self, task: dict) -> None:
        self.instruction = task.get("instruction", "Click the green button, then finish.")
        x, y = random.randint(20, self.w - 180), random.randint(20, self.h - 80)
        self.box, self.hit = (x, y, x + 160, y + 60), False

    def observe(self) -> dict:
        im = Image.new("RGB", (self.w, self.h), "white")
        d = ImageDraw.Draw(im)
        d.rectangle(self.box, fill=(150, 150, 150) if self.hit else (30, 160, 80))
        d.text((self.box[0] + 20, self.box[1] + 24), "Clicked" if self.hit else "Click me", fill="white")
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return {"screenshot": buf.getvalue()}

    def act(self, action) -> tuple:
        """`action` is pyautogui code as a string ("pyautogui.click(120, 80)", or "DONE" / "FAIL" to end); with
        `--harness legato` (the default here) its `.action` is the parsed legato.Action."""
        a = getattr(action, "action", None)
        if a is not None and a.type == "click" and a.x is not None:
            x0, y0, x1, y1 = self.box
            self.hit = self.hit or (x0 <= a.x <= x1 and y0 <= a.y <= y1)
        return self.observe(), str(action) in ("DONE", "FAIL"), None

    def evaluate(self) -> float:
        return 1.0 if self.hit else 0.0
