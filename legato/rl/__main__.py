"""legato.rl command line.

  legato rl probe   --policy-url URL --model NAME [--api-key-env VAR]
  legato rl collect --tasks tasks.json --policy-url URL --model NAME --version v41 --out DIR
                    [--desktops 16] [--start-stagger S] [--group 8] [--oversample 0.25] [--max-steps 15]
                    [--max-inflight 32] [--temperature T] [--seed N] [--step-timeout 60] [--rollout-timeout S]
                    [--backend osworld|fake] [--osworld PATH] [--fake-opts JSON]
                    [--no-ready-signal --fixed-wait 3] [--no-fast-reset]
                    [--harness qwen|legato|reference|fake] [--harness-opts JSON] [--eval-only]
                    [--extra-body JSON]
  legato rl export  DIR --out train.jsonl [--inline-images]
  legato rl profile DIR

tasks.json (legato.computer/docs/rl#tasks): a list of {"task_id", "domain"} (OSWorld tasks, resolved under --osworld)
or {"task_id", "config": {...}} (an inline OSWorld task config); with --backend fake, {"task_id", "instruction",
"reward"}. The API key is read from the environment variable named by --api-key-env (default LEGATO_POLICY_KEY),
never from the command line. collect exits 1 when no rollout completed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from legato.rl import DesktopConfig, HarnessConfig, PolicyClient, PolicyConfig, collect, export_steps, read_rollouts
from legato.rl.collect import CollectError, SetupError, effective_temperature
from legato.rl.profile import report, summarize, wall_clock


def _policy(a) -> PolicyConfig:
    ev = getattr(a, "eval_only", False)              # no logprobs or token ids (hosted APIs, benchmarks)
    return PolicyConfig(base_url=a.policy_url, model=a.model, version=getattr(a, "version", "probe"),
                        api_key=os.environ.get(a.api_key_env, ""), timeout_s=a.step_timeout, retries=a.retries,
                        logprobs=not ev, token_ids=not ev, extra_body=json.loads(getattr(a, "extra_body", "") or "{}"),
                        temperature=getattr(a, "temperature", None), seed=getattr(a, "seed", None))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="legato rl")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("probe", "collect"):
        p = sub.add_parser(name)
        p.add_argument("--policy-url", required=True, help="OpenAI-compatible root, e.g. https://host/v1")
        p.add_argument("--model", required=True, help="the model name the server serves")
        p.add_argument("--api-key-env", default="LEGATO_POLICY_KEY", help="environment variable holding the server's key")
        p.add_argument("--step-timeout", type=float, default=60.0, help="seconds per model call attempt")
        p.add_argument("--retries", type=int, default=1, help="retries of a model call that failed or timed out")
    c = sub.choices["collect"]
    c.add_argument("--tasks", required=True,
                   help="JSON list of tasks; the format: legato.computer/docs/rl#tasks (docs/rl.md, Tasks)")
    c.add_argument("--version", required=True, help="policy version tag written on every trajectory")
    c.add_argument("--out", required=True, help="output directory")
    c.add_argument("--desktops", type=int, default=4, help="worker processes, one desktop each")
    c.add_argument("--start-stagger", type=float, default=None,
                   help="seconds between starting one worker and the next (default 2 for osworld, 0 for fake)")
    c.add_argument("--group", type=int, default=4, help="attempts per task: one GRPO group")
    c.add_argument("--oversample", type=float, default=0.0,
                   help="extra attempts per group, as a share of --group; the group closes once --group finish")
    c.add_argument("--max-steps", type=int, default=15, help="policy steps per rollout")
    c.add_argument("--max-inflight", type=int, default=0, help="cap on concurrent model requests (0 = none)")
    c.add_argument("--temperature", type=float, default=None,
                   help="sampling temperature of every policy request (default: the harness's own; 0 for legato)")
    c.add_argument("--seed", type=int, default=None,
                   help="base sampling seed: each rollout gets its own seed from it, sent with its requests and recorded")
    c.add_argument("--rollout-timeout", type=float, default=0.0,
                   help="wall-clock seconds per rollout (0 = none); a rollout that hits it is graded as truncated")
    c.add_argument("--env-retries", type=int, default=1, help="re-runs of an attempt after a desktop failure")
    c.add_argument("--backend", default="osworld", choices=["osworld", "fake"],
                   help="osworld: OSWorld VMs; fake: no VMs, for tests and trainer wiring")
    c.add_argument("--osworld", default=os.environ.get("OSWORLD_PATH", ""),
                   help="an OSWorld checkout (default $OSWORLD_PATH)")
    c.add_argument("--fake-opts", default="",
                   help='JSON of fake desktop settings, e.g. \'{"reward": [1, 0]}\' (reward for tasks without one: a '
                        "number, or a list by attempt; size [w, h]; reset_s, act_s, fail_reset_every, fail_act_every)")
    c.add_argument("--no-ready-signal", action="store_true", help="fixed sleeps instead of waiting for the screen")
    c.add_argument("--fixed-wait", type=float, default=0.0, help="seconds slept after each action with --no-ready-signal")
    c.add_argument("--no-fast-reset", action="store_true", help="revert the VM snapshot every rollout")
    c.add_argument("--harness", default=None, choices=["qwen", "legato", "reference", "fake"],
                   help="the agent loop (default: fake with --backend fake, else qwen, OSWorld's QwenAgent)")
    c.add_argument("--harness-opts", default="", help="JSON of legato.Harness options, e.g. '{\"max_batch\": 1}'")
    c.add_argument("--eval-only", action="store_true", help="do not request logprobs or token ids")
    c.add_argument("--extra-body", default="", help="JSON merged into every model request")
    c.add_argument("--image-max", type=int, default=20, help="screenshots kept in the prompt")
    e = sub.add_parser("export")
    e.add_argument("dir")
    e.add_argument("--out", required=True)
    e.add_argument("--inline-images", action="store_true")
    r = sub.add_parser("profile")
    r.add_argument("dir")
    argv = sys.argv[1:] if argv is None else list(argv)
    if not argv:
        ap.print_help()
        return 2
    a = ap.parse_args(argv)
    if a.cmd == "probe":
        print(json.dumps(PolicyClient(_policy(a)).probe(), indent=1))
    elif a.cmd == "collect":
        tasks = json.loads(open(a.tasks).read())
        if not tasks:
            print(f"legato rl collect: {a.tasks} lists no tasks", file=sys.stderr)
            return 2
        policy = _policy(a)
        harness = HarnessConfig(name=a.harness or ("fake" if a.backend == "fake" else "qwen"), image_max=a.image_max,
                                options=json.loads(a.harness_opts or "{}"))
        if a.group > 1 and effective_temperature(policy, harness) == 0 and not a.eval_only:
            print(f"legato rl: warning: temperature 0 with --group {a.group}: a group's rollouts will be near-identical "
                  "and its GRPO advantages ~0; pass --temperature 1.0 to sample", file=sys.stderr)
        try:
            s = collect(tasks, policy, a.out, desktops=a.desktops, group=a.group, oversample=a.oversample,
                        max_steps=a.max_steps, max_inflight=a.max_inflight or None,
                        rollout_timeout_s=a.rollout_timeout or None, env_retries=a.env_retries,
                        desktop=DesktopConfig(backend=a.backend, osworld_path=a.osworld,
                                              ready_signal=not a.no_ready_signal, fast_reset=not a.no_fast_reset,
                                              fixed_wait_s=a.fixed_wait, fake=json.loads(a.fake_opts or "{}")),
                        harness=harness, start_stagger_s=a.start_stagger)
        except SetupError as err:
            print(f"legato rl collect: {err}", file=sys.stderr)
            return 2
        except CollectError as err:
            print(report(err.summary))
            print(f"legato rl collect: {err}", file=sys.stderr)
            return 1
        print(report(s))
    elif a.cmd == "export":
        print(json.dumps(export_steps(a.dir, a.out, inline_images=a.inline_images)))
    else:
        rs = read_rollouts(a.dir)
        meta = os.path.join(a.dir, "run.json")
        run = json.loads(open(meta).read()) if os.path.exists(meta) else {}
        print(report(summarize(rs, wall_s=wall_clock(rs, run.get("started")), group=run.get("group"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
