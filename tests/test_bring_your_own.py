"""Bring your own sandbox: attach(shell=run) on a real X display, and the --sandbox loader."""
import os
import shutil
import subprocess
import time

import pytest

import legato
from legato import providers
from legato.actions import Action
from legato.env import Env, png_size


@pytest.mark.skipif(not all(shutil.which(b) for b in ("Xvfb", "xdotool", "scrot")), reason="needs Xvfb, xdotool, scrot")
def test_shell_sandbox_on_a_real_display(tmp_path):
    disp = ":96"
    x = subprocess.Popen(["Xvfb", disp, "-screen", "0", "800x600x24"], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    try:
        time.sleep(1.0)
        calls = []

        def run(cmd):      # stands in for docker exec, ssh, or a sandbox SDK's run-command call
            calls.append(cmd)
            return subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=30,
                                  env={**os.environ, "DISPLAY": disp, "HOME": str(tmp_path)}).stdout

        env = legato.attach(shell=run, blink_off=False)
        assert png_size(env.observe().screenshot) == (800, 600)
        n = len(calls)
        obs = env.act(Action("click", x=100, y=100))
        assert len(calls) == n + 1                      # one call per action: act, wait, screenshot
        assert png_size(obs.screenshot) == (800, 600) and env.last["helper"]
    finally:
        x.terminate()


def _docker_image():
    import os
    img = os.environ.get("LEGATO_TEST_DOCKER_IMAGE", "")
    if not img or not shutil.which("docker"):
        return None
    ok = subprocess.run(["docker", "image", "inspect", img], capture_output=True).returncode == 0
    return img if ok else None


@pytest.mark.skipif(_docker_image() is None, reason="set LEGATO_TEST_DOCKER_IMAGE to a built desktop image")
def test_docker_sandbox():
    stop, env = providers.load("docker:" + _docker_image())(800, 600)
    try:
        assert png_size(env.observe().screenshot) == (800, 600)
        assert png_size(env.act(Action("click", x=10, y=10)).screenshot) == (800, 600)
    finally:
        providers.close(stop)


def test_sandbox_loader(tmp_path):
    f = tmp_path / "my_desktop.py"
    f.write_text(
        "import legato, pathlib\n"
        "class Box:\n"
        f"    def kill(self): pathlib.Path({str(tmp_path / 'killed')!r}).write_text('yes')\n"
        "def desktop(width, height):\n"
        "    return Box(), legato.attach(screenshot=lambda: b'', execute=lambda a: None)\n"
        "def bare(width, height):\n"
        "    return legato.attach(screenshot=lambda: b'', execute=lambda a: None)\n")
    handle, env = providers.load(f"{f}:desktop")(640, 480)
    assert isinstance(env, Env)
    providers.close(handle)
    assert (tmp_path / "killed").read_text() == "yes"
    handle, env = providers.load(f"{f}:bare")(640, 480)
    assert handle is None and isinstance(env, Env)
    called = []
    providers.close(lambda: called.append(True))
    assert called == [True]
    assert providers.load("e2b") is providers.e2b and providers.load("local") is providers.local
    assert providers.load("docker") is providers.docker
    with pytest.raises(ValueError):
        providers.load("not-a-spec")


def test_doctor_json(capsys, monkeypatch):
    import json
    from legato import doctor
    from legato.doctor import main
    monkeypatch.setattr(doctor, "window_capture", lambda app, perms: {"ok": None, "state": "not checked", "fix": None})
    assert main(["--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert {"docker", "local", "e2b"} <= set(d["sandboxes"]) and "mcp" in d and d["legato"] == legato.__version__


def test_verified_action_returns_matching_observation_and_dispatches_once():
    from PIL import Image
    import io

    frames = []
    for width in (8, 16):
        buf = io.BytesIO()
        Image.new("RGB", (width, 2)).save(buf, format="PNG")
        frames.append(buf.getvalue())
    calls, seen = [], []
    desk = legato.attach(screenshot=iter(frames).__next__, execute=calls.append)
    click = Action("click", x=2, y=1)
    def ready(obs):
        seen.append(obs.width)
        return obs.width == 16
    observed = desk.act(click, until=ready)
    assert calls == [click]
    assert seen == [8, 16]
    assert observed.width == 16 and observed.screenshot == frames[-1]
    assert desk.last["act_s"] >= 0 and desk.last["obs_s"] >= 0 and desk.last["settle_s"] >= 0


def _test_png():
    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new("RGB", (2, 1)).save(buf, "PNG")
    return buf.getvalue()


def test_missing_condition_preserves_action_count_and_next_action_uses_its_own_wait():
    calls = []
    desk = legato.attach(screenshot=_test_png, execute=calls.append, fixed_wait_s=0)
    click = Action("click", x=2, y=1)
    with pytest.raises(TimeoutError):
        desk.act(click, until=lambda obs: False, timeout_s=.025)
    assert calls == [click]
    for invalid in (0, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            desk.act(click, until=lambda obs: True, timeout_s=invalid)
    assert calls == [click]
    assert desk.act(click).screenshot == _test_png()
    assert calls == [click, click]


def test_verified_step_uses_remote_helpers_frame_once():
    calls = []
    def step(action):
        calls.append(action)
        desk._pending_png = _test_png()
        return {"helper": True, "settle_s": .3}
    desk = Env(lambda: pytest.fail("extra capture"), step=step)
    click = Action("click", x=2, y=1)
    assert desk.act(click, until=lambda image: image.size == (2, 1)).screenshot == _test_png()
    assert calls == [click] and desk.last["helper"] and desk.last["settle_s"] >= .3
