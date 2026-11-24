"""A fake desktop and a minimal harness: run the whole collect -> export loop with no VMs (tests, trainer wiring).

The fake screen (640x400 by default) says it is a fake desktop and shows the task and the step, so a model can read
it. FakeDesktop settings (DesktopConfig.fake, `--fake-opts`): size ([w, h]), reward (for tasks without their own),
reset_s, act_s, fail_reset_every (raise on every Nth reset, to exercise infrastructure retries), fail_act_every
(raise on every Nth action: a desktop that fails mid-rollout). A task's reward comes from task["reward"], else the
reward setting, else 0: a number, or a list indexed by the attempt number (e.g. [1, 0] gives a group rewards 1, 0, 1,
0, so its advantages are not 0). An action "DONE" or "FAIL" ends the episode.
FakeHarness sends one small multimodal request per step and acts on the reply: a reply containing "DONE" ends the
episode.
"""
from __future__ import annotations

import base64
import struct
import time
import zlib

from legato.rl.policy import PolicyClient


def tiny_png(seed: int = 0, w: int = 8, h: int = 8) -> bytes:
    raw = b"".join(b"\x00" + bytes([(seed * 37 + x * 11 + y * 7) % 256 for x in range(w * 3)]) for y in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def fake_screen(instruction: str, step: int = 0, size: tuple = (640, 400)) -> bytes:
    """The fake desktop's screenshot (PNG): a window that says it is a fake desktop, with the task and the step."""
    import io
    import textwrap

    from PIL import Image, ImageDraw, ImageFont
    w, h = max(64, int(size[0])), max(48, int(size[1]))

    def font(px: int):
        try:
            return ImageFont.load_default(size=px)        # scalable (Pillow 10.1+ with FreeType)
        except (TypeError, OSError, ImportError):
            return ImageFont.load_default()
    px = max(10, h // 22)
    big, small = font(round(px * 1.3)), font(px)
    im = Image.new("RGB", (w, h), (236, 239, 244))
    d = ImageDraw.Draw(im)
    bar = round(px * 2.2)
    d.rectangle([0, 0, w, bar], fill=(52, 58, 72))
    d.text((px, bar // 2), "Legato fake desktop", fill=(255, 255, 255), font=small, anchor="lm")
    x, y = px * 2, bar + px * 2
    for text, f, color in (("This is a fake desktop for testing.", big, (20, 24, 32)),
                           ("Nothing on it responds to clicks or keys.", small, (70, 76, 90))):
        d.text((x, y), text, fill=color, font=f)
        y += round(px * 2.2)
    y += px
    d.text((x, y), "Task:", fill=(20, 24, 32), font=big)
    y += round(px * 2)
    for line in textwrap.wrap(instruction, width=max(16, int((w - 2 * x) / (px * 0.5))))[:6]:
        d.text((x, y), line, fill=(20, 24, 32), font=small)
        y += round(px * 1.6)
    d.text((w - px * 2, h - px * 2), f"Step {step}", fill=(70, 76, 90), font=small, anchor="rs")
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


class FakeDesktop:
    def __init__(self, cfg):
        self.cfg, self.f = cfg, dict(cfg.fake or {})
        self.n_reset = self.n_act = self.n_acts = 0      # n_acts: over the desktop's life; n_act: this task's
        self.task, self.instruction = {}, ""

    def reset(self, task: dict) -> dict:
        self.n_reset += 1
        every = self.f.get("fail_reset_every")
        if every and self.n_reset % every == 0:
            raise RuntimeError("fake desktop: reset failed")
        time.sleep(self.f.get("reset_s", 0.0))
        self.task, self.instruction, self.n_act = task, task.get("instruction", f"do task {task['task_id']}"), 0
        return {"total_s": self.f.get("reset_s", 0.0), "path": "fake"}

    def wait_after_reset(self) -> dict:
        return {"wait_s": 0.0, "ready_s": 0.0}

    def observe(self) -> dict:
        return {"screenshot": fake_screen(self.instruction, self.n_act, tuple(self.f.get("size") or (640, 400)))}

    def act(self, action):
        self.n_act += 1
        self.n_acts += 1
        every = self.f.get("fail_act_every")
        if every and self.n_acts % every == 0:
            raise RuntimeError("fake desktop: the action failed")
        time.sleep(self.f.get("act_s", 0.0))
        done = str(action) in ("DONE", "FAIL")
        return self.observe(), done, {"act_s": self.f.get("act_s", 0.0), "obs_s": 0.0, "wait": {"wait_s": 0.0, "ready_s": 0.0}}

    def wait_before_eval(self) -> dict:
        return {"wait_s": 0.0}

    def evaluate(self) -> float:
        r = self.task.get("reward", self.f.get("reward", 0.0))
        if isinstance(r, list):
            r = r[self.task.get("_attempt", self.n_reset - 1) % len(r)]
        return float(r)

    def close(self) -> None:
        pass


class FakeHarness:
    def __init__(self, policy: PolicyClient, cfg):
        self.policy, self.history = policy, []

    def reset(self) -> None:
        self.history = []

    def predict(self, instruction: str, obs: dict):
        img = "data:image/png;base64," + base64.b64encode(obs["screenshot"]).decode()
        self.history.append({"role": "user", "content": [{"type": "image_url", "image_url": {"url": img}},
                                                          {"type": "text", "text": instruction}]})
        payload = {"messages": [{"role": "system", "content": "Act on the desktop. Reply DONE when finished."},
                                *self.history], "max_tokens": 64, "temperature": 1.0, "top_p": 1.0}
        rec = self.policy.chat(payload["messages"], max_tokens=64, temperature=1.0, top_p=1.0)
        self.history.append({"role": "assistant", "content": rec["content"]})
        action = "DONE" if "DONE" in rec["content"] else "pyautogui.click(10, 10)"
        return rec["content"], [action], [(payload, rec)]
