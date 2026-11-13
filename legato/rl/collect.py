"""Collect graded rollouts on desktops: the scheduler behind `python -m legato.rl collect`.

    from legato.rl import collect, PolicyConfig
    summary = collect(tasks, PolicyConfig(base_url, model, version="step-41"), out="runs/step-41",
                      desktops=16, group=8, oversample=0.25, max_inflight=32)

Each task gets `group` attempts (GRPO-style groups). With `oversample`, extra attempts start and the group closes
as soon as `group` of them finish; attempts still running are cancelled at their next step, so a few slow desktops
or slow model calls do not hold up the batch. One worker process per desktop; each keeps its desktop up between
rollouts (fast reset) and calls the policy through legato.rl.policy (logprobs, timeouts, admission cap).
"""
from __future__ import annotations

import json
import math
import multiprocessing as mp
import os
import queue
import sys
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from legato.rl.policy import PolicyClient, PolicyConfig, PolicyError
from legato.rl.trajectory import SCHEMA, store_request

PHASE_WAIT = {"after_reset": 60.0, "before_eval": 20.0}   # OSWorld's fixed waits; the ready signal ends them early


@dataclass
class DesktopConfig:
    backend: str = "osworld"          # "osworld" (Docker/QEMU VMs on this host) or "fake" (tests, trainer dev)
    osworld_path: str = ""            # an OSWorld checkout (desktop_env, mm_agents, evaluation_examples)
    ready_signal: bool = True         # wait for the screen instead of fixed sleeps
    fast_reset: bool = True           # keep the VM up and restore task state, instead of a snapshot revert
    fixed_wait_s: float = 0.0         # per-action sleep when ready_signal is off (OSWorld scripts use 0, 3 or 5)
    screen: tuple = (1920, 1080)
    fake: dict = field(default_factory=dict)


@dataclass
class HarnessConfig:
    name: str = "qwen"                # "qwen": OSWorld's QwenAgent; "legato": legato.Harness; "reference": Anthropic's
                                      # computer-use reference loop (legato.Harness.reference); "fake": tests
    image_max: int = 20               # screenshots kept in the prompt (keep-all held the prefix cache in our OSWorld study)
    fold_size: int = 10
    options: dict = field(default_factory=dict)   # extra legato.Harness keyword arguments (max_batch, coords, ...)


# ---------------------------------------------------------------- desktops

class OSWorldDesktop:
    """OSWorld DesktopEnv with legato's ready signal and fast reset (legato.rl.osworld_env)."""

    def __init__(self, cfg: DesktopConfig):
        for p in (cfg.osworld_path,):
            if p and p not in sys.path:
                sys.path.insert(0, p)
        from legato.rl.osworld_env import LegatoDesktopEnv
        self.cfg = cfg
        self.env = LegatoDesktopEnv(provider_name="docker", path_to_vm=None, action_space="pyautogui",
                                    screen_size=tuple(cfg.screen), headless=True, os_type="Ubuntu",
                                    require_a11y_tree=False, wait_mode="ready" if cfg.ready_signal else "fixed",
                                    reset_mode="revert")
        self.env.wait_mode = "ready" if cfg.ready_signal else "fixed"
        self.task_cfg, self.last_grade = None, None

    def reset(self, task: dict) -> dict:
        self.env.reset_mode = "fast" if self.cfg.fast_reset else "revert"
        self.env.timing = {}
        cfg = self._config(task)
        t = time.perf_counter()
        self.env.reset(task_config=cfg)
        self.instruction, self.task_cfg, self.last_grade = cfg["instruction"], cfg, None
        return dict(self.env.timing, total_s=time.perf_counter() - t)

    def _config(self, task: dict) -> dict:
        if "config" in task:
            cfg = dict(task["config"])
        else:
            p = Path(self.cfg.osworld_path) / "evaluation_examples" / "examples" / task["domain"] / f"{task['task_id']}.json"
            cfg = json.loads(p.read_text())
        cfg["_domain"] = task.get("domain", cfg.get("_domain", ""))
        return cfg

    def wait_after_reset(self) -> dict:
        return self.env.wait_phase(PHASE_WAIT["after_reset"])

    def observe(self) -> dict:
        return self.env._get_obs()

    def act(self, action) -> tuple[dict, bool, dict]:
        obs, _r, done, info = self.env.step(action, self.cfg.fixed_wait_s)
        return obs, bool(done), (info or {}).pop("legato", {})

    def wait_before_eval(self) -> dict:
        return self.env.wait_phase(PHASE_WAIT["before_eval"])

    def evaluate(self) -> float:
        g = (self.task_cfg or {}).get("legato_grader")
        if not g:                                     # an OSWorld task: OSWorld's own evaluator
            return float(self.env.evaluate())
        post = (self.task_cfg.get("evaluator") or {}).get("postconfig") or []
        if post:                                      # e.g. activate the app window and save
            self.env.setup_controller.setup(post, getattr(self.env, "enable_proxy", False))
        files = {name: self.env.controller.get_file(path) for name, path in g["files"].items()}
        from legato.rl.bench.office import grade
        score, self.last_grade = grade(g, files)
        return float(score)

    def close(self) -> None:
        self.env.close()


def make_desktop(cfg: DesktopConfig):
    if cfg.backend == "fake":
        from legato.rl.fake import FakeDesktop
        return FakeDesktop(cfg)
    return OSWorldDesktop(cfg)


# ---------------------------------------------------------------- harnesses

class QwenHarness:
    """OSWorld's QwenAgent (prompting, history, action parsing) with its model call routed through PolicyClient."""

    def __init__(self, policy: PolicyClient, cfg: HarnessConfig, osworld_path: str = ""):
        if osworld_path and osworld_path not in sys.path:
            sys.path.insert(0, osworld_path)
        import mm_agents.qwen.main as QM
        from mm_agents.qwen import QwenAgent
        self.policy, self.calls = policy, []

        def call(payload, model, **kw):
            rec = policy.chat(payload["messages"], max_tokens=payload.get("max_tokens", kw.get("default_max_tokens")),
                              temperature=payload.get("temperature", kw.get("default_temperature")),
                              top_p=payload.get("top_p", kw.get("default_top_p")))
            self.calls.append((payload, rec))
            return rec["content"]
        QM.call_openai_compatible = call
        self.agent = QwenAgent(model=policy.cfg.model, base_url=policy.cfg.base_url, api_key=policy.cfg.api_key or "x",
                               image_max=cfg.image_max, fold_size=cfg.fold_size)

    def reset(self) -> None:
        self.agent.reset()

    def predict(self, instruction: str, obs: dict):
        self.calls = []
        response, actions = self.agent.predict(instruction, obs)
        return response, list(actions or []), self.calls


PYAUTOGUI_KEYS = {"return": "enter", "escape": "esc", "backspace": "backspace", "prior": "pageup", "next": "pagedown",
                  "super": "win", "control": "ctrl", "page_up": "pageup", "page_down": "pagedown"}


def _pg_key(k: str) -> str:
    k = PYAUTOGUI_KEYS.get(k.lower(), k)
    return k.lower() if len(k) > 1 or k.isalpha() else k


def to_pyautogui(a) -> str:
    """A legato Action as OSWorld pyautogui code (or OSWorld's special tokens). None is the reference loop's
    screenshot action: a no-op, so the desktop returns a fresh screenshot."""
    if a is None:
        return "pass"
    t = a.type
    if t == "click":
        return f"pyautogui.click({a.x}, {a.y})"
    if t == "double_click":
        return f"pyautogui.doubleClick({a.x}, {a.y})"
    if t == "right_click":
        return f"pyautogui.rightClick({a.x}, {a.y})"
    if t == "type":
        return f"pyautogui.write({a.text!r})"
    if t == "key":
        keys = [_pg_key(k) for k in (a.keys or "").split("+") if k]
        return f"pyautogui.press({keys[0]!r})" if len(keys) == 1 else f"pyautogui.hotkey({', '.join(map(repr, keys))})"
    if t == "scroll":
        n = int(a.amount or 3)
        at = f", x={a.x}, y={a.y}" if a.x is not None else ""
        if a.direction in ("left", "right"):
            return f"pyautogui.hscroll({n if a.direction == 'right' else -n}{at})"
        return f"pyautogui.scroll({n if a.direction == 'up' else -n}{at})"
    if t == "drag":
        return f"pyautogui.moveTo({a.x}, {a.y}); pyautogui.dragTo({a.x2}, {a.y2}, duration=0.5, button='left')"
    if t == "wait":
        return "time.sleep(1.0)"
    return {"done": "DONE", "fail": "FAIL"}.get(t, "pass")


class ComputerToolHarness:
    """legato.Harness (or its reference profile) on an OSWorld desktop. predict() returns pyautogui code; the rollout
    loop runs it and hands each resulting observation back through observed()."""

    def __init__(self, policy: PolicyClient, cfg: HarnessConfig):
        from legato.harness import Harness
        c = policy.cfg
        kw = dict(step_timeout_s=c.timeout_s, retries=c.retries, extra_body=c.extra_body, **cfg.options)
        make = Harness.reference if cfg.name == "reference" else Harness
        self.h = make(c.model, c.base_url, c.api_key, **kw)
        self.h.client.close()
        self.h.client = policy                    # the scheduler's client: admission cap, retries, timing
        self.ep = self.plan = None
        self.pngs: list = []

    def reset(self) -> None:
        self.ep = self.plan = None
        self.pngs = []

    def observed(self, obs: dict) -> None:
        self.pngs.append(obs["screenshot"])

    def predict(self, instruction: str, obs: dict):
        if self.ep is None:
            self.ep = self.h.begin(instruction, obs["screenshot"])
        else:
            self.h.feed(self.ep, self.plan, self.pngs or [obs["screenshot"]])
        self.pngs = []
        self.plan = p = self.h.decide(self.ep)
        actions = [to_pyautogui(a) for a in p.actions]
        if p.done or p.failed:
            actions.append("DONE" if p.done else "FAIL")
        payload = {"messages": p.messages, "max_tokens": self.h.max_tokens, "temperature": self.h.temperature,
                   "top_p": None}
        return "\n".join(p.reply), actions, [(payload, p.rec)]


def make_harness(policy: PolicyClient, cfg: HarnessConfig, osworld_path: str):
    if cfg.name == "fake":
        from legato.rl.fake import FakeHarness
        return FakeHarness(policy, cfg)
    if cfg.name in ("legato", "reference"):
        return ComputerToolHarness(policy, cfg)
    return QwenHarness(policy, cfg, osworld_path)


# ---------------------------------------------------------------- one rollout

def run_rollout(desk, harness, job: dict, opts: dict, out: Path, cancelled) -> dict:
    rec = {"schema": SCHEMA, **{k: job[k] for k in ("rollout_id", "task_id", "domain", "group_id", "attempt")},
           "policy": {"model": opts["model"], "version": opts["version"]}, "status": "ok", "error": None,
           "reward": None, "steps": [], "t_start": time.time()}
    t0 = time.perf_counter()
    timing = {"reset_s": 0.0, "wait_after_reset_s": 0.0, "model_s": 0.0, "act_s": 0.0, "wait_s": 0.0, "obs_s": 0.0,
              "wait_before_eval_s": 0.0, "eval_s": 0.0}
    try:
        r = desk.reset({**job["task"], "_attempt": job["attempt"]})
        timing["reset_s"] = r.get("total_s", 0.0) - r.get("base_s", 0.0) - r.get("watcher_s", 0.0)
        rec["reset"] = r
        harness.reset()
        w = desk.wait_after_reset()
        timing["wait_after_reset_s"] = w.get("wait_s", 0.0)
        obs, done, i = desk.observe(), False, 0
        while not done and i < opts["max_steps"]:
            if cancelled():
                rec["status"] = "cancelled"
                break
            if opts.get("rollout_timeout_s") and time.perf_counter() - t0 > opts["rollout_timeout_s"]:
                rec["status"] = "truncated"
                break
            tp = time.perf_counter()
            try:
                response, actions, calls = harness.predict(desk.instruction, obs)
            except PolicyError as e:
                rec["status"], rec["error"] = "error", {"kind": "policy", "message": str(e)[:400]}
                break
            step = {"i": i, "t": time.time(), "predict_s": time.perf_counter() - tp, "actions": []}
            if calls:
                payload, pc = calls[-1]
                shas, n_img = store_request(payload["messages"], out)
                step["request"] = {"messages": shas, "n_images": n_img, "max_tokens": payload.get("max_tokens"),
                                   "temperature": payload.get("temperature"), "top_p": payload.get("top_p")}
                lps = pc.get("logprobs") or []
                step["response"] = {k: pc.get(k) for k in ("content", "reasoning", "token_ids", "finish_reason")}
                step["response"]["logprobs"] = [round(x["lp"], 5) if x["lp"] is not None else None for x in lps] or None
                step["response"]["tokens"] = None if pc.get("token_ids") else ([x["t"] for x in lps] or None)
                step["model"] = {"s": sum(c[1]["s"] for c in calls), "calls": len(calls), "attempts": pc["attempts"],
                                 **pc["usage"]}
                if pc.get("tool_calls"):
                    step["response"]["tool_calls"] = pc["tool_calls"]
                timing["model_s"] += step["model"]["s"]
            for a in actions:
                ta = time.perf_counter()
                obs, done, lg = desk.act(a)
                if hasattr(harness, "observed"):
                    harness.observed(obs)
                wt = lg.get("wait") or {}
                step["actions"].append({"action": str(a)[:300], "step_s": time.perf_counter() - ta,
                                        "act_s": lg.get("act_s"), "wait_s": wt.get("wait_s"), "ready_s": wt.get("ready_s"),
                                        "obs_s": lg.get("obs_s")})
                timing["act_s"] += lg.get("act_s") or 0.0
                timing["wait_s"] += wt.get("wait_s") or 0.0
                timing["obs_s"] += lg.get("obs_s") or 0.0
                if done:
                    break
            rec["steps"].append(step)
            i += 1
        if rec["status"] in ("ok", "truncated"):
            w = desk.wait_before_eval()
            timing["wait_before_eval_s"] = w.get("wait_s", 0.0)
            te = time.perf_counter()
            rec["reward"] = desk.evaluate()
            timing["eval_s"] = time.perf_counter() - te
            if getattr(desk, "last_grade", None) is not None:
                rec["grade"] = desk.last_grade
    except Exception as e:  # noqa: BLE001  the desktop failed: an infrastructure error, retried by the scheduler
        rec["status"], rec["error"] = "error", {"kind": "env", "message": f"{type(e).__name__}: {str(e)[:300]}",
                                                "trace": traceback.format_exc()[-1200:]}
    timing["total_s"] = time.perf_counter() - t0
    rec["timing"], rec["n_steps"], rec["t_end"] = timing, len(rec["steps"]), time.time()
    return rec


# ---------------------------------------------------------------- scheduler

def plan_jobs(tasks: list[dict], group: int, oversample: float, version: str) -> list[dict]:
    n = group + int(math.ceil(group * oversample))
    jobs = []
    for gi, t in enumerate(tasks):
        tid = str(t["task_id"])
        for k in range(n):
            jobs.append({"rollout_id": f"{tid}.{version}.{k}", "task_id": tid, "domain": t.get("domain", ""),
                         "group_id": f"{tid}@{version}", "gidx": gi, "attempt": k, "task": t})
    # attempts of one task go to different desktops at the same time: round-robin by attempt, then task
    jobs.sort(key=lambda j: (j["attempt"], j["gidx"]))
    return jobs


def _worker(idx, jobs, results_path, lock, done_counts, group, sem, pcfg, dcfg, hcfg, opts, out):
    out = Path(out)
    desk = harness = None
    try:
        policy = PolicyClient(PolicyConfig(**pcfg), semaphore=sem)
        desk = make_desktop(DesktopConfig(**dcfg))
        harness = make_harness(policy, HarnessConfig(**hcfg), dcfg.get("osworld_path", ""))
        while True:
            try:
                job = jobs.get(timeout=2)
            except queue.Empty:
                return
            g = job["gidx"]
            if done_counts[g] >= group:
                rec = {"schema": SCHEMA, **{k: job[k] for k in ("rollout_id", "task_id", "domain", "group_id", "attempt")},
                       "policy": {"model": pcfg["model"], "version": pcfg["version"]}, "status": "skipped", "steps": []}
            else:
                rec = run_rollout(desk, harness, job, opts, out, cancelled=lambda: done_counts[g] >= group)
                if rec["status"] == "error" and rec["error"]["kind"] == "env" and job.get("retry", 0) < opts["env_retries"]:
                    jobs.put({**job, "retry": job.get("retry", 0) + 1})   # infrastructure failure: run it again
                    rec["status"] = "retried"
                elif rec["status"] in ("ok", "truncated"):
                    with lock:
                        if done_counts[g] >= group:
                            rec["status"] = "surplus"            # finished after its group closed
                        else:
                            done_counts[g] += 1
            rec["worker"] = idx
            with lock:
                with open(results_path, "a") as f:
                    f.write(json.dumps(rec) + "\n")
    finally:
        for x in (desk,):
            try:
                if x is not None:
                    x.close()
            except Exception:  # noqa: BLE001
                pass


def collect(tasks: list[dict], policy: PolicyConfig, out: str | Path, desktops: int = 4, group: int = 4,
            oversample: float = 0.0, max_steps: int = 15, max_inflight: int | None = None,
            rollout_timeout_s: float | None = None, env_retries: int = 1, desktop: DesktopConfig | None = None,
            harness: HarnessConfig | None = None, start_stagger_s: float = 2.0) -> dict:
    """Run every task `group` times (plus oversampled attempts) across `desktops` workers; write rollouts.jsonl,
    images/, progress.json and summary.json to `out`; return the summary."""
    from legato.rl.profile import summarize
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    desktop, harness = desktop or DesktopConfig(), harness or HarnessConfig()
    jobs = plan_jobs(tasks, group, oversample, policy.version)
    ctx = mp.get_context("fork" if sys.platform.startswith("linux") else "spawn")
    q, lock = ctx.Queue(), ctx.Lock()
    done_counts = ctx.Array("i", len(tasks), lock=False)
    sem = ctx.BoundedSemaphore(max_inflight) if max_inflight else None
    for j in jobs:
        q.put(j)
    opts = {"max_steps": max_steps, "rollout_timeout_s": rollout_timeout_s, "env_retries": env_retries,
            "model": policy.model, "version": policy.version}
    results = out / "rollouts.jsonl"
    meta = {"started": time.time(), "tasks": len(tasks), "group": group, "oversample": oversample,
            "jobs": len(jobs), "desktops": desktops, "max_steps": max_steps, "max_inflight": max_inflight,
            "policy": {k: v for k, v in asdict(policy).items() if k != "api_key"},
            "desktop": asdict(desktop), "harness": asdict(harness)}
    (out / "run.json").write_text(json.dumps(meta, indent=1, default=str))
    procs = []
    for i in range(desktops):
        p = ctx.Process(target=_worker, args=(i, q, str(results), lock, done_counts, group, sem, asdict(policy),
                                              asdict(desktop), asdict(harness), opts, str(out)))
        p.start()
        procs.append(p)
        time.sleep(start_stagger_s)
    t0 = time.time()
    while any(p.is_alive() for p in procs):
        n = sum(1 for _ in open(results)) if results.exists() else 0
        (out / "progress.json").write_text(json.dumps({"written": n, "jobs": len(jobs), "elapsed_s": round(time.time() - t0),
                                                       "groups_closed": sum(1 for c in done_counts if c >= group),
                                                       "groups": len(tasks), "alive": sum(p.is_alive() for p in procs)}))
        time.sleep(5)
    for p in procs:
        p.join()
    from legato.rl.trajectory import read_rollouts
    summary = summarize(read_rollouts(out), wall_s=time.time() - t0, group=group)
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    (out / "progress.json").write_text(json.dumps({"finished": True, "elapsed_s": round(time.time() - t0)}))
    return summary
