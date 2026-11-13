"""A fake desktop and a minimal harness: run the whole collect -> export loop with no VMs (tests, trainer wiring).

FakeDesktop settings (DesktopConfig.fake): reset_s, act_s, fail_reset_every (raise on every Nth reset, to exercise
infrastructure retries). A task's reward comes from task["reward"] (a number, or a list indexed by the attempt number).
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


class FakeDesktop:
    def __init__(self, cfg):
        self.cfg, self.f = cfg, dict(cfg.fake or {})
        self.n_reset = self.n_act = 0
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
        return {"screenshot": tiny_png(self.n_act)}

    def act(self, action):
        self.n_act += 1
        time.sleep(self.f.get("act_s", 0.0))
        done = str(action) == "DONE"
        return self.observe(), done, {"act_s": self.f.get("act_s", 0.0), "obs_s": 0.0, "wait": {"wait_s": 0.0, "ready_s": 0.0}}

    def wait_before_eval(self) -> dict:
        return {"wait_s": 0.0}

    def evaluate(self) -> float:
        r = self.task.get("reward", 0.0)
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
