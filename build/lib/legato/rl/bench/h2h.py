"""Head-to-head report: several harnesses (arms) on the same tasks, compared with one reference arm.

    python -m legato.rl.bench.h2h --tasks tasks.json --arm reference=runs/ref --arm legato=runs/legato \
        --ref reference --out report/

Per arm: success, agent time (first screenshot to the model's last turn: model calls, actions, waits for the screen,
screenshots; not the desktop reset or the grading), model calls per task, prompt and output tokens per call, the
cached share of prompt tokens, and billed cost where the API reports it. Against the reference arm: the ratio of
summed per-task mean agent times with a 95% task-bootstrap CI, the same ratio over tasks both arms solved in every
attempt, and the success difference.
"""
from __future__ import annotations

import argparse
import json
import random
import statistics as st
from pathlib import Path

from legato.rl.trajectory import read_rollouts

FINISHED = ("ok", "truncated")


def agent_s(r: dict) -> float:
    return sum((s.get("predict_s") or 0.0) + sum(a.get("step_s") or 0.0 for a in s.get("actions") or [])
               for s in r.get("steps") or [])


def rollout_stats(r: dict) -> dict:
    m = [s.get("model") or {} for s in r.get("steps") or []]
    tot = lambda k: sum((x.get(k) or 0) for x in m)  # noqa: E731
    acts = sum(len(s.get("actions") or []) for s in r.get("steps") or [])
    t = r.get("timing") or {}
    return {"passed": (r.get("reward") or 0) >= 1, "score": r.get("reward") or 0.0, "agent_s": agent_s(r),
            "total_s": t.get("total_s", 0.0), "calls": len(m), "actions": acts, "model_s": tot("s"),
            "wait_s": t.get("wait_s", 0.0), "prompt": tot("prompt"), "completion": tot("completion"),
            "cached": tot("cached"), "cost": tot("cost"), "cost_known": any(x.get("cost") is not None for x in m)}


def summarize_arm(rs: list[dict]) -> dict:
    xs = [rollout_stats(r) for r in rs]
    if not xs:
        return {"rollouts": 0}
    calls = sum(x["calls"] for x in xs) or 1
    mean = lambda k: st.mean(x[k] for x in xs)  # noqa: E731
    return {"rollouts": len(xs), "passed": sum(x["passed"] for x in xs), "success": mean("passed"),
            "mean_score": mean("score"), "agent_s": mean("agent_s"), "median_agent_s": st.median(x["agent_s"] for x in xs),
            "total_s": mean("total_s"), "calls": mean("calls"), "actions": mean("actions"),
            "actions_per_call": sum(x["actions"] for x in xs) / calls, "model_s_per_call": sum(x["model_s"] for x in xs) / calls,
            "model_share": sum(x["model_s"] for x in xs) / (sum(x["agent_s"] for x in xs) or 1),
            "wait_s_per_action": sum(x["wait_s"] for x in xs) / (sum(x["actions"] for x in xs) or 1),
            "prompt_per_call": sum(x["prompt"] for x in xs) / calls, "completion_per_call": sum(x["completion"] for x in xs) / calls,
            "cached_share": sum(x["cached"] for x in xs) / (sum(x["prompt"] for x in xs) or 1),
            "cost_per_task": mean("cost") if all(x["cost_known"] for x in xs) else None}


def _ratio(arm: dict, ref: dict, tids: list) -> float | None:
    den = sum(ref[t] for t in tids)
    return sum(arm[t] for t in tids) / den if tids and den else None


def bootstrap(arm: dict, ref: dict, tids: list, n: int = 4000, seed: int = 7) -> tuple[float | None, float | None]:
    if len(tids) < 3:
        return None, None
    rnd, vals = random.Random(seed), []
    for _ in range(n):
        s = [rnd.choice(tids) for _ in tids]
        v = _ratio(arm, ref, s)
        if v is not None:
            vals.append(v)
    vals.sort()
    return vals[int(0.025 * len(vals))], vals[int(0.975 * len(vals)) - 1]


def compare(arms: dict[str, str | Path], tasks: list[dict], ref: str) -> dict:
    runs = {k: [r for r in read_rollouts(v) if r.get("status") in FINISHED] for k, v in arms.items()}
    errors = {k: sum(1 for r in read_rollouts(v) if r.get("status") == "error") for k, v in arms.items()}
    tids = [t["task_id"] for t in tasks]
    per = {k: {t: [rollout_stats(r) for r in rs if r["task_id"] == t] for t in tids} for k, rs in runs.items()}
    mean_t = {k: {t: st.mean(x["agent_s"] for x in v) for t, v in d.items() if v} for k, d in per.items()}
    solved = {k: {t for t, v in d.items() if v and all(x["passed"] for x in v)} for k, d in per.items()}
    out = {"ref": ref, "arms": {}, "per_task": {t: {k: {"n": len(per[k][t]), "passed": sum(x["passed"] for x in per[k][t]),
                                                         "agent_s": mean_t[k].get(t)} for k in arms} for t in tids}}
    for k in arms:
        s = summarize_arm(runs[k])
        s["errors"] = errors[k]
        if k != ref:
            common = [t for t in tids if t in mean_t[k] and t in mean_t[ref]]
            both = [t for t in common if t in solved[k] and t in solved[ref]]
            lo, hi = bootstrap(mean_t[k], mean_t[ref], common)
            s["vs_ref"] = {"time_ratio": _ratio(mean_t[k], mean_t[ref], common), "ci": [lo, hi], "tasks": len(common),
                           "both_solved_ratio": _ratio(mean_t[k], mean_t[ref], both), "both_solved_tasks": len(both),
                           "success_diff": (s.get("success") or 0) - (summarize_arm(runs[ref]).get("success") or 0)}
        out["arms"][k] = s
    return out


def report_md(res: dict, meta: dict) -> str:
    f = lambda v, n=1: "-" if v is None else f"{v:.{n}f}"  # noqa: E731
    L = [f"# {meta.get('title', 'Head-to-head')}", "", meta.get("intro", ""), ""]
    for k, d in (meta.get("arms") or {}).items():
        L.append(f"- **{k}**: {d}")
    L += ["", "| arm | rollouts | passed | success | agent s (mean) | median | calls/task | actions/call | model s/call | "
              "out tok/call | in tok/call | cached | wait s/action | $/task | errors |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for k, x in res["arms"].items():
        if not x.get("rollouts"):
            L.append(f"| {k} | 0 | | | | | | | | | | | | | {x.get('errors', 0)} |")
            continue
        L.append(f"| {k} | {x['rollouts']} | {x['passed']} | {f(100 * x['success'], 0)}% | {f(x['agent_s'])} | "
                 f"{f(x['median_agent_s'])} | {f(x['calls'])} | {f(x['actions_per_call'], 2)} | {f(x['model_s_per_call'], 2)} | "
                 f"{f(x['completion_per_call'], 0)} | {f(x['prompt_per_call'], 0)} | {f(100 * x['cached_share'], 0)}% | "
                 f"{f(x['wait_s_per_action'], 2)} | {f(x['cost_per_task'], 3) if x['cost_per_task'] is not None else '-'} | "
                 f"{x['errors']} |")
    L += ["", f"Against **{res['ref']}** (ratio of summed per-task mean agent time; below 1 is faster):", ""]
    for k, x in res["arms"].items():
        v = x.get("vs_ref")
        if not v:
            continue
        r, (lo, hi) = v["time_ratio"], v["ci"]
        L.append(f"- **{k}**: {f(r, 3)} [{f(lo, 3)}, {f(hi, 3)}] over {v['tasks']} tasks"
                 + (f" ({1 / r:.2f}x faster)" if r else "")
                 + f"; tasks both solved every time: {f(v['both_solved_ratio'], 3)} over {v['both_solved_tasks']}; "
                 f"success {'+' if v['success_diff'] >= 0 else ''}{f(100 * v['success_diff'], 0)} pts")
    names = list(res["arms"])
    L += ["", "| task | " + " | ".join(f"{k} passed | {k} s" for k in names) + " |",
          "|---|" + "---|---|" * len(names)]
    for t, row in res["per_task"].items():
        L.append(f"| {t} | " + " | ".join(f"{row[k]['passed']}/{row[k]['n']} | {f(row[k]['agent_s'])}" for k in names) + " |")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m legato.rl.bench.h2h")
    ap.add_argument("--tasks", required=True)
    ap.add_argument("--arm", action="append", required=True, help="name=rollout_dir")
    ap.add_argument("--ref", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--meta", default="", help="JSON: title, intro, arms (name -> description)")
    a = ap.parse_args(argv)
    arms = dict(x.split("=", 1) for x in a.arm)
    tasks = json.loads(Path(a.tasks).read_text())
    res = compare(arms, tasks, a.ref)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(res, indent=1))
    md = report_md(res, json.loads(a.meta or "{}"))
    (out / "RESULTS.md").write_text(md)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
