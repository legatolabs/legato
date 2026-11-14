"""The loop stays fast. No model and no API key: a scripted model, and either a simulated screen or a real desktop.
Each figure is compared with tests/snapshots/loop_perf.json, and a PR fails if it gets worse:

- settle: how long the wait for the screen takes on a simulated screen with a fake clock (exact, every PR). It must
  never return while the screen is still changing, and never take longer than the 2 s sleep it replaces.
- loop: model calls, screenshots, images per request, prompt size and how much of each prompt repeats the previous
  one (what keeps the prompt cache hitting), for a scripted 24-action task (exact, every PR).
- desktop: the real wait per action on the Docker desktop (timed, so with a tolerance; runs when
  LEGATO_TEST_DOCKER_IMAGE names a built desktop image, as in CI).

After an intended change, regenerate the baseline and commit it with the change:

    LEGATO_UPDATE_SNAPSHOTS=1 LEGATO_TEST_DOCKER_IMAGE=legato-desktop:ci pytest -q tests/test_loop_perf.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import legato  # noqa: E402
from legato.actions import Action  # noqa: E402
from legato.rl.fake import tiny_png  # noqa: E402
from legato.settle import Waiter  # noqa: E402

BASELINE = ROOT / "tests" / "snapshots" / "loop_perf.json"
UPDATE = bool(os.environ.get("LEGATO_UPDATE_SNAPSHOTS"))
FIXED_SLEEP_S = 2.0                     # what the reference loop sleeps after every action


def _baseline() -> dict:
    return json.loads(BASELINE.read_text()) if BASELINE.exists() else {}


def _record(section: str, figures: dict) -> dict:
    """Write this section's figures when updating; return the committed baseline for it."""
    if UPDATE:
        data = _baseline()
        data[section] = figures
        BASELINE.parent.mkdir(exist_ok=True)
        BASELINE.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
    base = _baseline().get(section)
    assert base is not None, f"no baseline for {section}: run with LEGATO_UPDATE_SNAPSHOTS=1 once"
    _summary(section, figures, base)
    return base


def _summary(section: str, now: dict, base: dict) -> None:
    rows = [f"| {k} | {base.get(k)} | {v} |" for k, v in now.items()]
    text = f"\n### loop performance: {section}\n\n| figure | baseline | this run |\n|---|---|---|\n" + "\n".join(rows) + "\n"
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(text)


# ---------------------------------------------------------------- settle, on a simulated screen with a fake clock

class SimScreen:
    """A screen that keeps changing until `busy_s` after the action, then holds still. Each grab costs `grab_s` of
    simulated time, like a remote screenshot."""

    def __init__(self, busy_s: float, grab_s: float = 0.12):
        self.t, self.busy_s, self.grab_s, self.frame = 0.0, busy_s, grab_s, 0

    def clock(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s

    def shot(self) -> bytes:
        self.t += self.grab_s
        if self.t < self.busy_s:
            self.frame += 1
        return b"frame %d" % self.frame


SETTLE_CASES = [(kind, busy) for kind in ("default", "click", "type", "key") for busy in (0.0, 0.3, 1.0, 9.0)]


def test_settle_waits_no_longer_than_it_has_to():
    figures, bad = {}, []
    for kind, busy in SETTLE_CASES:
        s = SimScreen(busy)
        w = Waiter(s.shot, clock=s.clock, sleep=s.sleep)
        w.settle(kind)
        took = round(s.t, 3)
        figures[f"{kind} busy {busy}s"] = took
        if took > FIXED_SLEEP_S + 0.5:
            bad.append(f"{kind}, screen busy {busy} s: waited {took} s, longer than the {FIXED_SLEEP_S} s sleep it replaces")
        if busy < FIXED_SLEEP_S and took < busy:
            bad.append(f"{kind}, screen busy {busy} s: returned after {took} s, while the screen was still changing")
    base = _record("settle", figures)
    for k, v in figures.items():
        if k in base and v > base[k] + 0.01:
            bad.append(f"settle {k}: {v} s, slower than the baseline {base[k]} s")
    assert not bad, "\n".join(bad)


# ---------------------------------------------------------------- the loop, with a scripted model

def _tc(args: dict) -> dict:
    return {"type": "function", "function": {"name": "computer", "arguments": json.dumps(args)}}


TURNS = 8
SCRIPT = [[_tc({"action": "left_click", "coordinate": [100 + 10 * i, 200]}), _tc({"action": "type", "text": f"x{i}"}),
           _tc({"action": "key", "keys": "Return"})] for i in range(TURNS)]


@pytest.fixture()
def scripted_model():
    bodies, script = [], [list(t) for t in SCRIPT]

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            bodies.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            calls = script.pop(0) if script else [_tc({"action": "done"})]
            out = json.dumps({"choices": [{"message": {"content": "", "tool_calls": calls}}],
                              "usage": {"prompt_tokens": 10, "completion_tokens": 3}}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1", bodies
    srv.shutdown()


def _common_prefix(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def test_loop_shape(scripted_model):
    url, bodies = scripted_model
    grabs = []
    frames = iter(range(10**6))

    def shot():
        grabs.append(1)
        return tiny_png(next(frames) % 251 + 1, w=64, h=40)    # every screenshot differs, like a real screen

    env = legato.attach(screenshot=shot, execute=lambda a: None, fixed_wait_s=0.0)
    res = legato.Harness("scripted", url).run("scripted task", env, max_steps=TURNS + 2)
    assert res.done and len(res.actions) == 3 * TURNS, (
        f"the scripted model sent {TURNS} turns of 3 actions, but {len(res.actions)} ran: is batching off?")

    msgs = [json.dumps(b["messages"]) for b in bodies]
    reuse = [_common_prefix(a, b) / len(a) for a, b in zip(msgs, msgs[1:])]
    images = [sum(1 for m in b["messages"] if isinstance(m.get("content"), list)
                  for p in m["content"] if p.get("type") == "image_url") for b in bodies]
    figures = {
        "model calls": len(bodies),
        "screenshots": len(grabs),
        "max images per request": max(images),
        "mean prompt KB": round(sum(map(len, msgs)) / len(msgs) / 1024, 1),
        "mean prefix reuse": round(sum(reuse) / len(reuse), 3),
        "min prefix reuse": round(min(reuse), 3),
    }
    base = _record("loop", figures)
    bad = [f"{k}: {figures[k]}, more than the baseline {base[k]}"
           for k in ("model calls", "screenshots", "max images per request") if figures[k] > base[k]]
    if figures["mean prompt KB"] > base["mean prompt KB"] * 1.10:
        bad.append(f"mean prompt KB: {figures['mean prompt KB']}, over 10% above the baseline {base['mean prompt KB']}")
    for k in ("mean prefix reuse", "min prefix reuse"):
        if figures[k] < base[k] - 0.02:
            bad.append(f"{k}: {figures[k]}, below the baseline {base[k]}: each prompt repeats less of the last one, "
                       "so the prompt cache will hit less")
    assert not bad, "\n".join(bad)


# ---------------------------------------------------------------- the real Docker desktop

def _docker_image():
    img = os.environ.get("LEGATO_TEST_DOCKER_IMAGE", "")
    if not img or not shutil.which("docker"):
        return None
    return img if subprocess.run(["docker", "image", "inspect", img], capture_output=True).returncode == 0 else None


DESKTOP_SCRIPT = [Action("click", x=400, y=300), Action("type", text="echo legato"), Action("key", keys="Return"),
                  Action("type", text="ls /"), Action("key", keys="Return"), Action("key", keys="ctrl+l"),
                  Action("click", x=200, y=200), Action("scroll", x=400, y=300, direction="down", amount=3)]
DESKTOP_TOLERANCE = 1.5                 # timed on shared CI machines: fail only on a clear regression


@pytest.mark.skipif(_docker_image() is None, reason="set LEGATO_TEST_DOCKER_IMAGE to a built desktop image")
def test_desktop_wait_per_action():
    from legato import providers
    stop, env = providers.load("docker:" + _docker_image())(1024, 768)
    try:
        env.observe()
        waits = []
        for _ in range(2):                                     # twice, and keep the faster pass: less CI noise
            pass_waits = []
            for a in DESKTOP_SCRIPT:
                env.act(a)
                pass_waits.append(env.last.get("settle_s", 0.0))
            waits.append(pass_waits)
    finally:
        providers.close(stop)
    best = min(waits, key=sum)
    figures = {"mean wait per action s": round(sum(best) / len(best), 3), "max wait s": round(max(best), 3)}
    base = _record("desktop", figures)
    bad = []
    if figures["mean wait per action s"] > max(base["mean wait per action s"] * DESKTOP_TOLERANCE, 0.3):
        bad.append(f"mean wait per action {figures['mean wait per action s']} s, over {DESKTOP_TOLERANCE}x the baseline "
                   f"{base['mean wait per action s']} s")
    if figures["max wait s"] > FIXED_SLEEP_S + 0.5:
        bad.append(f"an action waited {figures['max wait s']} s, longer than the {FIXED_SLEEP_S} s sleep it replaces")
    assert not bad, "\n".join(bad)
