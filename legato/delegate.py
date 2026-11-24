"""Planner and executor: a strong model plans and checks, a fast model acts.

    planner = Planner("openai/gpt-6.1-terra", "https://openrouter.ai/api/v1", key)
    harness = Harness("qwen/qwen3.8-27b", base_url, key, image_max=2, planner=planner)
    harness.run(task, env)

The planner sees the task and the screen and writes short steps, each with what the screen shows when it is done. The
harness's own model (the executor) runs one step at a time with a short context: the step, what to expect, the next
step, and its last few screenshots (`image_max`). It calls `step_done` when the screen shows the expected result and
`stuck` when it cannot go on. The planner is called again only:
- when the executor calls `stuck` (or `fail`);
- when one step takes more than `max_turns_per_step` executor turns;
- every `every` executor turns;
- when the plan runs out, to judge whether the task is done.
Only the planner ends the task (done or fail).
"""
from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from legato.rl.policy import PolicyClient, PolicyConfig, PolicyError
from legato.safety import Denied, Stopped, Unavailable
from legato.tools import Tool, ToolCall

if TYPE_CHECKING:
    from legato.env import Env
    from legato.harness import Harness, RunResult

PLANNER_SYSTEM = """You plan computer tasks for a fast assistant who operates the desktop. You see the task and the \
screen. Write a short plan: steps the assistant can do one at a time, each a concrete instruction (what to click, \
type or press, naming what is on screen) and what the screen shows once it is done. Prefer reliable keyboard \
shortcuts. Save files in their current format.
Call the `plan` tool:
- status "continue" with the steps still to do (at most {max_steps}), when the task is not finished;
- status "done" when the screen shows the task is complete and saved;
- status "fail" when the task cannot be done on this computer.
When you are called again, the plan so far and what happened are given: keep what still holds, fix what went wrong, \
and judge done only from the screen."""

PLAN_TOOL = {"type": "function", "function": {
    "name": "plan", "description": "The plan for the rest of the task, or the task's end.",
    "parameters": {"type": "object", "properties": {
        "status": {"type": "string", "enum": ["continue", "done", "fail"]},
        "steps": {"type": "array", "items": {"type": "object", "properties": {
            "do": {"type": "string", "description": "one concrete step"},
            "expect": {"type": "string", "description": "what the screen shows when the step is done"}},
            "required": ["do", "expect"]}},
        "note": {"type": "string", "description": "one line: why (optional)"}},
        "required": ["status"]}}}

EXECUTOR_TASK = """Overall task (for context; do only your step): {task}
Your step now: {do}
When it is done the screen shows: {expect}
{next}Call `step_done` as soon as the screen shows that. Call `stuck` with the reason if the step cannot be done as \
written (an unexpected window, a missing item, an error). Do not do later steps."""


@dataclass
class Planner:
    """The planning model and when it is called. Any OpenAI-compatible endpoint."""
    model: str
    base_url: str
    api_key: str = ""
    every: int = 8                       # executor turns between check-ins (0: only on the other triggers)
    max_turns_per_step: int = 4          # executor turns on one step before the planner looks
    max_steps: int = 10                  # steps a plan may carry
    max_tokens: int = 8192
    temperature: float | None = 0.0
    extra_body: dict | None = None       # e.g. {"reasoning": {"effort": "low"}}
    timeout_s: float = 240.0
    retries: int = 2
    client: Any = None                   # anything with .chat(messages, ...) -> record; built from the fields if None
    style: str = "steps"                 # "steps": the executor does each step (this module); "point": legato/point.py
    notes: bool = True                   # point: the planner keeps notes that are given back to it at every call
    #                                      (on by default: it does not slow short tasks, legato-research EXP-0098)
    shell_name: str = ""                 # point: what the harness's shell tool runs, as the planner is told ("":
    #                                      what the tool says it runs: bash, or PowerShell on Windows)

    def __post_init__(self) -> None:
        if self.client is None:
            self.client = PolicyClient(PolicyConfig(base_url=self.base_url, model=self.model, api_key=self.api_key,
                                                    version="planner", timeout_s=self.timeout_s, retries=self.retries,
                                                    logprobs=False, token_ids=False,
                                                    extra_body=dict(self.extra_body or {})))

    def ask(self, task: str, image_url: str, state: str) -> tuple[str, list[dict], dict]:
        """(status, steps, record) for the task, the current screen, and what has happened so far."""
        msgs = [{"role": "system", "content": PLANNER_SYSTEM.format(max_steps=self.max_steps)},
                {"role": "user", "content": [{"type": "text", "text": f"Task: {task}\n\n{state}"},
                                             {"type": "image_url", "image_url": {"url": image_url}}]}]
        rec = self.client.chat(msgs, max_tokens=self.max_tokens, temperature=self.temperature, tools=[PLAN_TOOL],
                               tool_choice={"type": "function", "function": {"name": "plan"}})
        args = _plan_args(rec)
        status = str(args.get("status") or "").lower()
        steps = [{"do": str(s.get("do", "")).strip(), "expect": str(s.get("expect", "")).strip()}
                 for s in args.get("steps") or [] if isinstance(s, dict) and str(s.get("do", "")).strip()]
        if status not in ("continue", "done", "fail"):
            status = "continue" if steps else "fail"
        if status == "continue" and not steps:
            status = "fail"
        return status, steps[: self.max_steps], rec


def _plan_args(rec: dict) -> dict:
    for tc in rec.get("tool_calls") or []:
        fn = tc.get("function") or {}
        a = fn.get("arguments")
        if isinstance(a, str):
            try:
                a = json.loads(a)
            except ValueError:
                continue
        if isinstance(a, dict):
            return a
    text = str(rec.get("content") or "")
    i, j = text.find("{"), text.rfind("}")
    if i >= 0 and j > i:
        try:
            a = json.loads(text[i: j + 1])
            return a if isinstance(a, dict) else {}
        except ValueError:
            pass
    return {}


@dataclass
class _State:
    steps: list
    i: int = 0
    marks: list = field(default_factory=list)     # (step, "done" | "stuck: why" | "checked after N turns")
    recent: list = field(default_factory=list)    # the executor's latest actions, described


def _describe(st: _State, reason: str) -> str:
    lines = ["Plan so far:"]
    for k, s in enumerate(st.steps):
        mark = next((m for j, m in st.marks if j == k), None)
        cur = " <- current" if k == st.i else ""
        lines.append(f"{k + 1}. {s['do']} (expect: {s['expect']})" + (f" [{mark}]" if mark else "") + cur)
    if st.recent:
        lines.append("The assistant's latest actions: " + "; ".join(st.recent[-8:]))
    lines.append(f"Why you are called now: {reason}")
    return "\n".join(lines)


def run_delegated(h: "Harness", task: str, env: "Env", max_steps: int = 30,
                  on_step: Callable | None = None) -> "RunResult":
    """Harness.run with a planner (see the module docstring). max_steps counts executor turns."""
    from legato.harness import RunResult
    p: Planner = h.planner
    res, t0 = RunResult(task), time.monotonic()
    res.planner = {"calls": 0, "by": {}, "s": 0.0, "prompt": 0, "completion": 0, "cost_usd": 0.0}
    res.trace = []                          # what happened, call by call: for reading a run afterwards
    added = []
    for t in (Tool("step_done", "the current step's expected result is on the screen", {"type": "object",
                   "properties": {}}, lambda a: "ok"),
              Tool("stuck", "the current step cannot be done as written", {"type": "object", "properties": {
                   "reason": {"type": "string"}}, "required": ["reason"]}, lambda a: "ok")):
        if t.name not in h.tools:
            h.tools[t.name] = t
            added.append(t.name)
    obs = env.observe()
    png = obs.screenshot

    def call_planner(reason_key: str, state: str) -> tuple[str, list]:
        t = time.monotonic()
        status, steps, rec = p.ask(task, h._image_url(base64.b64encode(png).decode()), state)
        u = rec.get("usage") or {}
        pl = res.planner
        pl["calls"] += 1
        pl["by"][reason_key] = pl["by"].get(reason_key, 0) + 1
        pl["s"] += time.monotonic() - t
        pl["prompt"] += u.get("prompt") or 0
        pl["completion"] += u.get("completion") or 0
        pl["cost_usd"] += u.get("cost") or 0.0
        res.usage["cost_usd"] += u.get("cost") or 0.0
        res.timings.append({"planner_s": time.monotonic() - t, "planner": reason_key})
        res.trace.append({"planner": reason_key, "status": status, "s": round(time.monotonic() - t, 2),
                          "steps": [[x["do"][:140], x["expect"][:100]] for x in steps]})
        return status, steps

    try:
        try:
            status, steps = call_planner("start", "No plan yet: write the first one.")
        except PolicyError as e:
            res.error = f"planner: {str(e)[:280]}"
            return res
        st = _State(steps)
        turns = turns_in_step = since_check = 0
        ep = None
        while status == "continue" and not res.stopped:
            if st.i >= len(st.steps):           # the plan ran out: the planner judges the end, or plans more
                reason_key, reason = "end", "Every step is marked done. Is the task complete and saved?"
            elif turns >= max_steps:
                res.error = f"executor turn limit ({max_steps})"
                break
            else:
                reason_key = reason = None
                if ep is None:
                    s = st.steps[st.i]
                    nxt = (f"Next step, for context only: {st.steps[st.i + 1]['do']}\n"
                           if st.i + 1 < len(st.steps) else "")
                    ep = h.begin(EXECUTOR_TASK.format(task=task, do=s["do"], expect=s["expect"], next=nxt), png)
                    turns_in_step = 0
                try:
                    plan = h.decide(ep)
                except PolicyError as e:
                    res.error = str(e)[:300]
                    break
                u = plan.rec.get("usage") or {}
                for k in ("prompt", "completion", "cached"):
                    res.usage[k] += u.get(k) or 0
                res.usage["cost_usd"] += u.get("cost") or 0.0
                timing = {"model_s": plan.rec["s"], "act_s": 0.0, "settle_s": 0.0, "obs_s": 0.0}
                turns += 1
                turns_in_step += 1
                since_check += 1
                pngs, results, ran, note, step_done, stuck = [], [], [], None, False, None
                if plan.done:                    # the executor's done/fail mean this step, not the task
                    step_done = True
                elif plan.failed:
                    stuck = "the assistant called fail"
                for a in plan.actions:
                    if isinstance(a, ToolCall):
                        if a.name == "step_done":
                            step_done = True
                            break
                        if a.name == "stuck":
                            stuck = str(a.args.get("reason") or "no reason given")[:300]
                            break
                        results.append(f"{a.name} result:\n{h.call_tool(a)}")
                        ran.append(a)
                        continue
                    try:
                        obs = env.observe() if a is None else env.act(a)
                    except Stopped as e:
                        res.stopped, res.error = True, f"stopped by the person: {e}"
                        break
                    except Denied as e:
                        from legato.harness import _refused
                        note = _refused(e)
                        res.actions.append(f"{'not possible' if isinstance(e, Unavailable) else 'not allowed'}: "
                                           f"{a.describe()}")
                        obs = env.observe()
                        pngs.append(obs.screenshot)
                        break
                    ran.append(a)
                    pngs.append(obs.screenshot)
                    for k in ("act_s", "settle_s", "obs_s"):
                        timing[k] += env.last.get(k, 0.0)
                if ran and not pngs:
                    obs = env.observe()
                    pngs.append(obs.screenshot)
                if pngs:
                    png = pngs[-1]
                res.steps += 1
                desc = [a.describe() if a else "screenshot" for a in ran]
                res.trace.append({"step": st.i, "turn": turns_in_step, "acts": [str(x)[:80] for x in desc],
                                  "done": step_done, "stuck": stuck, "s": round(plan.rec["s"], 2),
                                  "say": " | ".join(plan.reply)[:160]})
                res.actions += desc
                st.recent += desc
                res.timings.append(timing)
                if on_step:
                    on_step(turns - 1, ran, timing)
                if res.stopped:
                    break
                if step_done:
                    st.marks.append((st.i, "done"))
                    st.i += 1
                    ep = None
                    if p.every and since_check >= p.every and st.i < len(st.steps):
                        reason_key, reason = "every", f"A check-in after {since_check} assistant turns."
                    else:
                        continue
                elif stuck:
                    st.marks.append((st.i, f"stuck: {stuck}"))
                    reason_key, reason = "stuck", f"The assistant is stuck on step {st.i + 1}: {stuck}"
                elif turns_in_step >= p.max_turns_per_step:
                    st.marks.append((st.i, f"not done after {turns_in_step} turns"))
                    reason_key, reason = "slow", f"Step {st.i + 1} is not done after {turns_in_step} assistant turns."
                elif p.every and since_check >= p.every:
                    reason_key, reason = "every", f"A check-in after {since_check} assistant turns."
                else:
                    h.feed(ep, plan, pngs or [png], results, note)
                    continue
            try:
                status, steps = call_planner(reason_key, _describe(st, reason))
            except PolicyError as e:
                res.error = f"planner: {str(e)[:280]}"
                break
            since_check = 0
            st = _State(steps, marks=[], recent=st.recent[-8:])
            ep = None
        if status == "done":
            res.done = True
            res.actions.append("done")
        elif status == "fail":
            res.failed = True
            res.actions.append("fail")
    finally:
        for name in added:
            h.tools.pop(name, None)
        res.wall_s = time.monotonic() - t0
    return res
