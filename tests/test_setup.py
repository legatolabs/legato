"""One saved model and desktop for every way of running Legato: legato.config, `legato setup`, legato.agent."""
import io
import json
import os
import stat
import sys

import pytest

import legato
from legato import config, setup
from legato.__main__ import main
from legato.harness import Harness, _answer


def saved(**kw):
    base = dict(provider="openrouter", model="qwen/qwen3.8-27b", base_url="https://openrouter.ai/api/v1", api_key="sk-1")
    base.update(kw)
    return config.save(config.Config(**base))


# ------------------------------------------------------------ the saved choice
def test_save_and_load_keep_the_choice_private(own_config):
    p = saved(desktop="background", app="Notes")
    assert p == own_config and stat.S_IMODE(os.stat(p).st_mode) == 0o600
    cfg = config.load()
    assert (cfg.model, cfg.key, cfg.desktop, cfg.app, cfg.ready) == ("qwen/qwen3.8-27b", "sk-1", "background", "Notes", True)


def test_environment_wins_and_unknown_keys_survive(own_config, monkeypatch):
    own_config.write_text(json.dumps({"model": "a", "base_url": "http://localhost:8000/v1", "later": 1}))
    monkeypatch.setenv("LEGATO_MODEL", "b")
    monkeypatch.setenv("LEGATO_DESKTOP", "local")
    cfg = config.load()
    assert cfg.model == "b" and cfg.desktop == "screen" and cfg.ready            # a local server needs no key
    config.save(cfg)
    assert json.loads(own_config.read_text())["later"] == 1


def test_a_key_in_the_environment_is_enough_to_start(monkeypatch):
    assert not config.load().ready and "legato setup" in config.load().missing()
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    cfg = config.load()
    assert cfg.ready and cfg.model == "qwen/qwen3.8-27b" and cfg.key == "k"
    assert not config.load(use_env=False).ready                                  # the file alone


def test_which_app_a_task_names():
    apps = ["Safari", "Notes", "Spotify", "Calendar", "Maps", "Home", "News", "Microsoft Excel", "Google Chrome"]
    assert config.app_for_task("Write a shopping list in Notes", apps) == "Notes"
    assert config.app_for_task("play some jazz on spotify", apps) == "Spotify"
    assert config.app_for_task("Add the totals in Excel", apps) == "Microsoft Excel"
    assert config.app_for_task("open chrome and search for cats", apps) == "Google Chrome"
    assert config.app_for_task("add lunch to my Google Calendar", apps) is None   # a web app: the browser
    assert config.app_for_task("go home and check the news", apps) is None       # everyday words
    assert config.app_for_task("look up the Eiffel Tower", apps) is None


def test_the_browser_is_used_only_where_background_mode_is_tested(monkeypatch):
    monkeypatch.setattr(config, "app_for_task", lambda task, apps=None: None)
    monkeypatch.setattr(config, "default_browser", lambda: "Firefox")
    assert config.pick_app("x", config.Config()) == "Safari"
    monkeypatch.setattr(config, "default_browser", lambda: "Google Chrome")
    assert config.pick_app("x", config.Config()) == "Safari"            # Chrome 158 ignores background clicks on pages
    monkeypatch.setattr(config, "app_for_task", lambda task, apps=None: "Google Chrome")
    assert config.pick_app("open chrome", config.Config()) == "Google Chrome"   # named, it is still used
    assert config.pick_app("x", config.Config(app="TextEdit")) == "TextEdit"


# ------------------------------------------------------------ the Harness and legato.run use it
def test_harness_with_no_model_uses_the_saved_one():
    with pytest.raises(ValueError, match="legato setup"):
        Harness()
    saved()
    h = Harness()
    assert h.model == "qwen/qwen3.8-27b" and h.client.cfg.api_key == "sk-1"
    assert Harness("other", "https://example.com/v1").client.cfg.api_key == ""   # the saved key stays with its server
    assert Harness("other", "https://openrouter.ai/api/v1/").client.cfg.api_key == "sk-1"


def test_agent_for_each_desktop(monkeypatch):
    saved()
    from legato import agent
    from legato.env import attach
    other = attach(screenshot=lambda: b"", execute=lambda a: None)
    h = agent.agent_for(other)                                                  # a sandbox: Harness's defaults
    assert not h.plan_mode and not h.end_fix and h.image_max == 8 and h.tools == {}
    monkeypatch.setattr(agent, "desktop_kind", lambda env: "mac-screen")        # the Mac app's setup
    h = agent.agent_for(other)
    assert h.plan_mode and h.end_fix and h.image_max == 4 and set(h.tools) == {"bash", "applescript"}
    monkeypatch.setattr(agent, "desktop_kind", lambda env: "mac-background")    # no shell to take the screen
    h = agent.agent_for(other)
    assert not h.plan_mode and h.end_fix and h.image_max == 4 and h.tools == {}
    assert agent.agent_for(other, image_max=2).image_max == 2                    # options win


def test_answer_is_the_done_text_or_the_reply():
    from legato.actions import Action
    assert _answer({"content": "Looking."}, [Action("done", text="330 m")]) == "330 m"
    assert _answer({"content": 'It is 330 m.\n{"action": "done", "text": "x"}'}, []) in ("x", "It is 330 m.")
    assert _answer({"content": "It is 330 m tall."}, [Action("done")]) == "It is 330 m tall."


def test_run_without_a_model_says_to_set_up(capsys):
    assert main(["run", "a task"]) == 2
    assert "legato setup" in capsys.readouterr().err


# ------------------------------------------------------------ legato setup
@pytest.fixture()
def no_machine(monkeypatch):
    """No real checks or changes to this machine's coding agents or permissions."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(setup, "coding_agents", lambda: [])
    monkeypatch.setattr(setup, "permission_lines", lambda request=False: [])


def test_setup_yes_with_a_key_on_stdin(no_machine, monkeypatch, own_config, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("sk-ant-123\n"))
    assert main(["setup", "--yes", "--no-check", "--provider", "anthropic", "--api-key", "-", "--desktop", "docker"]) == 0
    cfg = config.load()
    assert (cfg.provider, cfg.model, cfg.base_url, cfg.key, cfg.desktop) == (
        "anthropic", "claude-sonnet-5-5", "https://api.anthropic.com/v1", "sk-ant-123", "docker")
    assert "Saved to" in capsys.readouterr().out


def test_setup_yes_uses_a_key_from_the_environment_without_saving_it(no_machine, monkeypatch, own_config):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-1")
    monkeypatch.setattr(sys, "platform", "linux")
    assert main(["setup", "--yes", "--no-check"]) == 0
    data = json.loads(own_config.read_text())
    assert data["api_key_env"] == "OPENROUTER_API_KEY" and "api_key" not in data and data["desktop"] == "screen"
    assert config.load().key == "sk-or-1"


def test_setup_json_reports_what_is_missing(no_machine, capsys):
    assert main(["setup", "--json"]) == 0
    st = json.loads(capsys.readouterr().out)
    assert st["ready"] is False and any("legato setup" in m for m in st["missing"])


def test_setup_checks_the_model(no_machine, monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr(config, "check_model", lambda m, u, k: seen.update(m=m, k=k) or
                        {"ok": True, "seconds": 1.2, "sees_images": True})
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    assert main(["setup", "--yes", "--desktop", "screen"]) == 0
    assert seen == {"m": "qwen/qwen3.8-27b", "k": "k"} and "ok, 1.2 s a call" in capsys.readouterr().out


def test_setup_adds_legato_to_coding_agents(no_machine, monkeypatch, tmp_path):
    calls = []

    class Done:
        returncode, stdout, stderr = 0, "", ""
    monkeypatch.setattr(setup, "coding_agents", lambda: ["claude", "cursor"])
    monkeypatch.setattr(setup.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or Done())
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/local/bin/{name}")
    cursor = tmp_path / ".cursor" / "mcp.json"
    cursor.parent.mkdir()
    cursor.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
    monkeypatch.setattr(setup, "_cursor_file", lambda: cursor)
    monkeypatch.setattr(setup, "legato_command", lambda: ["/venv/bin/legato"])
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    assert main(["setup", "--yes", "--no-check", "--desktop", "screen"]) == 0
    assert ["claude", "mcp", "add", "legato", "-s", "user", "--", "/venv/bin/legato", "mcp"] in calls
    servers = json.loads(cursor.read_text())["mcpServers"]
    assert servers["other"] == {"command": "x"} and servers["legato"] == {"command": "/venv/bin/legato", "args": ["mcp"]}


def test_setup_mcp_none_adds_nothing(no_machine, monkeypatch):
    monkeypatch.setattr(setup, "coding_agents", lambda: ["claude"])
    monkeypatch.setattr(setup, "add_mcp", lambda c: pytest.fail("should not add"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    assert main(["setup", "--yes", "--no-check", "--desktop", "screen", "--mcp", "none"]) == 0


def test_legato_command_from_uvx(monkeypatch):
    monkeypatch.setattr(sys, "prefix", "/Users/x/.cache/uv/archive-v0/abc")
    assert setup.legato_command() == ["uvx", "--from", "legatolabs", "legato"]


def test_legato_run_is_legato_dot_run():
    assert legato.run is legato.agent.run


def test_setup_does_not_save_a_model_that_does_not_answer(no_machine, monkeypatch, own_config, capsys):
    monkeypatch.setattr(config, "check_model", lambda m, u, k: {"ok": False, "error": "HTTP 401: invalid key"})
    monkeypatch.setenv("OPENROUTER_API_KEY", "bad")
    assert main(["setup", "--yes", "--desktop", "screen"]) == 1
    assert not own_config.exists() and "nothing was saved" in capsys.readouterr().err
    assert main(["setup", "--yes", "--desktop", "screen", "--no-check"]) == 0 and own_config.exists()


def test_a_thinking_model_that_answers_nothing_is_not_called_blind(no_machine, monkeypatch, capsys):
    monkeypatch.setattr(config, "check_model", lambda m, u, k: {"ok": True, "seconds": 0.5, "sees_images": None})
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    assert main(["setup", "--yes", "--desktop", "screen"]) == 0
    assert "vision model" not in capsys.readouterr().out


# ------------------------------------------------------------ your own sandbox
BOX = '''
from pathlib import Path
import legato

LOG = Path(__file__).with_suffix(".log")

def desktop(width, height):
    with LOG.open("a") as f:
        f.write(f"open {width}x{height}\\n")
    env = legato.attach(screenshot=lambda: b"", execute=lambda action: None)
    return (lambda: LOG.open("a").write("close\\n")), env
'''


def test_desktop_spec_takes_your_own_sandbox(tmp_path, monkeypatch):
    (tmp_path / "box.py").write_text(BOX)
    monkeypatch.chdir(tmp_path)
    assert config.desktop_spec("local") == "screen" and config.desktop_spec("docker:me/desk") == "docker:me/desk"
    assert config.desktop_spec("box.py:desktop") == f"{(tmp_path / 'box.py').resolve()}:desktop"   # works from anywhere
    assert config.desktop_spec("mycompany.desktops:cloud") == "mycompany.desktops:cloud"
    for bad in ("nowhere.py:desktop", "somewhere", "box.py:", "docker:"):
        with pytest.raises(ValueError):
            config.desktop_spec(bad)
    assert config.is_own("mycompany.desktops:cloud") and not any(map(config.is_own, ["docker:x", "background", "local"]))


def test_setup_saves_your_own_sandbox_and_every_run_uses_it(no_machine, monkeypatch, own_config, tmp_path):
    box = tmp_path / "box.py"
    box.write_text(BOX)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "stdin", io.StringIO("sk-1\n"))
    assert main(["setup", "--yes", "--no-check", "--provider", "openrouter", "--api-key", "-",
                 "--desktop", "box.py:desktop"]) == 0
    spec = f"{box.resolve()}:desktop"
    assert config.load().desktop == spec
    monkeypatch.chdir(own_config.parent.parent)                       # somewhere else
    from legato.agent import open_desktop
    env, close, what = open_desktop()
    close()
    assert (tmp_path / "box.log").read_text() == "open 1280x800\nclose\n" and what == spec


def test_setup_refuses_a_sandbox_function_it_cannot_find(no_machine, monkeypatch, own_config, tmp_path, capsys):
    (tmp_path / "box.py").write_text("def other(width, height): pass\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "stdin", io.StringIO("sk-1\n"))
    assert main(["setup", "--yes", "--no-check", "--provider", "openrouter", "--api-key", "-",
                 "--desktop", "box.py:desktop"]) == 2
    assert "cannot use that desktop" in capsys.readouterr().err and not own_config.exists()


def test_legato_run_with_no_flags_runs_in_your_saved_sandbox(monkeypatch, own_config, tmp_path, capsys):
    """The whole path: setup saved a sandbox function; `legato run "task"` opens it, the model acts, it closes it."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    replies = [{"action": "left_click", "coordinate": [10, 10]}, {"action": "done", "text": "hello"}]
    seen = []

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            seen.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            call = {"id": f"c{len(seen)}", "type": "function",
                    "function": {"name": "computer", "arguments": json.dumps(replies.pop(0))}}
            out = json.dumps({"choices": [{"message": {"content": "", "tool_calls": [call]}}],
                              "usage": {"prompt_tokens": 10, "completion_tokens": 3}}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Model)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    box = tmp_path / "box.py"
    box.write_text('''
from pathlib import Path
import legato
from legato.rl.fake import tiny_png

LOG = Path(__file__).with_suffix(".log")

def desktop(width, height):
    LOG.open("a").write("open\\n")
    env = legato.attach(screenshot=lambda: tiny_png(1, w=64, h=40),
                        execute=lambda a: LOG.open("a").write(f"{a.type}\\n"))
    return (lambda: LOG.open("a").write("close\\n")), env
''')
    try:
        saved(provider="custom", model="m", base_url=f"http://127.0.0.1:{srv.server_address[1]}/v1",
              desktop=config.desktop_spec(str(box) + ":desktop"))
        monkeypatch.chdir(own_config.parent.parent)
        assert main(["run", "Say hello", "--json"]) == 0
    finally:
        srv.shutdown()
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["done"] is True and out["answer"] == "hello"
    assert (tmp_path / "box.log").read_text() == "open\nclick\nclose\n" and len(seen) == 2


def test_legato_run_json_prints_json_when_it_cannot_start(capsys):
    """Agents parse `legato run --json`: a failure before the task starts is one JSON object too, plus the stderr line."""
    assert main(["run", "Say hello", "--json"]) == 2
    out, err = capsys.readouterr()
    got = json.loads(out.strip().splitlines()[-1])
    assert got["done"] is False and "legato setup" in got["error"] and "legato setup" in err


def test_a_desktop_that_fails_mid_run_is_one_line_and_json(monkeypatch, own_config, capsys):
    """A capture that fails after the run started: no traceback, and with --json the run's result with its status."""
    import legato.agent
    saved()
    def broken():
        raise RuntimeError("the window could not be captured")
    env = legato.attach(screenshot=broken, execute=lambda a: None)
    monkeypatch.setattr(legato.agent, "open_desktop", lambda *a, **k: (env, lambda: None, "a test desktop"))
    assert main(["run", "Say hello", "--json"]) == 2
    out, err = capsys.readouterr()
    got = json.loads(out.strip().splitlines()[-1])
    assert got["status"] == "error" and got["done"] is False and got["failed"] is False
    assert got["error"] == "the desktop failed: the window could not be captured" and "usage" in got
    assert "Traceback" not in err
    assert main(["run", "Say hello"]) == 2
    out, err = capsys.readouterr()
    assert "\nthe desktop failed: the window could not be captured (after " in out and "Traceback" not in err


def test_legato_run_timeout_and_status_in_json(monkeypatch, own_config, capsys):
    import legato.agent
    from legato.rl.fake import tiny_png
    saved()
    env = legato.attach(screenshot=lambda: tiny_png(1), execute=lambda a: None)
    monkeypatch.setattr(legato.agent, "open_desktop", lambda *a, **k: (env, lambda: None, "a test desktop"))
    assert main(["run", "Say hello", "--json", "--timeout", "0"]) == 1       # out of time before the first call
    got = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert (got["status"], got["steps"], got["done"]) == ("timeout", 0, False) and "time limit" in got["error"]


def test_legato_run_json_says_error_when_it_cannot_start(capsys):
    assert main(["run", "Say hello", "--json"]) == 2
    got = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert got["status"] == "error" and got["done"] is False


def test_legato_run_timeout_covers_opening_the_desktop(monkeypatch, own_config, capsys):
    import legato.agent
    from legato.rl.fake import tiny_png
    saved()
    seen = {}

    def opened(*a, **k):
        seen.update(k)
        return legato.attach(screenshot=lambda: tiny_png(1), execute=lambda a: None), (lambda: None), "a test desktop"
    monkeypatch.setattr(legato.agent, "open_desktop", opened)
    assert main(["run", "Say hello", "--json", "--timeout", "0"]) == 1
    got = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert seen["timeout_s"] == 0 and got["error"] == "the run's time limit (0 s) ran out"


def test_a_coding_agent_that_is_not_installed_is_said_plainly(monkeypatch):
    import shutil

    from legato import setup
    monkeypatch.setattr(shutil, "which", lambda name: None)
    try:
        setup.add_mcp("claude")
    except RuntimeError as e:
        assert "claude is not installed here" in str(e) and "nothing was added" in str(e)
    else:
        raise AssertionError("a missing client must not pass silently")


def test_doctor_says_when_the_legato_command_is_not_on_path(monkeypatch, capsys):
    import shutil

    from legato import doctor
    monkeypatch.setattr(shutil, "which", lambda name: None)
    c = doctor.checks()
    assert c["command"]["ok"] is False and "-m legato" in c["command"]["fix"] and "PATH" in c["command"]["fix"]


def test_an_environment_variable_is_written_as_the_platforms_shell_writes_it(monkeypatch):
    from legato import config
    monkeypatch.setattr(config.sys, "platform", "win32")
    assert config.env_name("OPENROUTER_API_KEY") == "$env:OPENROUTER_API_KEY"
    monkeypatch.setattr(config.sys, "platform", "darwin")
    assert config.env_name("OPENROUTER_API_KEY") == "$OPENROUTER_API_KEY"


def test_a_saved_key_is_not_sent_to_a_server_named_by_the_environment(monkeypatch, tmp_path):
    from legato import config
    monkeypatch.setenv("LEGATO_CONFIG", str(tmp_path / "config.json"))
    for v in ("LEGATO_API_KEY", "LEGATO_BASE_URL", "LEGATO_MODEL", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(v, raising=False)
    config.save(config.Config(provider="openrouter", model="qwen/qwen3.8-27b", api_key="sk-or-saved"))
    assert config.load().key == "sk-or-saved"
    monkeypatch.setenv("LEGATO_BASE_URL", "http://localhost:8000/v1")
    cfg = config.load()
    assert cfg.base_url == "http://localhost:8000/v1" and cfg.key == "" and cfg.key_source == ""
    monkeypatch.setenv("LEGATO_API_KEY", "its-own")
    assert config.load().key == "its-own"
    monkeypatch.delenv("LEGATO_API_KEY")
    monkeypatch.setenv("LEGATO_BASE_URL", "https://openrouter.ai/api/v1/")        # the same server: the key stays
    assert config.load().key == "sk-or-saved"
    config.save(config.Config(provider="openrouter", model="m", api_key_env="OPENROUTER_API_KEY"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-env")
    monkeypatch.setenv("LEGATO_BASE_URL", "http://other.example/v1")
    assert config.load().key == ""


# ------------------------------------------------------------ what the model check finds, saved for every run
def test_setup_saves_what_the_model_needs_and_every_run_uses_it(no_machine, monkeypatch, own_config, capsys):
    monkeypatch.setattr(config, "check_model", lambda m, u, k: {
        "ok": True, "seconds": 1.8, "sees_images": True, "tool_calls": "json", "coords": "pixels",
        "coords_default": "norm1000", "fix": None})
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    assert main(["setup", "--yes", "--mcp", "none", "--desktop", "screen", "--model", "mistralai/pixtral-large-2502"]) == 0
    out = capsys.readouterr().out
    assert "ok, 1.8 s a call; it takes no tool calls here, so Legato will ask it for its actions as JSON (saved)" in out
    assert "its clicks are in pixels (saved)" in out
    saved_file = json.loads(own_config.read_text())
    assert (saved_file["tool_calls"], saved_file["coords"]) == ("json", "pixels")
    h = legato.agent.agent_for(None)
    assert h.native_tools is False and h.coords == "pixels"
    assert legato.agent.agent_for(None, coords="norm1000").coords == "norm1000"        # what the caller passes wins
    other = legato.agent.agent_for(None, model="qwen/qwen3.8-27b")                     # another model: its own defaults
    assert other.native_tools is True and other.coords == "norm1000"
    # a model that needs nothing unusual clears what the last one needed
    monkeypatch.setattr(config, "check_model", lambda m, u, k: {
        "ok": True, "seconds": 0.8, "sees_images": True, "tool_calls": "native", "coords": "norm1000",
        "coords_default": "norm1000", "fix": None})
    assert main(["setup", "--yes", "--mcp", "none", "--model", "qwen/qwen3.8-27b"]) == 0
    assert not {"tool_calls", "coords"} & set(json.loads(own_config.read_text()))
    assert main(["setup", "--yes", "--mcp", "none", "--no-check", "--coords", "pixels"]) == 0     # by hand
    assert json.loads(own_config.read_text())["coords"] == "pixels"


def test_setup_refuses_a_text_only_model_in_plain_words(no_machine, monkeypatch, own_config, capsys):
    monkeypatch.setattr(config, "check_model", lambda m, u, k: {
        "ok": False, "answers": True, "images": False, "error": "HTTP 404: No endpoints found that support image input",
        "fix": "this model does not accept images; Legato needs a vision model, for example a/one or b/two"})
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    assert main(["setup", "--yes", "--mcp", "none", "--model", "deepseek/deepseek-v4-flash"]) == 1
    cap = capsys.readouterr()
    assert "nothing was saved: this model does not accept images; Legato needs a vision model, for example a/one" in cap.err
    assert "Check the key" not in cap.err + cap.out and not own_config.exists()


def test_setup_local_with_no_server_says_where_it_looked(no_machine, monkeypatch, own_config, capsys):
    monkeypatch.setattr(config, "find_local_server", lambda: None)
    monkeypatch.setattr(config, "list_models", lambda *a, **k: pytest.fail("no second wait for a server that is not there"))
    assert main(["setup", "--yes", "--mcp", "none", "--provider", "local"]) == 2
    err = capsys.readouterr().err
    assert all(s in err for s in ("no model server answered", ":8000", ":11434", ":1234", "ollama serve", "vllm serve"))
    assert not own_config.exists()


def test_setup_without_a_key_does_not_say_run_is_next(no_machine, monkeypatch, own_config, capsys):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(config, "_windows_owner_only", lambda p: True)
    assert main(["setup", "--yes", "--mcp", "none", "--provider", "openrouter", "--desktop", "screen"]) == 0
    out = capsys.readouterr().out
    assert 'no key yet: $env:OPENROUTER_API_KEY = "..."' in out and "export" not in out
    assert "Before a task can run: no key for" in out and "Next: legato run" not in out
    monkeypatch.setattr(sys, "platform", "linux")
    assert "export OPENROUTER_API_KEY=..." in config.load().missing()


def test_find_local_server_asks_them_together_and_takes_the_first_in_order(monkeypatch):
    monkeypatch.setattr(config, "list_models", lambda u, timeout_s=0: ["m"] if u.endswith(("b", "c")) else [])
    assert config.find_local_server(("http://a", "http://b", "http://c")) == ("http://b", ["m"])
    assert config.find_local_server(("http://a",)) is None


def test_a_saved_key_is_not_sent_to_a_new_base_url(monkeypatch):
    """The security review: `legato setup --base-url <another server>` reused the key saved for the provider's own
    server and sent it there in the model check. A saved key now goes only to the server it was saved for."""
    from legato import config
    from legato.setup import main
    config.save(config.Config(provider="openrouter", model="qwen/qwen3.8-27b",
                              base_url="https://openrouter.ai/api/v1", api_key="sk-or-saved"))
    seen = {}
    monkeypatch.setattr(config, "check_model", lambda m, u, k: seen.update(u=u, k=k) or
                        {"ok": True, "seconds": 0.5, "sees_images": True})
    main(["--yes", "--mcp", "none", "--provider", "openrouter", "--base-url", "https://elsewhere.example/v1"])
    assert seen.get("k") != "sk-or-saved"                  # not sent to the new server (with no key, not checked)
    saved = config.load(use_env=False)
    assert not (saved.base_url == "https://elsewhere.example/v1" and saved.api_key == "sk-or-saved")
