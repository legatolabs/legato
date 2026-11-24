"""The first run: `legato` alone says what to do, `legato init` writes an agent.py that runs, `legato run --background`."""
import sys

import pytest

import legato
from legato import start
import legato.local  # noqa: F401  (loads the module)
from legato.__main__ import main
from legato.harness import RunResult


@pytest.fixture()
def quiet(monkeypatch):
    """No questions, no real checks of this machine."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(start, "missing", lambda desktop, key_env: [])
    monkeypatch.setattr(start, "default_browser", lambda: "Google Chrome")


def test_legato_alone_says_what_to_do(capsys):
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "legato init" in out and "legato doctor" in out
    assert "claude mcp add legato -- legato mcp --background" in out
    assert "https://legato.computer/llms.txt" in out             # the prompt for a coding agent


def test_init_writes_an_agent_that_compiles(tmp_path, quiet, monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "darwin")
    out = tmp_path / "agent.py"
    assert main(["init", "--yes", "--out", str(out)]) == 0
    src = out.read_text()
    compile(src, str(out), "exec")
    assert 'model="qwen/qwen3.8-27b"' in src and 'base_url="https://openrouter.ai/api/v1"' in src
    assert 'os.environ.get("OPENROUTER_API_KEY", "")' in src
    assert 'legato.local(background="Google Chrome")' in src     # the default browser, in the background
    assert f'python {out} "your task"' in capsys.readouterr().out


def test_the_written_agent_runs_the_loop(tmp_path, quiet, monkeypatch):
    out = tmp_path / "agent.py"
    main(["init", "--yes", "--desktop", "local", "--out", str(out)])
    seen = {}

    class FakeHarness:
        def __init__(self, **kw):
            seen["harness"] = kw

        def run(self, task, env, max_steps, on_step):
            seen["run"] = (task, env, max_steps)
            on_step(0, [None], {})
            r = RunResult(task)
            r.done = True
            return r

    monkeypatch.setattr(legato, "Harness", FakeHarness)
    monkeypatch.setattr(legato, "local", lambda **kw: ("desk", kw))
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr(sys, "argv", ["agent.py", "open", "the", "terminal"])
    exec(compile(out.read_text(), str(out), "exec"), {"__name__": "__main__"})
    assert seen["harness"] == {"model": "qwen/qwen3.8-27b", "base_url": "https://openrouter.ai/api/v1", "api_key": "k"}
    assert seen["run"] == ("open the terminal", ("desk", {}), 30)


def test_init_local_model_and_docker(tmp_path, quiet):
    out = tmp_path / "a.py"
    assert main(["init", "--yes", "--provider", "local", "--desktop", "docker", "--out", str(out)]) == 0
    src = out.read_text()
    compile(src, str(out), "exec")
    assert 'base_url="http://localhost:8000/v1"' in src and 'api_key=""' in src
    assert "stop, desk = legato.providers.docker(1280, 800)" in src and src.rstrip().endswith("stop()")


def test_init_any_app_and_a_note_for_untested_ones(tmp_path, quiet, monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "darwin")
    out = tmp_path / "a.py"
    assert main(["init", "--yes", "--app", "Firefox", "--out", str(out)]) == 0
    assert 'legato.local(background="Firefox")' in out.read_text()
    assert "tested on Safari, Google Chrome, TextEdit" in capsys.readouterr().out


def test_init_background_is_mac_only(tmp_path, quiet, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    out = tmp_path / "a.py"
    assert main(["init", "--yes", "--out", str(out)]) == 0
    assert "legato.local()" in out.read_text()                   # off a Mac the default is this screen
    assert main(["init", "--yes", "--desktop", "background", "--force", "--out", str(out)]) == 2


def test_init_does_not_overwrite(tmp_path, quiet):
    out = tmp_path / "agent.py"
    out.write_text("mine")
    assert main(["init", "--yes", "--out", str(out)]) == 2
    assert out.read_text() == "mine"
    assert main(["init", "--yes", "--force", "--out", str(out)]) == 0


def test_init_asks_when_in_a_terminal(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(start, "missing", lambda desktop, key_env: [])
    monkeypatch.setattr(start, "default_browser", lambda: "Safari")
    answers = iter(["3", "https://api.example.com/v1", "my-model", "EXAMPLE_KEY", "1", "TextEdit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    out = tmp_path / "a.py"
    assert main(["init", "--out", str(out)]) == 0
    src = out.read_text()
    assert 'model="my-model"' in src and 'base_url="https://api.example.com/v1"' in src
    assert 'os.environ.get("EXAMPLE_KEY", "")' in src and 'background="TextEdit"' in src


def test_init_stopped_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    def stop(prompt=""):
        raise KeyboardInterrupt
    monkeypatch.setattr("builtins.input", stop)
    out = tmp_path / "a.py"
    assert main(["init", "--out", str(out)]) == 1
    assert not out.exists()


def test_missing_names_the_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    todo = start.missing("docker", "OPENROUTER_API_KEY")
    assert any("export OPENROUTER_API_KEY" in t and "openrouter.ai/keys" in t for t in todo)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    assert not any("OPENROUTER_API_KEY" in t for t in start.missing("docker", "OPENROUTER_API_KEY"))


def test_default_browser_off_a_mac(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert start.default_browser() is None


def test_run_background_works_in_one_app(monkeypatch, capsys):
    seen = {}

    def fake_local(**kw):
        seen.update(kw)
        raise RuntimeError("stop here")
    monkeypatch.setattr(sys.modules["legato.local"], "local", fake_local)   # legato.local is also the function
    assert main(["run", "a task", "--model", "m", "--base-url", "http://localhost:8000/v1", "--background", "Safari"]) == 2
    assert seen["background"] == "Safari"
    assert "stop here" in capsys.readouterr().err


# ------------------------------------------------------------ a missing package or sandbox: one line, no traceback
def test_mcp_without_the_mcp_package(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "legato.mcp_server", None)          # as if `mcp` were not installed
    assert main(["mcp"]) == 2
    assert capsys.readouterr().err.startswith("legato: ")


def test_rl_alone_lists_its_commands(capsys):
    assert main(["rl"]) == 2
    out = capsys.readouterr().out
    assert "probe" in out and "collect" in out


def test_a_sandbox_that_cannot_start(tmp_path, capsys):
    f = tmp_path / "desk.py"
    f.write_text("def desktop(w, h):\n    raise RuntimeError('Docker is installed but not running')\n")
    assert main(["run", "a task", "--model", "m", "--base-url", "http://localhost:8000/v1", "--sandbox", f"{f}:desktop"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("legato: cannot start --sandbox") and "Docker is installed but not running" in err


def test_background_without_the_mac_extras(monkeypatch):
    from legato.background import MacApp
    monkeypatch.setitem(sys.modules, "ApplicationServices", None)
    with pytest.raises(ImportError, match=r'pip install "legatolabs\[local\]"'):
        MacApp("Safari")
