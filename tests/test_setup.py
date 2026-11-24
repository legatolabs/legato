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
    assert config.pick_app("x", config.Config()) == "Google Chrome"
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
