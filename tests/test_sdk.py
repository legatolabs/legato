"""SDK pieces without a model: action parsing, the E2B adapter (one remote call per action), the in-sandbox helper on a
real X display, and typing with control keys."""
import base64
import json
import os
import shutil
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import legato  # noqa: E402
from legato import actions as A  # noqa: E402
from legato import env as E  # noqa: E402


def png(w=1024, h=768, color=(255, 255, 255)) -> bytes:
    raw = b"".join(b"\x00" + bytes(color) * w for _ in range(h))
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + \
        chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


class FakeCommands:
    def __init__(self, log):
        self.log = log

    def run(self, cmd, timeout=None, background=False):
        self.log.append(cmd)
        out = json.dumps({"act_s": 0.01, "settle_s": 0.2, "capped": False, "grabs": 7,
                          "png": base64.b64encode(png(800, 600)).decode()}) if "legato_step" in cmd else ""
        return type("R", (), {"stdout": out, "stderr": "", "exit_code": 0})()


class FakeSandbox:
    def __init__(self):
        self.log, self.files_written = [], {}
        self.commands = FakeCommands(self.log)
        self.files = type("F", (), {"write": lambda _s, p, d: self.files_written.__setitem__(p, d)})()
        self.killed = False

    def screenshot(self):
        return png()

    def left_click(self):
        self.log.append("left_click")

    double_click = right_click = left_click

    def write(self, text):
        self.log.append(f"write {text}")

    def scroll(self, d, n):
        self.log.append(f"scroll {d}")

    def kill(self):
        self.killed = True


def test_parse_tolerates_model_shapes():
    for args in ({"action": "left_click", "coordinate": [500, 500]}, {"action": "click", "x": "500", "y": "500"},
                 {"action": "click", "point": "(500, 500)"}):
        a, _ = A.parse(args, 1024, 768)
        assert (a.type, a.x, a.y) == ("click", 512, 384)
    assert A.parse({"action": "hotkey", "keys": ["ctrl", "s"]}, 1024, 768)[0].keys == "ctrl+s"
    assert A.parse({"action": "key", "keys": "enter"}, 1024, 768)[0].keys == "Return"
    assert A.parse({"action": "fly"}, 1024, 768)[0] is None
    assert A.parse({"action": "click"}, 1024, 768)[0] is None


def test_e2b_adapter_uses_one_call_per_action():
    sb = FakeSandbox()
    env = legato.attach(sb)
    assert env.helper and E.HELPER_PATH in sb.files_written
    n = len(sb.log)
    obs = env.act(legato.Action("click", x=10, y=20))
    calls = sb.log[n:]
    assert len(calls) == 1 and "xdotool" in calls[0] and "mousemove" in calls[0] and "--sync" not in calls[0]
    assert env.last["settle_s"] == 0.2 and obs.width == 800      # the frame came back in the same call


@pytest.mark.skipif(not (shutil.which("Xvfb") and shutil.which("xdotool")), reason="needs Xvfb and xdotool")
def test_helper_script_on_a_real_display(tmp_path):
    disp = ":97"
    x = subprocess.Popen(["Xvfb", disp, "-screen", "0", "640x480x24"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(1.0)
        p = tmp_path / "h.py"
        p.write_text(E.HELPER)
        spec = {"cmds": [["xdotool", "mousemove", "10", "10"]], "rule": legato.settle.LOCAL_RULES["click"]}
        if not shutil.which("xwd") and not shutil.which("scrot"):
            pytest.skip("no xwd or scrot to grab the framebuffer")
        out = subprocess.run([sys.executable, str(p), json.dumps(spec)], capture_output=True, text=True,
                             env={**os.environ, "DISPLAY": disp}, timeout=30)
        r = json.loads(out.stdout.strip().splitlines()[-1])
        assert not r["capped"] and 0.4 <= r["settle_s"] < 1.5 and r["grabs"] >= 3
        spec["shot"] = True
        out = subprocess.run([sys.executable, str(p), json.dumps(spec)], capture_output=True, text=True,
                             env={**os.environ, "DISPLAY": disp}, timeout=30)
        r = json.loads(out.stdout.strip().splitlines()[-1])
        assert base64.b64decode(r["png"])[:4] == b"\x89PNG"
    finally:
        x.kill()


def test_typing_splits_control_keys():
    cmds = E.type_cmds("Total\t\t=SUM(C2:C8)\t=SUM(D2:D8)\n")
    keys = [c for c in cmds if c[:2] == ["xdotool", "key"]]
    assert [k[2] for k in keys] == ["Tab", "Tab", "Tab", "Return"]
    assert all(cmds[cmds.index(k) + 1][0] == "sleep" for k in keys)
    typed = "".join(c[-1] for c in cmds if c[:2] == ["xdotool", "type"])
    assert typed == "Total=SUM(C2:C8)=SUM(D2:D8)"
    assert E.type_cmds("plain text") == [["xdotool", "type", "--delay", str(E.TYPE_DELAY_MS), "--", "plain text"]]


def test_fixed_wait_replaces_the_ready_wait():
    done = []
    env = legato.attach(screenshot=lambda: png(64, 48), execute=lambda a: done.append(a.type), fixed_wait_s=0.2)
    t = time.monotonic()
    obs = env.act(legato.Action("click", x=1, y=1))
    assert done == ["click"] and env.last["settle_s"] == 0.2 and time.monotonic() - t >= 0.2 and obs.width == 64
