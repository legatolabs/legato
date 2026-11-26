"""The native helper (native/legato-step): picked by CPU, installed once per sandbox, the same answers as the Python
helper on a real X display, and the Python helper whenever it can't run. Build it with native/build.sh (or
`cargo build --release` in native/legato-step); the tests that run it skip without a build."""
import base64
import io
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import legato
from legato import env as E
from legato.actions import Action
from legato.settle import LOCAL_RULES

ROOT = Path(__file__).resolve().parents[1]
ARCH = platform.machine()


def _built() -> Path | None:
    crate = ROOT / "native/legato-step/target"
    for p in (E.NATIVE_DIR / f"legato-step-{ARCH}", crate / f"{ARCH}-unknown-linux-musl/release/legato-step",
              crate / "release/legato-step"):
        if p.exists():
            return p
    return None


BIN = _built()
HAVE_X = all(shutil.which(b) for b in ("Xvfb", "xdotool"))
needs_bin = pytest.mark.skipif(BIN is None, reason="native helper not built (native/build.sh)")
needs_x = pytest.mark.skipif(not HAVE_X, reason="needs Xvfb and xdotool")


def elf(machine: int) -> bytes:
    return b"\x7fELF" + bytes(14) + machine.to_bytes(2, "little") + bytes(40)


def test_picks_the_binary_for_the_sandbox_cpu(tmp_path, monkeypatch):
    x86, arm = tmp_path / "x86", tmp_path / "arm"
    x86.write_bytes(elf(0x3E))
    arm.write_bytes(elf(0xB7))
    monkeypatch.setattr(E, "NATIVE_DIR", tmp_path / "none")
    monkeypatch.setenv("LEGATO_HELPER_BIN", str(x86))
    assert E.native_helper("x86_64") == elf(0x3E) and E.native_helper("amd64\n") == elf(0x3E)
    assert E.native_helper("aarch64") is None                 # wrong CPU: the Python helper
    monkeypatch.setenv("LEGATO_HELPER_BIN", str(arm))
    assert E.native_helper("arm64") == elf(0xB7)
    monkeypatch.setenv("LEGATO_HELPER", "python")
    assert E.native_helper("arm64") is None and not E.native_available()


def test_no_binary_keeps_the_python_helper(monkeypatch, tmp_path):
    monkeypatch.delenv("LEGATO_HELPER_BIN", raising=False)
    monkeypatch.setattr(E, "NATIVE_DIR", tmp_path)
    calls = []
    assert E.install_native(lambda c: calls.append(c) or "x86_64") is None and calls == []   # no extra round trip


def test_a_failed_install_falls_back(monkeypatch, tmp_path):
    b = tmp_path / "b"
    b.write_bytes(elf(0x3E))
    monkeypatch.setenv("LEGATO_HELPER_BIN", str(b))

    def run(cmd):
        if cmd.startswith("uname"):
            return "x86_64\n"
        if "printf" in cmd:
            return ""
        raise OSError("noexec")
    assert E.install_native(run) is None


@pytest.fixture
def display():
    # Xvfb reports an unused display on stdout only after it is ready to accept connections.
    with subprocess.Popen(["Xvfb", "-displayfd", "1", "-screen", "0", "800x600x24", "-noreset", "-nolisten", "tcp"],
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True) as server:
        try:
            number = server.stdout.readline().strip()
            assert number.isdecimal(), "Xvfb exited before reporting a display"
            yield f":{number}"
        finally:
            server.terminate()


def run_helper(argv, spec, disp):
    out = subprocess.run(argv + [json.dumps(spec)], capture_output=True, text=True, timeout=30,
                         env={**os.environ, "DISPLAY": disp})
    return json.loads(out.stdout.strip().splitlines()[-1])


@needs_bin
@needs_x
def test_same_answers_as_the_python_helper(display, tmp_path):
    from PIL import Image
    if not shutil.which("xwd") and not shutil.which("scrot"):
        pytest.skip("the Python helper needs xwd or scrot")
    py = tmp_path / "h.py"
    py.write_text(E.HELPER)
    spec = {"cmds": [["xdotool", "mousemove", "10", "10"], ["sleep", "0.01"]], "rule": LOCAL_RULES["click"],
            "shot": True}
    a, b = run_helper([sys.executable, str(py)], spec, display), run_helper([str(BIN)], spec, display)
    assert b["impl"] == "rust" and set(a) <= set(b)
    for r in (a, b):
        assert not r["capped"] and 0.4 <= r["settle_s"] < 1.5 and r["grabs"] >= 3
    if shutil.which("scrot"):              # the Python helper's frame comes from scrot
        ia, ib = (Image.open(io.BytesIO(base64.b64decode(r["png"]))).convert("RGB") for r in (a, b))
        assert ia.size == ib.size == (800, 600) and ia.tobytes() == ib.tobytes()
    zero = run_helper([str(BIN)], {"cmds": [], "rule": {**LOCAL_RULES["default"], "cap": 0}}, display)
    assert zero["capped"] and zero["grabs"] == 0 and "png" not in zero


@pytest.mark.parametrize("native", [False, True])
def test_zero_cap_executes_commands_without_capturing(native, tmp_path):
    if native and BIN is None:
        pytest.skip("native helper not built")
    helper = tmp_path / "helper.py"
    helper.write_text(E.HELPER)
    marker = tmp_path / "action"
    # A no-shot, no-wait call needs no display or screenshot utilities.
    cmd = [sys.executable, "-c", "from pathlib import Path; import sys; Path(sys.argv[1]).touch()", str(marker)]
    spec = {"cmds": [cmd], "rule": {**LOCAL_RULES["default"], "cap": 0}}
    proc = subprocess.run(([str(BIN)] if native else [sys.executable, str(helper)]) + [json.dumps(spec)],
                          env={**os.environ, "DISPLAY": "", "PATH": str(tmp_path)}, capture_output=True, check=True)
    out = json.loads(proc.stdout)
    assert marker.exists() and out["capped"] and out["grabs"] == 0 and "png" not in out


@pytest.mark.parametrize("native", [False, True])
@needs_x
def test_zero_cap_returns_a_fresh_screenshot(native, display, tmp_path):
    if native and BIN is None:
        pytest.skip("native helper not built")
    if not native and not shutil.which("scrot"):
        pytest.skip("the Python helper needs scrot")
    helper = tmp_path / "helper.py"
    helper.write_text(E.HELPER)
    argv = [str(BIN)] if native else [sys.executable, str(helper)]
    if not shutil.which("xsetroot"):
        pytest.skip("needs xsetroot to change the pixels")
    from PIL import Image
    for color, rgb in (("red", (255, 0, 0)), ("blue", (0, 0, 255))):
        spec = {"cmds": [["xsetroot", "-solid", color]], "rule": {**LOCAL_RULES["default"], "cap": 0}, "shot": True}
        out = run_helper(argv, spec, display)
        assert out["capped"] and out["grabs"] == 0
        with Image.open(io.BytesIO(base64.b64decode(out["png"]))) as im:
            assert im.size == (800, 600) and im.convert("RGB").getpixel((5, 5)) == rgb


@needs_bin
def test_bad_input_and_missing_programs_fail_loudly():
    assert subprocess.run([str(BIN), "{"], capture_output=True).returncode == 2
    r = subprocess.run([str(BIN), json.dumps({"cmds": [["no-such-program-xyz"]], "rule": {}})], capture_output=True)
    assert r.returncode == 3 and not r.stdout
    assert subprocess.run([str(BIN), "--version"], capture_output=True, text=True).stdout.startswith("legato-step ")


@needs_bin
def test_without_a_display_it_still_answers():
    env = {k: v for k, v in os.environ.items() if k != "DISPLAY"}
    r = subprocess.run([str(BIN), json.dumps({"rule": LOCAL_RULES["type"]})], capture_output=True, text=True,
                       env=env, timeout=10)
    out = json.loads(r.stdout)
    assert not out["capped"] and out["settle_s"] < 1.0


@needs_bin
@needs_x
def test_shell_attach_installs_it_once(display, tmp_path, monkeypatch):
    monkeypatch.setenv("LEGATO_HELPER_BIN", str(BIN))
    remote = E.native_path(BIN.read_bytes())
    Path(remote).unlink(missing_ok=True)
    calls = []

    def run(cmd):
        calls.append(cmd)
        return subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=30,
                              env={**os.environ, "DISPLAY": display, "HOME": str(tmp_path)}).stdout

    env = legato.attach(shell=run, blink_off=False)
    assert env.helper_cmd == remote and os.access(remote, os.X_OK)
    assert sum("printf" in c for c in calls) >= 2                # uploaded in chunks
    obs = env.act(Action("click", x=100, y=100))
    assert E.png_size(obs.screenshot) == (800, 600) and env.last["impl"] == "rust"
    assert remote in calls[-1]                                   # one call per action
    calls.clear()
    env2 = legato.attach(shell=run, blink_off=False)             # the same sandbox again: no upload
    assert env2.helper_cmd == remote and len(calls) == 2 and not any("printf" in c for c in calls)
    monkeypatch.setenv("LEGATO_HELPER", "python")
    env3 = legato.attach(shell=run, blink_off=False)
    assert env3.helper_cmd.startswith("python3") and env3.act(Action("wait")) and env3.last["impl"] == "python"


@needs_bin
@needs_x
def test_e2b_writes_the_binary_as_a_file(display, tmp_path, monkeypatch):
    monkeypatch.setenv("LEGATO_HELPER_BIN", str(BIN))
    remote = E.native_path(BIN.read_bytes())
    Path(remote).unlink(missing_ok=True)
    log, written = [], []

    class Commands:
        def run(self, cmd, timeout=None, background=False):
            log.append(cmd)
            r = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=30,
                               env={**os.environ, "DISPLAY": display})
            if r.returncode:                      # the E2B SDK raises on a non-zero exit
                raise RuntimeError(r.stderr)
            return type("R", (), {"stdout": r.stdout, "stderr": r.stderr, "exit_code": 0})()

    class Files:
        def write(self, path, data):
            written.append(path)
            Path(path).write_bytes(data if isinstance(data, bytes) else data.encode())

    sb = type("SB", (), {"commands": Commands(), "files": Files(), "screenshot": lambda self: b""})()
    env = legato.attach(sb, blink_off=False)
    assert env.helper and env.helper_cmd == remote and written and not any("printf" in c for c in log)
    obs = env.act(Action("type", text="hi"))
    assert E.png_size(obs.screenshot) == (800, 600) and env.last["impl"] == "rust"
