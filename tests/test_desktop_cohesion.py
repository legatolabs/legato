"""Public harness and Mac app contracts over changing desktop frames."""
import base64
import sys

import pytest

from legato import plan as plans
from legato.agent import agent_for, desktop_kind
from legato.background import BackgroundEnv
from legato.env import Env, png_size
from legato.harness import Harness
from test_app_engine import FakeLink, Run, png
from test_harness import server, tc  # noqa: F401
from test_plan import call, shell
from test_point import act, at, harness


def plan(*steps):
    return {"tool_calls": [call("plan", {"steps": list(steps)})]}


def last_screen(request):
    parts = request["messages"][-1]["content"]
    image = next(p["image_url"]["url"] for p in reversed(parts) if p["type"] == "image_url")
    return base64.b64decode(image.split(",", 1)[1])


@pytest.mark.parametrize("entry", ["sdk", "mac_app"])
def test_plan_feeds_and_records_the_last_screen_across_empty_turns(entry, server, monkeypatch, tmp_path):
    monkeypatch.setattr(plans, "warm_up", lambda img: None)
    start, edited = png(), png((18, 124, 63))
    state = [start]

    def type_text(action):
        assert action.type == "type"
        state[0] = edited

    env = Env(lambda: state[0], execute=type_text, fixed_wait_s=0)
    h = Harness("m", server["url"], plan=True, image_format="png")
    server["script"] = [plan({"action": "type", "text": "one"}), {"content": "Thinking."}, plan({"action": "done"})]
    if entry == "sdk":
        assert h.run("Enter one", env, record_dir=tmp_path).done
    else:
        rec = Run(FakeLink(), {"id": "case", "task": "Enter one", "runs_dir": str(tmp_path)},
                  harness=h, env_factory=lambda run: env)()
        assert rec["status"] == "done"
    assert [last_screen(body) for body in server["bodies"]] == [start, edited, edited]
    if entry == "sdk":
        assert (tmp_path / "turn-002.png").read_bytes() == edited
        assert (tmp_path / "turn-003.png").read_bytes() == edited


@pytest.mark.parametrize("preceding_tool", [False, True])
def test_wait_for_returns_the_exact_screen_which_matched(server, monkeypatch, preceding_tool):
    shots = [png((n, 0, 0)) for n in (1, 2, 3)]
    grabs = []

    def capture():
        frame = shots[len(grabs)]
        grabs.append(frame)
        return frame

    monkeypatch.setattr(plans, "warm_up", lambda img: None)
    monkeypatch.setattr(plans, "find_text", lambda img, text, near=None: (10, 10) if img == shots[1] else None)
    steps = ([{"action": "shell", "command": "echo ready"}] if preceding_tool else []) + [
        {"action": "wait_for", "text": "Ready", "timeout_s": 1}]
    server["script"] = [plan(*steps), plan({"action": "done"})]
    h = Harness("m", server["url"], plan=True, image_format="png", tools=[shell([])])
    result = h.run("Wait for the update", Env(capture, execute=lambda _: None))
    assert result.done
    assert last_screen(server["bodies"][1]) == shots[1]
    assert len(grabs) == 2


def test_background_subclasses_keep_the_window_only_recipe(monkeypatch):
    class CustomWindow(BackgroundEnv):
        pass

    class BackgroundLookalike(Env):
        pass

    BackgroundLookalike.__name__ = "BackgroundEnv"
    env = object.__new__(CustomWindow)
    monkeypatch.setattr(sys, "platform", "darwin")
    options = dict(model="m", base_url="http://localhost/v1", api_key="test")
    h = agent_for(env, **options)
    assert not h.plan_mode and not h.tools and h.image_max == 4
    assert agent_for(env, image_max=2, **options).image_max == 2
    assert desktop_kind(object.__new__(BackgroundLookalike)) == "other"


def resizing_window():
    size, clicks = [(100, 60)], []

    def execute(action):
        if action.type == "key":
            size[0] = (200, 80)
        elif action.type == "click":
            clicks.append((action.x, action.y))

    return Env(lambda: png(size=size[0]), execute=execute, fixed_wait_s=0), clicks


@pytest.mark.parametrize("coords,model_size,point", [
    ("norm1000", None, [750, 500]), ("pixels", None, [150, 40]), ("pixels", (80, 40), [60, 20])])
def test_click_after_a_window_resizes_uses_the_new_image_geometry(coords, model_size, point, server):
    env, clicks = resizing_window()
    server["script"] = [
        {"tool_calls": [tc({"action": "key", "keys": "Return"})]},
        {"tool_calls": [tc({"action": "left_click", "coordinate": point})]},
        {"tool_calls": [tc({"action": "done"})]}]
    h = Harness("m", server["url"], coords=coords, model_size=model_size, image_format="png")
    assert h.run("Open the detail view, then click its item", env).done
    assert clicks == [(150, 40)]
    assert png_size(last_screen(server["bodies"][1])) == (model_size or (200, 80))


@pytest.mark.parametrize("planner_aims", [True, False])
def test_point_planner_and_pointer_use_latest_frame_geometry(planner_aims):
    env, clicks = resizing_window()
    target = {"click": "the item"}
    if planner_aims:
        target["at"] = [150, 40]
    h, _ = harness([act("continue", {"key": "Return"}), act("continue", target), act("done")],
                   [at(750, 500)])
    assert h.run("Open the detail view, then click its item", env).done
    assert clicks == [(150, 40)]
    assert len(h.client.sent) == (0 if planner_aims else 1)
