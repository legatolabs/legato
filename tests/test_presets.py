"""legato.presets.mac: the Mac app's harness (the mac-2 champion) on a fake Mac, with its command guard."""
import json

import pytest

from legato import plan as plans
from legato import presets, safety
from legato.local import LocalEnv
from test_harness import server  # noqa: F401
from test_local import Gui, Screen


def mac(confirm=None, shell=None, **kw):
    """A fake Mac: the screen, mouse and shell are fakes; nothing runs on this computer."""
    e = LocalEnv(fixed_wait_s=0, gui=Gui(), grabber=Screen(), confirm=confirm, **kw)
    e.mac = True
    ran = []
    e.shell = shell or (lambda cmd, timeout_s=30.0: ran.append(cmd) or "ok\n[exit 0]")
    e.ran = ran
    return e


def test_the_preset_is_the_champion():
    env = mac(wait="cells")
    h = presets.mac(env, "some/model", "http://127.0.0.1:9/v1", "k")
    # legato-research tracks/mac/champion.json (mac-2): its harness options and tools, exactly
    assert {**presets.MAC_OPTIONS, "extra_tools": list(presets.MAC_TOOLS)} == {
        "image_max": 4, "extra_tools": ["bash", "applescript"], "plan": True, "end_fix": True, "image_format": "jpeg"}
    assert (h.image_max, h.plan_mode, h.end_fix, h.image_format) == (4, True, True, "jpeg")
    assert list(h.tools) == ["bash", "applescript"] and h.coords == "norm1000"
    assert h.system == presets.plan_system() and "Only words can be read" in h.system
    assert h.tools["bash"].description == presets.SHELL_HINT
    assert h.tools["applescript"].description == presets.APPLESCRIPT_HINT
    claude = presets.mac(env, "anthropic/claude-x", "http://127.0.0.1:9/v1")
    assert claude.coords == "pixels"                        # still model-agnostic: the Harness picks per model
    assert presets.mac(env, plan=False).system is None      # no plans prompt without plans
    assert presets.mac(env, image_max=8).image_max == 8


def test_mac_env_is_local_with_the_cells_wait(monkeypatch):
    got = {}
    import sys
    monkeypatch.setattr(sys.modules["legato.local"], "local", lambda **kw: got.update(kw) or "env")
    assert presets.mac_env(confirm=safety.deny) == "env"
    assert got == {"confirm": safety.deny, "wait": "cells"}
    presets.mac_env(wait="screen")
    assert got["wait"] == "screen"


def test_plans_prompt_keeps_its_fields_and_limits_applescript():
    s = presets.plan_system()
    assert s.count("{w}") == 1 and "{batch}" in s
    text = s.format(w=1440, h=900, coords="c", batch=12)
    assert "Calendar" in text and "never to search or list" in text
    assert plans.PLAN_SYSTEM.split("- applescript")[0] == s.split("- applescript")[0]


def test_shell_asks_before_what_the_guard_flags_and_a_no_stops_the_plan():
    asked, answer = [], [False]
    env = mac(confirm=lambda q: asked.append(q) or answer[0])
    sh = presets.shell_tool(env)
    out = sh.handler({"command": "rm -rf ~/Downloads/old"})
    assert out.startswith("not run:") and "deletes files" in out and plans.tool_failed(out)
    assert env.ran == [] and "rm -rf ~/Downloads/old" in asked[0] and "deletes files" in asked[0]
    assert sh.handler({"command": "open -a Spotify"}) == "ok\n[exit 0]" and len(asked) == 1   # runs unasked
    answer[0] = True
    assert sh.handler({"command": "pip install foo"}) == "ok\n[exit 0]" and "installs software" in asked[-1]
    assert sh.handler({"command": "osascript -e 'beep'"}).startswith("not run: use the applescript tool")
    assert env.ran == ["open -a Spotify", "pip install foo"]


def test_no_confirm_asks_nothing():
    env = mac(confirm=None)
    assert presets.shell_tool(env).handler({"command": "rm x"}) == "ok\n[exit 0]" and env.ran == ["rm x"]


def test_the_app_in_front_comes_back_after_a_question():
    env = mac(confirm=lambda q: False)
    calls = []
    env._front = lambda: calls.append("front") or "com.spotify.client"
    env._refocus = lambda b: calls.append(("refocus", b))
    presets.shell_tool(env).handler({"command": "killall Music"})
    assert calls == ["front", ("refocus", "com.spotify.client")]


def test_applescript_is_limited_to_its_apps_and_guarded():
    asked = []
    env = mac(confirm=lambda q: asked.append(q) or False)
    tool = presets.applescript_tool(env)
    out = tool.handler({"script": 'tell application "Messages" to get every chat'})
    assert out.startswith("not run: AppleScript here is only for") and env.ran == []
    out = tool.handler({"script": 'tell application "Notes" to get name of every note'})
    assert out.startswith("not run: this script reads through every item")
    out = tool.handler({"script": 'tell application "Reminders" to delete reminder "milk"'})
    assert out.startswith("not run:") and "deletes something" in asked[0] and env.ran == []
    assert tool.handler({"script": 'tell application "Reminders" to make new reminder with properties {name:"x"}'}) \
        .startswith("ran (exit 0)")
    assert any("osacompile" in c for c in env.ran)          # the checked tool: compiled first
    escaped = 'tell application \\"Reminders\\"\\nmake new reminder\\nend tell'
    assert presets._unescape(escaped) == 'tell application "Reminders"\nmake new reminder\nend tell'


def test_a_preset_run_stops_its_plan_at_a_denied_command(server):  # noqa: F811
    def call(steps):
        return {"type": "function", "function": {"name": "plan", "arguments": json.dumps({"steps": steps})}}
    server["script"] = [
        {"tool_calls": [call([{"action": "shell", "command": "rm -rf ~/Music/old"}, {"action": "done"}])]},
        {"tool_calls": [call([{"action": "shell", "command": "ls ~/Music"}, {"action": "done"}])]},
    ]
    asked = []
    env = mac(confirm=lambda q: asked.append(q) or False)
    res = presets.mac(env, "m", server["url"]).run("clear old music", env, max_steps=4)
    assert len(asked) == 1 and env.ran == ["ls ~/Music"]
    assert res.done and res.steps == 2                      # the done after the denied command did not count
    body = server["bodies"][0]
    assert [t["function"]["name"] for t in body["tools"]] == ["plan"]
    assert body["messages"][0]["content"] == presets.plan_system().format(w=100, h=50, coords=
                                                                          "Coordinates are [x, y] on a 0-1000 scale "
                                                                          "of the screenshot's width and height "
                                                                          "(origin top-left).", batch=12)
    told = [p["text"] for p in server["bodies"][1]["messages"][-1]["content"] if p["type"] == "text"]
    assert any("said no to this command" in t for t in told) and any("stopped" in t for t in told)


def test_no_shell_no_preset():
    import legato
    with pytest.raises(TypeError):
        presets.shell_tool(legato.attach(screenshot=lambda: b"", execute=lambda a: None))
