"""legato.delegate: a planner model plans and checks, the harness's model executes; scripted fake models, a fake desktop."""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import legato  # noqa: E402
from legato.delegate import Planner  # noqa: E402
from legato.rl.fake import tiny_png  # noqa: E402


def rec(calls: list | None = None, content: str = "") -> dict:
    return {"content": content, "tool_calls": calls, "finish_reason": "tool_calls" if calls else "stop",
            "usage": {"prompt": 100, "completion": 10, "cached": 0, "cost": 0.001}, "s": 0.01}


def call(name: str, args: dict) -> dict:
    return {"type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def act(**args) -> dict:
    return call("computer", args)


def plan(status: str, *steps: tuple) -> dict:
    return rec([call("plan", {"status": status, "steps": [{"do": d, "expect": e} for d, e in steps]})])


class Scripted:
    """A model that answers from a script and keeps what it was sent."""

    def __init__(self, script: list):
        self.script, self.sent = list(script), []

    def chat(self, messages, **kw) -> dict:
        self.sent.append({"messages": messages, **kw})
        assert self.script, "the model was called more often than the test expects"
        return self.script.pop(0)


def desktop(log: list):
    png = tiny_png(1, w=8, h=8)
    png = png[:16] + struct.pack(">II", 1000, 500) + png[24:]
    return legato.attach(screenshot=lambda: png, execute=lambda a: log.append(a))


def harness(planner_script: list, executor_script: list, **planner_kw):
    p = Planner("big", "http://x/v1", client=Scripted(planner_script), **planner_kw)
    h = legato.Harness("small", "http://x/v1", image_max=2, planner=p)
    h.client = Scripted(executor_script)
    return h, p


def test_plan_steps_then_planner_judges_done():
    h, p = harness([plan("continue", ("click OK", "the dialog is gone"), ("press ctrl+s", "the title has no *")),
                    plan("done")],
                   [rec([act(action="left_click", coordinate=[500, 500])]), rec([call("step_done", {})]),
                    rec([act(action="key", keys="ctrl+s"), call("step_done", {})])])
    log = []
    res = h.run("save it", desktop(log))
    assert res.done and not res.failed and [a.type for a in log] == ["click", "key"]
    assert res.planner["calls"] == 2 and res.planner["by"] == {"start": 1, "end": 1}
    assert res.steps == 3 and res.actions[-1] == "done"
    assert [("planner" in e) for e in res.trace] == [True, False, False, False, True]
    assert res.trace[0]["steps"][0] == ["click OK", "the dialog is gone"] and res.trace[2]["done"] is True
    first = h.client.sent[0]["messages"][1]["content"][0]["text"]
    assert "Your step now: click OK" in first and "Next step, for context only: press ctrl+s" in first
    assert {t["function"]["name"] for t in h.client.sent[0]["tools"]} >= {"computer", "step_done", "stuck"}
    assert "step_done" not in h.tools and "stuck" not in h.tools        # the executor's tools are removed after the run


def test_stuck_calls_the_planner_with_the_reason():
    h, p = harness([plan("continue", ("open File", "the File menu is open")),
                    plan("continue", ("close the popup", "no popup")), plan("done")],
                   [rec([call("stuck", {"reason": "a popup covers the menu"})]),
                    rec([act(action="key", keys="Escape"), call("step_done", {})])])
    res = h.run("t", desktop([]))
    assert res.done and res.planner["by"] == {"start": 1, "stuck": 1, "end": 1}
    state = p.client.sent[1]["messages"][1]["content"][0]["text"]
    assert "stuck: a popup covers the menu" in state and "Why you are called now" in state


def test_a_slow_step_calls_the_planner():
    h, p = harness([plan("continue", ("find the cell", "the cell is selected")), plan("fail")],
                   [rec([act(action="left_click", coordinate=[10, 10])]), rec([act(action="left_click", coordinate=[20, 20])])],
                   max_turns_per_step=2)
    res = h.run("t", desktop([]))
    assert res.failed and not res.done and res.planner["by"] == {"start": 1, "slow": 1}
    state = p.client.sent[1]["messages"][1]["content"][0]["text"]
    assert "not reported done after 2 turns" in state and "Actions already taken on step 1: click (10, 5); click (20, 10)" in state


def test_check_in_every_n_turns():
    steps = [(f"step {i}", f"result {i}") for i in range(4)]
    h, p = harness([plan("continue", *steps), plan("continue", *steps[2:]), plan("done")],
                   [rec([call("step_done", {})])] * 4, every=2)
    res = h.run("t", desktop([]))
    assert res.done and res.planner["by"] == {"start": 1, "every": 1, "end": 1}


def test_the_executors_done_ends_only_its_step():
    h, p = harness([plan("continue", ("type 42", "42 is in the cell")), plan("fail")],
                   [rec([act(action="type", text="42"), act(action="done")])])
    log = []
    res = h.run("t", desktop(log))
    assert [a.type for a in log] == ["type"] and res.failed and res.planner["by"] == {"start": 1, "end": 1}


def test_the_executor_sees_one_step_and_few_screenshots():
    h, p = harness([plan("continue", ("a", "A"), ("b", "B")), plan("done")],
                   [rec([act(action="left_click", coordinate=[1, 1])])] * 3 + [rec([call("step_done", {})]),
                    rec([call("step_done", {})])], max_turns_per_step=9)
    res = h.run("t", desktop([]))
    assert res.done
    fourth = h.client.sent[3]["messages"]
    imgs = sum(1 for m in fourth if m["role"] == "user" for c in m["content"] if c["type"] == "image_url")
    assert imgs <= 2                                              # image_max=2
    fifth = h.client.sent[4]["messages"]
    assert "Your step now: b" in fifth[1]["content"][0]["text"] and len(fifth) == 2   # a fresh, short context per step


def test_a_plan_with_no_steps_fails_and_the_turn_limit_holds():
    h, _ = harness([rec(content="I am not sure")], [])
    assert h.run("t", desktop([])).failed
    h, _ = harness([plan("continue", ("a", "A"))], [rec([act(action="left_click", coordinate=[1, 1])])] * 3,
                   max_turns_per_step=99, every=0)
    res = h.run("t", desktop([]), max_steps=3)
    assert not res.done and not res.failed and "turn limit" in res.error and res.steps == 3


def test_the_planner_is_told_what_was_completed_before_a_replan():
    h, p = harness([plan("continue", ("align slide 3", "right-aligned"), ("align slide 4", "centred")),
                    plan("continue", ("finish slide 4", "centred")), plan("done")],
                   [rec([call("step_done", {})]), rec([call("stuck", {"reason": "the text is not selected"})]),
                    rec([call("step_done", {})])])
    res = h.run("t", desktop([]))
    assert res.done
    replan = p.client.sent[1]["messages"][1]["content"][0]["text"]
    assert "Completed earlier in this task" in replan and "- align slide 3" in replan
    end = p.client.sent[2]["messages"][1]["content"][0]["text"]
    assert "- align slide 3" in end and "- finish slide 4" in end       # the memory survives the re-plan
