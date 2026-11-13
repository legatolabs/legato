"""legato.rl command line.

  python -m legato.rl probe   --policy-url URL --model NAME [--api-key-env VAR]
  python -m legato.rl collect --tasks tasks.json --policy-url URL --model NAME --version v41 --out DIR
                              [--desktops 16] [--group 8] [--oversample 0.25] [--max-steps 15] [--max-inflight 32]
                              [--step-timeout 60] [--rollout-timeout S] [--osworld PATH] [--backend osworld|fake]
                              [--no-ready-signal --fixed-wait 3] [--no-fast-reset]
                              [--harness qwen|legato|reference|fake] [--harness-opts JSON] [--eval-only]
                              [--extra-body JSON]
  python -m legato.rl export  DIR --out train.jsonl [--inline-images]
  python -m legato.rl profile DIR

tasks.json: a list of {"task_id", "domain"} (OSWorld tasks, resolved under --osworld) or {"task_id", "config": {...}}
(an inline OSWorld task config). The API key is read from the environment variable named by --api-key-env
(default LEGATO_POLICY_KEY), never from the command line.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from legato.rl import DesktopConfig, HarnessConfig, PolicyClient, PolicyConfig, collect, export_steps, read_rollouts
from legato.rl.profile import report, summarize


def _policy(a) -> PolicyConfig:
    ev = getattr(a, "eval_only", False)              # no logprobs or token ids (hosted APIs, benchmarks)
    return PolicyConfig(base_url=a.policy_url, model=a.model, version=getattr(a, "version", "probe"),
                        api_key=os.environ.get(a.api_key_env, ""), timeout_s=a.step_timeout, retries=a.retries,
                        logprobs=not ev, token_ids=not ev, extra_body=json.loads(getattr(a, "extra_body", "") or "{}"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m legato.rl")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("probe", "collect"):
        p = sub.add_parser(name)
        p.add_argument("--policy-url", required=True, help="OpenAI-compatible root, e.g. https://host/v1")
        p.add_argument("--model", required=True)
        p.add_argument("--api-key-env", default="LEGATO_POLICY_KEY")
        p.add_argument("--step-timeout", type=float, default=60.0)
        p.add_argument("--retries", type=int, default=1)
    c = sub.choices["collect"]
    c.add_argument("--tasks", required=True)
    c.add_argument("--version", required=True, help="policy version tag written on every trajectory")
    c.add_argument("--out", required=True)
    c.add_argument("--desktops", type=int, default=4)
    c.add_argument("--group", type=int, default=4)
    c.add_argument("--oversample", type=float, default=0.0)
    c.add_argument("--max-steps", type=int, default=15)
    c.add_argument("--max-inflight", type=int, default=0, help="cap on concurrent model requests (0 = none)")
    c.add_argument("--rollout-timeout", type=float, default=0.0)
    c.add_argument("--env-retries", type=int, default=1)
    c.add_argument("--backend", default="osworld", choices=["osworld", "fake"])
    c.add_argument("--osworld", default=os.environ.get("OSWORLD_PATH", ""))
    c.add_argument("--no-ready-signal", action="store_true")
    c.add_argument("--fixed-wait", type=float, default=0.0)
    c.add_argument("--no-fast-reset", action="store_true")
    c.add_argument("--harness", default="qwen", choices=["qwen", "legato", "reference", "fake"])
    c.add_argument("--harness-opts", default="", help="JSON of legato.Harness options, e.g. '{\"max_batch\": 1}'")
    c.add_argument("--eval-only", action="store_true", help="do not request logprobs or token ids")
    c.add_argument("--extra-body", default="", help="JSON merged into every model request")
    c.add_argument("--image-max", type=int, default=20)
    e = sub.add_parser("export")
    e.add_argument("dir")
    e.add_argument("--out", required=True)
    e.add_argument("--inline-images", action="store_true")
    r = sub.add_parser("profile")
    r.add_argument("dir")
    a = ap.parse_args(argv)
    if a.cmd == "probe":
        print(json.dumps(PolicyClient(_policy(a)).probe(), indent=1))
    elif a.cmd == "collect":
        tasks = json.loads(open(a.tasks).read())
        s = collect(tasks, _policy(a), a.out, desktops=a.desktops, group=a.group, oversample=a.oversample,
                    max_steps=a.max_steps, max_inflight=a.max_inflight or None,
                    rollout_timeout_s=a.rollout_timeout or None, env_retries=a.env_retries,
                    desktop=DesktopConfig(backend=a.backend, osworld_path=a.osworld, ready_signal=not a.no_ready_signal,
                                          fast_reset=not a.no_fast_reset, fixed_wait_s=a.fixed_wait),
                    harness=HarnessConfig(name=a.harness, image_max=a.image_max,
                                          options=json.loads(a.harness_opts or "{}")))
        print(report(s))
    elif a.cmd == "export":
        print(json.dumps(export_steps(a.dir, a.out, inline_images=a.inline_images)))
    else:
        rs = read_rollouts(a.dir)
        meta = os.path.join(a.dir, "run.json")
        group = json.loads(open(meta).read()).get("group") if os.path.exists(meta) else None
        print(report(summarize(rs, group=group)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
