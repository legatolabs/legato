"""legato.tools: the Tool type, the bash tool and each desktop's shell."""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import legato  # noqa: E402
from legato.env import E2BEnv  # noqa: E402
from legato.local import LocalEnv  # noqa: E402
from legato.tools import ToolCall, clip, wrap_command  # noqa: E402


def test_tool_spec_and_name_check():
    t = legato.Tool("note", "Write a note.", {"type": "object"}, lambda a: "ok")
    assert t.spec() == {"type": "function", "function": {"name": "note", "description": "Write a note.",
                                                         "parameters": {"type": "object"}}}
    for bad in ("", "has space", "x" * 65):
        with pytest.raises(ValueError):
            legato.Tool(bad, "", {}, lambda a: "")
    assert ToolCall("bash", {"command": "ls"}).describe() == 'bash({"command": "ls"})'
    assert ToolCall("bash", {"command": "x" * 200}).describe().endswith('...)')


def test_clip_keeps_both_ends():
    assert clip("short", 20) == "short"
    assert clip("a" * 50 + "b" * 50, 20) == "a" * 10 + "\n...(80 characters cut)...\n" + "b" * 10


def test_bash_on_this_computer():
    sh = legato.tools.bash(timeout_s=5)
    assert sh.name == "bash" and sh.parameters["required"] == ["command"]
    assert sh.handler({"command": "echo hi; echo oops >&2; exit 3"}) == "hi\noops\n[exit 3]"
    assert sh.handler({}) == sh.handler({"command": "  "}) == "error: give a command"
    assert legato.tools.bash(max_chars=10).handler({"command": "printf '%040d' 0"}).count("characters cut") == 1


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
def test_bash_timeout_kills_background_children():
    t = time.monotonic()
    out = legato.tools.bash(timeout_s=0.5).handler({"command": "sleep 5 & sleep 5"})
    assert "[exit timeout after 0.5 s]" in out and time.monotonic() - t < 3


@pytest.mark.skipif(shutil.which("timeout") is None, reason="wrap_command is for Linux shells, which have GNU timeout")
def test_wrapped_command_runs_in_a_real_shell():
    out = legato.tools.local_shell(wrap_command("echo $((6 * 7)); false", 5), 10)
    assert out.startswith("42\n[exit 1]")


def test_bash_in_a_shell_sandbox():
    sent = []
    env = legato.attach(shell=lambda cmd: sent.append(cmd) or "out\n[exit 0]\n")
    assert legato.tools.bash(env).handler({"command": "ls ~"}) == "out\n[exit 0]\n"
    assert sent[-1] == env.PRE + wrap_command("ls ~", 30)
    via_fn = legato.tools.bash(lambda cmd: sent.append(cmd) or b"ok")
    assert via_fn.handler({"command": "pwd"}) == "ok" and sent[-1] == wrap_command("pwd", 30)


def test_bash_in_e2b():
    calls = []

    def run(cmd, timeout=None):
        calls.append((cmd, timeout))
        return SimpleNamespace(stdout="hi\n[exit 0]\n", stderr="")
    env = E2BEnv.__new__(E2BEnv)                      # skip the helper install: only the shell is under test
    env.sb = SimpleNamespace(commands=SimpleNamespace(run=run))
    assert legato.tools.bash(env, timeout_s=10).handler({"command": "echo hi"}) == "hi\n[exit 0]\n"
    assert calls == [(wrap_command("echo hi", 10), 25)]


def test_local_env_has_this_computers_shell():
    assert LocalEnv.shell is legato.tools.local_shell


def test_a_desktop_without_a_shell_is_refused():
    env = legato.attach(screenshot=lambda: b"", execute=lambda a: None)
    assert env.shell is None
    with pytest.raises(TypeError):
        legato.tools.bash(env)


def test_the_shell_returns_when_its_command_ends_not_when_a_program_it_started_does():
    """A command that only starts something (a browser, a server) used to hold the tool until that program closed
    its output: `Start-Process msedge <url>` took 12 s or more."""
    import sys
    import time

    from legato.tools import local_shell
    if sys.platform == "win32":
        cmd = "Start-Process powershell -ArgumentList '-Command', 'Start-Sleep 6' -NoNewWindow; 'started'"
    else:
        cmd = "(sleep 6 &) ; echo started"
    t = time.monotonic()
    out = local_shell(cmd, timeout_s=20)
    assert "started" in out and out.rstrip().endswith("[exit 0]") and time.monotonic() - t < 4
    t = time.monotonic()
    out = local_shell("sleep 30" if sys.platform != "win32" else "Start-Sleep 30", timeout_s=0.5)
    assert "timeout after 0.5 s" in out and time.monotonic() - t < 5


def test_on_windows_the_shell_finds_programs_registered_by_name():
    """`Start-Process msedge` takes about 12 s when Edge's folder is not on PATH; the tool adds the folders of the
    programs Windows has registered by name (App Paths)."""
    from legato.tools import app_path_dirs
    entries = [(r'"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"', None),
               (r"C:\Program Files\Office\WINWORD.EXE", r"C:\Program Files\Office\;C:\Program Files\Common\Office"),
               (None, None), (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge_proxy.exe", None)]
    assert app_path_dirs(lambda: iter(entries)) == [r"C:\Program Files (x86)\Microsoft\Edge\Application",
                                                    r"C:\Program Files\Office", r"C:\Program Files\Common\Office"]

    def broken():
        raise OSError("no registry")
        yield
    assert app_path_dirs(broken) == []


def test_the_windows_shell_runs_with_those_folders_at_the_end_of_path(monkeypatch):
    import subprocess

    import legato.tools as tools
    ran = {}

    class Done:
        returncode = 0

        def wait(self, timeout=None):
            return 0
    monkeypatch.setattr(tools.sys, "platform", "win32")
    monkeypatch.setattr(tools, "_APP_DIRS", [r"C:\Edge\Application", r"C:\Windows"])
    monkeypatch.setenv("PATH", r"C:\Windows;C:\Tools")
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: (ran.update(kw=kw), Done())[1])
    tools.local_shell("Start-Process msedge")
    assert ran["kw"]["env"]["PATH"] == r"C:\Windows;C:\Tools;C:\Edge\Application"       # added once, after what was there
