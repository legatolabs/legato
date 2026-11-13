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


def test_doctor_json(capsys):
    import json
    from legato.doctor import main
    assert main(["--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert {"docker", "local", "e2b"} <= set(d["sandboxes"]) and "mcp" in d and d["legato"] == legato.__version__
