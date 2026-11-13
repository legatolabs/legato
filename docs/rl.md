# legato.rl: graded desktop rollouts for computer-use RL

`legato.rl` runs the rollout side of computer-use RL. Your trainer keeps the weights, and your model server keeps
serving them. The desktops run wherever you already run them. legato.rl sits between the three:

- **Step loop:** screenshot → policy call → action → wait until the screen is ready.
- **Between tasks:** it resets each desktop.
- **After each attempt:** it grades it.
- **Output:** trajectories your trainer can use directly, with token ids, logprobs, rewards, group advantages and
  the policy version.

It owns no GPUs, and it never sees your weights.

```
trainer ──(weight sync, as today)──> model server (vLLM, SGLang, a provider)
   ^                                        ^
   | trajectories                           | screenshot + history -> action, with logprobs
   |                                        |
legato.rl collect ──────────────────────────┘
   └── desktops (one worker each): reset / act / wait until ready / screenshot / grade
```

## What it measured (our OSWorld study: OSWorld tasks on OSWorld's own harness, Qwen3.5-9B, 1,560 rollouts)

- Rollouts **1.5–1.9× faster** than OSWorld's own setup, with the same success rate. Against the 5 s per-action
  setting, the time ratio was 0.52 (95% CI 0.43–0.65); against 0 s it was 0.67 (0.52–0.89).
- **Fast reset** took 2.2 s, against 20.4 s for a snapshot revert, on the domains it covers.
- The **ready signal** costs about 0.44 s per action and replaces 3–5 s fixed sleeps. It cut the 60 s wait after
  each reset to about 4 s.
- **One H100 serving Qwen3.5-9B:** latency doubled at about 32 agents, throughput peaked near 48, and it collapsed
  past 64 when the KV cache filled. That is why `--max-inflight` exists.
- **Keep every screenshot in the prompt.** Dropping old ones lowered the prefix-cache hit rate from 85% to 25–58%
  and cost success.

## Quickstart

```bash
pip install legatolabs
export LEGATO_POLICY_KEY=...            # the model server's key; never passed on the command line

# 1. Check the server: logprobs, token ids, cached-token reporting
python -m legato.rl probe --policy-url https://your-server/v1 --model Qwen/Qwen3.5-9B

# 2. Collect a batch on policy version step-41
python -m legato.rl collect --tasks tasks.json --policy-url https://your-server/v1 --model Qwen/Qwen3.5-9B \
    --version step-41 --out runs/step-41 --desktops 16 --group 8 --oversample 0.25 --max-steps 15 \
    --max-inflight 32 --osworld ~/osworld

# 3. One training sample per policy step, with group advantages
python -m legato.rl export runs/step-41 --out runs/step-41/train.jsonl

# 4. Where the batch spent its time
python -m legato.rl profile runs/step-41
```

From Python:

```python
from legato.rl import collect, export_steps, PolicyConfig, DesktopConfig
summary = collect(tasks, PolicyConfig("https://your-server/v1", "Qwen/Qwen3.5-9B", version="step-41", api_key=key),
                  out="runs/step-41", desktops=16, group=8, oversample=0.25, max_inflight=32,
                  desktop=DesktopConfig(osworld_path="/home/me/osworld"))
export_steps("runs/step-41", "runs/step-41/train.jsonl")
```

`tasks.json` is a list of entries in either of two forms:
- `{"task_id", "domain"}`: an OSWorld task, resolved under `--osworld`;
- `{"task_id", "config": {...}}`: an inline task config in OSWorld's format, with instruction, setup and evaluator.

## Backends

- **`osworld`** (default) runs OSWorld's DesktopEnv with its Docker/QEMU provider on a Linux host with KVM.
  - Each worker owns one VM (4 vCPU, 4 GB) and keeps it up between rollouts.
  - Fast reset kills the task apps, checks that none survived, restores the clean `/home/user` baseline, then runs
    the task's own setup.
  - `os` and `multi_apps` tasks change state outside `/home/user`, so they fall back to a snapshot revert.
- **`fake`** needs no VMs. Use it for tests and to wire up a trainer before any desktops exist.
- **Sandbox providers** (Daytona, E2B and others) are the next backends. A backend is a class with `reset`,
  `wait_after_reset`, `observe`, `act`, `wait_before_eval`, `evaluate` and `close` (see `collect.OSWorldDesktop`).

The harness is OSWorld's QwenAgent: its prompt, its history and its action parsing. legato.rl routes its model
call through `legato.rl.policy`.

## Knobs

| Option | Default | What it does |
|---|---|---|
| `--group` | 4 | Attempts per task: one GRPO group |
| `--oversample` | 0 | Extra attempts per group. The group closes once `group` attempts finish, and the rest are cancelled at their next step (stragglers do not hold the batch) |
| `--max-inflight` | none | Cap on concurrent model requests across all desktops. Set it to about 32–48 per H100 for a 9B model (our OSWorld study) |
| `--step-timeout` / `--retries` | 60 s / 1 | Per-step bound on a model call. In our OSWorld study, one hung request held a rollout for 34 minutes |
| `--rollout-timeout` | none | Wall-clock cap per rollout. A rollout that hits it is graded and marked `truncated` |
| `--env-retries` | 1 | A desktop failure (VM or setup) re-queues the attempt on another try |
| `--no-ready-signal --fixed-wait S` | off | OSWorld-style fixed sleeps, for baselines |
| `--no-fast-reset` | off | Snapshot revert every rollout, for baselines |
| `--image-max` | 20 | Screenshots kept in the prompt |

## Output: `legato.rollout/v0`

`DIR/rollouts.jsonl` holds one record per attempt; `DIR/messages/` and `DIR/images/` hold the prompts. Its fields:

- **Identity:**
  - `rollout_id`, `task_id`, `domain`, `group_id`, `attempt`;
  - `policy` {`model`, `version`}.
- **Outcome:**
  - `status`, one of:
    - `ok`: finished by the agent or the step limit;
    - `truncated`: the time limit hit;
    - `cancelled`: its group closed first;
    - `skipped`: its group was full before it started;
    - `surplus`: finished after its group closed;
    - `retried`: a desktop failure, re-run;
    - `error`: kind `policy` or `env`.
  - `reward`, `error`, `n_steps`.
- **Timing:** `timing`, seconds by phase: `reset_s`, `wait_after_reset_s`, `model_s`, `act_s`, `wait_s`,
  `obs_s`, `wait_before_eval_s`, `eval_s`, `total_s`.
- **Steps:** `steps[]`, one entry per policy call:
  - `request`: the exact prompt, as a list of message hashes. Each message is stored once in `DIR/messages/`, with
    its images as `legato-image://<sha>` in `DIR/images/`; `legato.rl.load_request` rebuilds it. Storage then
    grows with the number of steps, instead of repeating the whole history at every step.
  - `response`: `content`, `reasoning`, `token_ids` (when the server returns them), `logprobs` (one float per
    token), `tokens` (token strings, only when there are no token ids) and `finish_reason`;
  - `model`: seconds, attempts, and prompt, completion and cached tokens;
  - `actions[]`: the action, plus `act_s`, `wait_s`, `ready_s` and `obs_s`.

`legato.rl export` writes one line per step of every `ok` or `truncated` rollout. Each line carries:
- `messages` (image references, or inline data URLs with `--inline-images`);
- `response_text`, `response_reasoning`, `response_token_ids`, `response_logprobs` and `response_tokens`;
- `reward` and `advantage`. The advantage is (reward − group mean) / group std over the group's completed attempts,
  and is 0 when every reward in the group is the same.
- `policy_version`, so trainers can correct for rollouts collected on an older policy.

`DIR/summary.json` (and `profile`) reports:
- status counts and success rate;
- rollouts per hour;
- time share by phase;
- model-call p50/p95/p99;
- retried calls, policy and env errors;
- prefix-cache hit rate;
- the share of steps with logprobs and with token ids.

## Notes

- **Token ids:** the client asks for `return_token_ids`, which recent vLLM supports. If the server refuses the
  field, the client drops it and keeps logprobs; the trainer can then re-tokenize `response_text` with the
  model's chat template.
- **Policy versions:** legato.rl does not sync weights. Load the new weights or adapter on your model server, then
  pass the new `--version`, and the model name if it changed.
- **Rewards:** OSWorld's evaluators run inside the desktop after the task; a task's reward is its evaluator score.
