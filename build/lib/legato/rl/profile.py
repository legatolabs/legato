"""Where a batch of rollouts spent its time, and how the policy server behaved (the breakdown from our OSWorld study, per batch)."""
from __future__ import annotations

import statistics as st
from collections import Counter

PHASES = ("reset_s", "wait_after_reset_s", "model_s", "act_s", "wait_s", "obs_s", "wait_before_eval_s", "eval_s")


def _q(xs: list, p: float):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else None


def summarize(rollouts: list[dict], wall_s: float | None = None, group: int | None = None) -> dict:
    status = Counter(r.get("status") for r in rollouts)
    done = [r for r in rollouts if r.get("status") in ("ok", "truncated")]
    tot = sum(r["timing"]["total_s"] for r in done) or 1e-9
    calls = [s["model"] for r in done for s in r.get("steps", []) if s.get("model")]
    secs = [c["s"] for c in calls]
    prompt = sum(c.get("prompt") or 0 for c in calls)
    cached = sum(c.get("cached") or 0 for c in calls)
    steps = [s for r in done for s in r.get("steps", [])]
    groups = Counter(r["group_id"] for r in done)
    return {
        "rollouts": len(rollouts), "status": dict(status), "completed": len(done),
        "success_rate": (sum(1 for r in done if (r.get("reward") or 0) >= 1) / len(done)) if done else None,
        "mean_reward": (sum(r.get("reward") or 0 for r in done) / len(done)) if done else None,
        "rollout_s": {"mean": tot / len(done) if done else None,
                      "median": st.median(r["timing"]["total_s"] for r in done) if done else None},
        "rollouts_per_hour": (len(done) / wall_s * 3600) if wall_s else None,
        "time_share": {p: sum(r["timing"].get(p, 0.0) for r in done) / tot for p in PHASES},
        "model_call_s": {"p50": _q(secs, .5), "p95": _q(secs, .95), "p99": _q(secs, .99), "max": max(secs) if secs else None},
        "model_retried_calls": sum(1 for c in calls if (c.get("attempts") or 1) > 1),
        "policy_errors": sum(1 for r in rollouts if (r.get("error") or {}).get("kind") == "policy"),
        "env_errors": sum(1 for r in rollouts if (r.get("error") or {}).get("kind") == "env"),
        "prefix_cache_hit": cached / prompt if prompt else None,
        "logprob_coverage": (sum(1 for s in steps if (s.get("response") or {}).get("logprobs")) / len(steps)) if steps else None,
        "token_id_coverage": (sum(1 for s in steps if (s.get("response") or {}).get("token_ids")) / len(steps)) if steps else None,
        "groups_full": sum(1 for n in groups.values() if group and n >= group) if group else None,
        "groups": len({r["group_id"] for r in rollouts}),
    }


def report(s: dict) -> str:
    f = lambda v, pct=False: "-" if v is None else (f"{100 * v:.0f}%" if pct else f"{v:.2f}")  # noqa: E731
    lines = [f"rollouts {s['rollouts']} ({', '.join(f'{k} {v}' for k, v in s['status'].items())}); "
             f"groups full {s['groups_full']}/{s['groups']}",
             f"success {f(s['success_rate'], True)}, mean reward {f(s['mean_reward'])}, "
             f"rollout s mean {f(s['rollout_s']['mean'])} median {f(s['rollout_s']['median'])}, "
             f"rollouts/h {f(s['rollouts_per_hour'])}",
             "time share: " + ", ".join(f"{k[:-2]} {f(v, True)}" for k, v in s["time_share"].items()),
             f"model call s p50 {f(s['model_call_s']['p50'])} p95 {f(s['model_call_s']['p95'])} "
             f"p99 {f(s['model_call_s']['p99'])} max {f(s['model_call_s']['max'])}; retried {s['model_retried_calls']}, "
             f"policy errors {s['policy_errors']}, env errors {s['env_errors']}",
             f"prefix cache hit {f(s['prefix_cache_hit'], True)}; steps with logprobs {f(s['logprob_coverage'], True)}, "
             f"with token ids {f(s['token_id_coverage'], True)}"]
    return "\n".join(lines)
