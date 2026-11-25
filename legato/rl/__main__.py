"""legato.rl command line.

  legato rl probe   --policy-url URL --model NAME [--api-key-env VAR]
  legato rl collect --tasks tasks.json --policy-url URL --model NAME --version v41 --out DIR
                    [--desktops 16] [--start-stagger S] [--group 8] [--oversample 0.25] [--max-steps 15]
                    [--max-inflight 32] [--temperature T] [--seed N] [--step-timeout 60] [--rollout-timeout S]
                    [--backend osworld|fake|local|FILE.py:Class|module:Class] [--osworld PATH] [--fake-opts JSON]
                    [--backend-opts JSON] [--no-ready-signal --fixed-wait 3] [--no-fast-reset]
                    [--harness qwen|legato|reference|fake] [--harness-opts JSON] [--eval-only]
                    [--require-logprobs] [--extra-body JSON]
  legato rl export  DIR --out train.jsonl [--inline-images]
  legato rl profile DIR

First try, without VMs: docs/rl.md, "Try it in two minutes" (legato.computer/docs/rl#try-it-in-two-minutes-without-vms).
tasks.json (legato.computer/docs/rl#tasks): a list of {"task_id", "domain"} (OSWorld tasks, resolved under --osworld)
or {"task_id", "config": {...}} (an inline OSWorld task config); with --backend fake, {"task_id", "instruction",
"reward"}. The API key is read from the environment variable named by --api-key-env (default LEGATO_POLICY_KEY),
never from the command line. Every JSON option also takes @file.json (in PowerShell, quotes inside an argument are
lost: use the file). collect exits 1 when no rollout completed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from legato.rl import DesktopConfig, HarnessConfig, PolicyClient, PolicyConfig, collect, export_steps, read_rollouts
from legato.rl.collect import CollectError, SetupError, effective_temperature, preflight
from legato.rl.policy import PolicyError, probe_verdict
from legato.rl.profile import report, summarize, wall_clock


POWERSHELL = ("In PowerShell, quotes inside an argument are lost: put the JSON in a file and pass {flag} @file.json "
              "(or escape them: {flag} '{{\\\"reward\\\": [1, 0]}}')")


class UserError(Exception):
    """A mistake in the command line or its files: printed as one line, exit 2."""


def read_text(path: str) -> str:
    """A text file as PowerShell may have written it: UTF-8 with or without a BOM, or UTF-16 (PowerShell 5's `>`)."""
    raw = open(path, "rb").read()
    return raw.decode("utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig")


def json_arg(flag: str, value: str) -> dict:
    """A JSON-valued option: the JSON itself, or @file.json. Raises UserError, in one line, when it is not JSON."""
    if not value:
        return {}
    text = value
    if value.startswith("@"):
        try:
            text = read_text(value[1:])
        except OSError as e:
            raise UserError(f"{flag} {value}: cannot read {value[1:]} ({e.strerror or e})") from None
    try:
        out = json.loads(text)
    except ValueError as e:
        raise UserError(f"{flag}: not valid JSON ({e}): {text.strip()[:80]!r}. "
                        + POWERSHELL.format(flag=flag)) from None
    if not isinstance(out, dict):
        raise UserError(f"{flag}: a JSON object is needed ({{...}}), not {text.strip()[:80]!r}")
    return out


class Parser(argparse.ArgumentParser):
    def error(self, message: str):
        if "unrecognized arguments" in message and any(c in message for c in "{}[]"):    # JSON split by the shell
            message += (". A JSON option arrived in pieces: in PowerShell, quotes inside an argument are lost. Put "
                        "the JSON in a file and pass it with @, as in --fake-opts @opts.json")
        super().error(message)


def _policy(a) -> PolicyConfig:
    ev = getattr(a, "eval_only", False)              # no logprobs or token ids (hosted APIs, benchmarks)
    return PolicyConfig(base_url=a.policy_url, model=a.model, version=getattr(a, "version", "probe"),
                        api_key=os.environ.get(a.api_key_env, ""), timeout_s=a.step_timeout, retries=a.retries,
                        logprobs=not ev, token_ids=not ev, extra_body=json_arg("--extra-body", getattr(a, "extra_body", "")),
                        temperature=getattr(a, "temperature", None), seed=getattr(a, "seed", None))


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    cmd = next((x for x in argv if not x.startswith("-")), "")
    try:
        return _main(argv)
    except UserError as e:
        print(f"legato rl {cmd}: {e}", file=sys.stderr)
        return 2
    except PolicyError as e:                         # a wrong URL, key or model name: the server's answer, one line
        print(f"legato rl {cmd}: {e}", file=sys.stderr)
        return 1


def _main(argv: list) -> int:
    ap = Parser(prog="legato rl", description="Graded desktop rollouts for computer-use RL. First try, without VMs: "
                                              "docs/rl.md, \"Try it in two minutes\".")
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
                   help="JSON list of tasks; the format: legato.computer/docs/rl#tasks (docs/rl.md, Tasks). Without "
                        'VMs: [{"task_id": "t1", "instruction": "Click the word Task", "reward": [1, 0]}] and --backend fake')
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
    c.add_argument("--backend", default="osworld", metavar="{osworld,fake,local,FILE.py:Class,module:Class}",
                   help="osworld: OSWorld VMs (Linux with KVM); fake: no VMs, a screen that responds to nothing and a "
                        "reward you set, for wiring up a trainer; local: this computer's screen with your reset and "
                        "grading (legato/rl/local.py); or your own class (docs/rl.md, Your own backend)")
    c.add_argument("--backend-opts", default="",
                   help="JSON (or @file.json) of keyword arguments for your own backend class, or for local")
    c.add_argument("--osworld", default=os.environ.get("OSWORLD_PATH", ""),
                   help="an OSWorld checkout (default $OSWORLD_PATH)")
    c.add_argument("--fake-opts", default="",
                   help='JSON (or @file.json) of fake desktop settings, e.g. \'{"reward": [1, 0]}\' (reward for tasks without one: a '
                        "number, or a list by attempt; size [w, h]; reset_s, act_s, fail_reset_every, fail_act_every)")
    c.add_argument("--no-ready-signal", action="store_true", help="fixed sleeps instead of waiting for the screen")
    c.add_argument("--fixed-wait", type=float, default=0.0, help="seconds slept after each action with --no-ready-signal")
    c.add_argument("--no-fast-reset", action="store_true", help="revert the VM snapshot every rollout")
    c.add_argument("--harness", default=None, choices=["qwen", "legato", "reference", "fake"],
                   help="the agent loop (default: qwen, OSWorld's QwenAgent, with --backend osworld; else legato, "
                        "legato.Harness). fake tests the wiring only: it ignores what the model says to do")
    c.add_argument("--harness-opts", default="",
                   help="JSON (or @file.json) of legato.Harness options, e.g. '{\"max_batch\": 1}'")
    c.add_argument("--eval-only", action="store_true", help="do not request logprobs or token ids")
    c.add_argument("--require-logprobs", action="store_true",
                   help="for policy-gradient training: ask the server one small question first and stop, before any "
                        "rollout, unless it returns logprobs; exit 1 if any step of the batch lacks them")
    c.add_argument("--extra-body", default="", help="JSON (or @file.json) merged into every model request")
    c.add_argument("--image-max", type=int, default=20, help="screenshots kept in the prompt")
    e = sub.add_parser("export")
    e.add_argument("dir")
    e.add_argument("--out", required=True)
    e.add_argument("--inline-images", action="store_true")
    r = sub.add_parser("profile")
    r.add_argument("dir")
    if not argv:
        ap.print_help()
        return 2
    a = ap.parse_args(argv)
    if a.cmd == "probe":
        p = PolicyClient(_policy(a)).probe()
        print(json.dumps(p, indent=1))
        print("\n".join(probe_verdict(p)), file=sys.stderr)
        return 0 if p["vision"] else 1
    elif a.cmd == "collect":
        try:
            tasks = json.loads(read_text(a.tasks))
        except OSError as e:
            raise UserError(f"--tasks {a.tasks}: cannot read it ({e.strerror or e}). The format: docs/rl.md, Tasks; "
                            'without VMs: [{"task_id": "t1", "instruction": "Click the word Task", "reward": [1, 0]}]') from None
        except ValueError as e:
            raise UserError(f"--tasks {a.tasks}: not valid JSON ({e})") from None
        if not tasks or not isinstance(tasks, list) or not all(isinstance(t, dict) and "task_id" in t for t in tasks):
            print(f"legato rl collect: {a.tasks} lists no tasks: it must be a JSON list of objects with a \"task_id\" "
                  "(docs/rl.md, Tasks)", file=sys.stderr)
            return 2
        if a.require_logprobs and a.eval_only:
            raise UserError("--require-logprobs and --eval-only contradict each other: --eval-only asks for no logprobs")
        policy = _policy(a)
        harness = HarnessConfig(name=a.harness or ("qwen" if a.backend == "osworld" else "legato"),
                                image_max=a.image_max, options=json_arg("--harness-opts", a.harness_opts))
        desktop = DesktopConfig(backend=a.backend, osworld_path=a.osworld, ready_signal=not a.no_ready_signal,
                                fast_reset=not a.no_fast_reset, fixed_wait_s=a.fixed_wait,
                                fake=json_arg("--fake-opts", a.fake_opts),
                                options=json_arg("--backend-opts", a.backend_opts))
        if a.fake_opts and a.backend != "fake":
            raise UserError(f"--fake-opts is for --backend fake; --backend {a.backend} takes --backend-opts")
        if harness.name == "fake":
            print("legato rl: note: --harness fake tests the wiring only: every action is the same click whatever the "
                  "model replies, and replies are cut at 64 tokens. For the model's own actions drop it "
                  "(--harness legato).", file=sys.stderr)
        if a.group > 1 and effective_temperature(policy, harness) == 0 and not a.eval_only:
            print(f"legato rl: warning: temperature 0 with --group {a.group}: a group's rollouts will be near-identical "
                  "and its GRPO advantages ~0; pass --temperature 1.0 to sample", file=sys.stderr)
        try:
            if a.require_logprobs:
                preflight(desktop, harness)          # a setup mistake is reported before the server is asked anything
                r = PolicyClient(policy).chat([{"role": "user", "content": "Reply with the word ok."}], max_tokens=16)
                if not r["logprobs"]:
                    print(f"legato rl collect: --require-logprobs: {a.model} at {a.policy_url} returned no logprobs, so "
                          "its rollouts cannot train a policy by policy gradient. Nothing was collected. Use vLLM or "
                          "another server that returns logprobs (docs/rl.md, \"GRPO with a local vLLM server\"), or "
                          "drop --require-logprobs to collect for evaluation.", file=sys.stderr)
                    return 1
            s = collect(tasks, policy, a.out, desktops=a.desktops, group=a.group, oversample=a.oversample,
                        max_steps=a.max_steps, max_inflight=a.max_inflight or None,
                        rollout_timeout_s=a.rollout_timeout or None, env_retries=a.env_retries,
                        desktop=desktop, harness=harness, start_stagger_s=a.start_stagger)
        except SetupError as err:
            print(f"legato rl collect: {err}", file=sys.stderr)
            return 2
        except CollectError as err:
            print(report(err.summary))
            print(f"legato rl collect: {err}", file=sys.stderr)
            return 1
        print(report(s))
        print(f"rollouts: {a.out}/rollouts.jsonl; next: legato rl export {a.out} --out {a.out}/train.jsonl")
        cov = s.get("logprob_coverage")
        if not a.eval_only and cov is not None and cov < 1:
            print(f"legato rl: note: {100 * cov:.0f}% of steps came back with logprobs. This batch is good for "
                  "evaluation and as data, not for policy-gradient training: serve the model with vLLM or another "
                  "server that returns logprobs (legato rl probe says what a server returns).", file=sys.stderr)
            if a.require_logprobs:
                return 1
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
