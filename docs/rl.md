# legato.rl: graded desktop rollouts for computer-use RL

`legato.rl` runs the rollout side of computer-use RL: it steps, resets and grades desktops, and writes trajectories
with token ids, logprobs, rewards, group advantages and the policy version. Your trainer and model server keep the
weights; legato.rl owns no GPUs. Measured results: [benchmarks](benchmarks.md).

## Quickstart

```bash
pip install legatolabs
export LEGATO_POLICY_KEY=...            # the model server's key; never passed on the command line

# 1. Check the server: logprobs, token ids, cached-token reporting
legato rl probe --policy-url https://your-server/v1 --model Qwen/Qwen3.5-9B

# 2. Collect a batch on policy version step-41. tasks.json lists {"task_id", "domain"} (OSWorld tasks under
#    --osworld) or {"task_id", "config": {...}} (inline OSWorld task configs)
legato rl collect --tasks tasks.json --policy-url https://your-server/v1 --model Qwen/Qwen3.5-9B \
    --version step-41 --out runs/step-41 --desktops 16 --group 8 --oversample 0.25 --max-steps 15 \
    --max-inflight 32 --osworld ~/osworld

# 3. One training sample per policy step, with group advantages
legato rl export runs/step-41 --out runs/step-41/train.jsonl

# 4. Where the batch spent its time
legato rl profile runs/step-41
```

From Python:

```python
from legato.rl import collect, export_steps, PolicyConfig, DesktopConfig
summary = collect(tasks, PolicyConfig("https://your-server/v1", "Qwen/Qwen3.5-9B", version="step-41", api_key=key),
                  out="runs/step-41", desktops=16, group=8, oversample=0.25, max_inflight=32,
                  desktop=DesktopConfig(osworld_path="/home/me/osworld"))
export_steps("runs/step-41", "runs/step-41/train.jsonl")
```

## Backends

- **`osworld`** (default): OSWorld's DesktopEnv (Docker/QEMU) on a Linux host with KVM; one VM (4 vCPU, 4 GB) per
  worker, kept up between rollouts. Fast reset kills the task apps, checks that none survived, restores the clean
  `/home/user` baseline and runs the task's setup. `os` and `multi_apps` tasks use a snapshot revert.
- **`fake`**: no VMs, for tests and for wiring up a trainer.
- **Your own:** a class with `reset`, `wait_after_reset`, `observe`, `act`, `wait_before_eval`, `evaluate` and `close`
  (see `collect.OSWorldDesktop`).

The harness is OSWorld's QwenAgent, with its model call routed through `legato.rl.policy`.

## Knobs

| Option | Default | What it does |
|---|---|---|
| `--group` | 4 | Attempts per task: one GRPO group |
| `--oversample` | 0 | Extra attempts per group. The group closes once `group` attempts finish, and the rest are cancelled at their next step (stragglers do not hold the batch) |
| `--max-inflight` | none | Cap on concurrent model requests across all desktops. Set it to about 32–48 per H100 for a 9B model |
| `--step-timeout` / `--retries` | 60 s / 1 | Per-step bound on a model call |
| `--rollout-timeout` | none | Wall-clock cap per rollout. A rollout that hits it is graded and marked `truncated` |
| `--env-retries` | 1 | A desktop failure (VM or setup) re-queues the attempt on another try |
| `--no-ready-signal --fixed-wait S` | off | OSWorld-style fixed sleeps, for baselines |
| `--no-fast-reset` | off | Snapshot revert every rollout, for baselines |
| `--image-max` | 20 | Screenshots kept in the prompt |

## Output: `legato.rollout/v0`

`DIR/rollouts.jsonl` holds one record per attempt:

- **Identity:** `rollout_id`, `task_id`, `domain`, `group_id`, `attempt`, `policy` {`model`, `version`}.
- **Outcome:** `status`, `reward` (the task's OSWorld evaluator score, run inside the desktop after the task),
  `error`, `n_steps`. `status` is `ok` (ended by the agent or the step limit), `truncated` (time limit), `cancelled`
  (its group closed first), `skipped` (its group was full before it started), `surplus` (finished after its group
  closed), `retried` (desktop failure, re-run) or `error` (kind `policy` or `env`).
- **Timing:** `timing`, seconds by phase: `reset_s`, `wait_after_reset_s`, `model_s`, `act_s`, `wait_s`, `obs_s`,
  `wait_before_eval_s`, `eval_s`, `total_s`.
- **Steps:** `steps[]`, one per policy call:
  - `request`: the prompt as message hashes, each message stored once in `DIR/messages/`, images as
    `legato-image://<sha>` in `DIR/images/`. `legato.rl.load_request` rebuilds it.
  - `response`: `content`, `reasoning`, `token_ids` (when the server returns them), `logprobs` (one float per token),
    `tokens` (only without token ids), `finish_reason`.
  - `model`: seconds, attempts, and prompt, completion and cached tokens.
  - `actions[]`: the action, plus `act_s`, `wait_s`, `ready_s`, `obs_s`.

`legato rl export` writes one line per step of every `ok` or `truncated` rollout: `messages` (image references, or
data URLs with `--inline-images`), `response_text`, `response_reasoning`, `response_token_ids`, `response_logprobs`,
`response_tokens`, `reward`, `advantage` and `policy_version`. The advantage is (reward − group mean) / group std over
the group's completed attempts, and 0 when all rewards in the group are equal.

`DIR/summary.json` (and `profile`) reports status counts, success rate, rollouts per hour, time share by phase,
model-call p50/p95/p99, retried calls, policy and env errors, prefix-cache hit rate, and the share of steps with
logprobs and with token ids.

## Notes

- **Token ids:** requested as `return_token_ids` (recent vLLM). If the server refuses, the client keeps logprobs only;
  re-tokenize `response_text` with the model's chat template.
- **Policy versions:** legato.rl does not sync weights. Load the new weights or adapter on your server, then pass the
  new `--version` (and model name, if it changed).
