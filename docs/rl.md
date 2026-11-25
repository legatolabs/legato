# legato.rl: graded desktop rollouts for computer-use RL

`legato.rl` runs the rollout side of computer-use RL: it steps, resets and grades desktops, and writes trajectories
with token ids, logprobs, rewards, group advantages and the policy version. Your trainer and model server keep the
weights; legato.rl owns no GPUs. Measured results: [benchmarks](benchmarks.md).

## Try it in two minutes, without VMs

This runs the whole loop (collect, export, profile) on any machine, Windows and macOS included, with a hosted model.
Save `tasks.json`:

```json
[
  {"task_id": "t1", "instruction": "Click the word Task on the screen, then say you are done.", "reward": [1, 0]}
]
```

```bash
pip install legatolabs
export OPENROUTER_API_KEY=...           # PowerShell: $env:OPENROUTER_API_KEY = "..."

legato rl probe --policy-url https://openrouter.ai/api/v1 --model qwen/qwen3.8-27b --api-key-env OPENROUTER_API_KEY
legato rl collect --tasks tasks.json --backend fake --policy-url https://openrouter.ai/api/v1 --model qwen/qwen3.8-27b --api-key-env OPENROUTER_API_KEY --version v0 --out runs/v0 --group 2 --desktops 2 --max-steps 3 --temperature 1.0
legato rl export runs/v0 --out runs/v0/train.jsonl
legato rl profile runs/v0
```

- `probe` says in words what the server is good for: whether the model takes images, makes tool calls and returns
  logprobs.
- `collect` writes `runs/v0/rollouts.jsonl` (one record per attempt), with the prompts in `runs/v0/messages/` and the
  screenshots in `runs/v0/images/`. `export` writes one training sample per policy step; `profile` prints the batch's
  summary again.
- **What the fake backend is:** a 640×400 picture that says it is a fake desktop and shows the task. The model gets
  real requests and its actions are recorded as it gave them (`legato.Harness`, the default harness here), but nothing
  on the screen responds. **The reward is not measured:** it is the number you wrote in `tasks.json` (`[1, 0]`: 1 for
  the first attempt, 0 for the second), and every record carries `"reward_source": "fake"`. Use it to wire up a
  trainer, not to learn anything about a model.
- **What the real thing needs:** desktops that respond and grade. That is [OSWorld](https://github.com/xlang-ai/OSWorld)
  on a Linux host with KVM (`--osworld PATH`, the default backend), or [your own backend](#your-own-backend). For
  policy-gradient training (GRPO) the server must return logprobs: vLLM does, and many hosted APIs do not
  (`probe` tells you). `--require-logprobs` stops `collect` before any rollout when they are missing. Hosted models are
  fine for evaluation and for collecting data.
- **JSON options** (`--fake-opts`, `--harness-opts`, `--backend-opts`, `--extra-body`) also take a file:
  `--fake-opts @opts.json`. Use the file in PowerShell, which drops the quotes inside `'{"reward": [1, 0]}'`.

## Quickstart

On a Linux host with KVM and an OSWorld checkout:

```bash
pip install legatolabs
export LEGATO_POLICY_KEY=...            # the model server's key; never passed on the command line

# 1. Check the server: logprobs, token ids, cached-token reporting, an image, a tool call
legato rl probe --policy-url https://your-server/v1 --model Qwen/Qwen3.5-9B

# 2. Collect a batch on policy version step-41 (tasks.json: see Tasks below)
legato rl collect --tasks tasks.json --policy-url https://your-server/v1 --model Qwen/Qwen3.5-9B \
    --version step-41 --out runs/step-41 --desktops 16 --group 8 --oversample 0.25 --max-steps 15 \
    --max-inflight 32 --osworld ~/osworld

# 3. One training sample per policy step, with group advantages
legato rl export runs/step-41 --out runs/step-41/train.jsonl

# 4. Where the batch spent its time
legato rl profile runs/step-41
```

`collect` exits 1 when no rollout completed, with the first error.

From Python, put the call under the `__main__` guard: the workers are processes that import your script again on
macOS and Windows.

```python
from legato.rl import DesktopConfig, HarnessConfig, PolicyConfig, collect, export_steps

def main():
    tasks = [{"task_id": "t1", "instruction": "Open the report", "reward": [1, 0]}]
    policy = PolicyConfig("http://localhost:8000/v1", "Qwen/Qwen3.5-9B", version="step-0", temperature=1.0, seed=0)
    collect(tasks, policy, out="runs/step-0", desktops=4, group=8,
            desktop=DesktopConfig(backend="fake"),   # or DesktopConfig(osworld_path="/home/me/osworld")
            harness=HarnessConfig(name="legato"))
    export_steps("runs/step-0", "runs/step-0/train.jsonl")

if __name__ == "__main__":
    main()
```

`collect` raises `SetupError` before any worker starts when the harness or desktop can't load, and `CollectError`
(with `.summary`) when no rollout completed.

## Tasks

`--tasks` is a JSON list, one object per task:

- OSWorld: `{"task_id": "...", "domain": "chrome"}`, read from `evaluation_examples/examples/<domain>/<task_id>.json`
  under `--osworld`, or `{"task_id": "...", "config": {...}}`, an inline OSWorld task config.
- Fake backend: `{"task_id": "t1", "instruction": "Open the report", "reward": [1, 0]}`; `instruction` and `reward`
  are optional (see Backends).

## Backends

- **`osworld`** (default): OSWorld's DesktopEnv (Docker/QEMU) on a Linux host with KVM; one VM (4 vCPU, 4 GB) per
  worker, kept up between rollouts. Fast reset kills the task apps, checks that none survived, restores the clean
  `/home/user` baseline and runs the task's setup. `os` and `multi_apps` tasks use a snapshot revert.
- **`fake`** (`--backend fake`): no VMs, for tests and for wiring up a trainer. The policy still gets real requests:
  a 640×400 screen that says it is a fake desktop and shows the task. The reward is the task's `reward`, else the one
  in `--fake-opts '{"reward": ...}'`, else 0, and is recorded with `"reward_source": "fake"`. A list is read by
  attempt, so `[1, 0]` gives a group rewards 1, 0, 1, 0 and non-zero advantages. `--fake-opts` also takes `size`
  (`[w, h]`), `reset_s`, `act_s`, `fail_reset_every` and `fail_act_every` (fail every Nth reset or action).
- **`local`** (`--backend local --backend-opts @local.json --desktops 1`): this computer's own screen, mouse and
  keyboard. `local.json` is `{"hooks": "my_hooks.py"}`, a file with `reset(task)` (put the screen in the task's start
  state) and `evaluate(task) -> float` (the reward). Risky actions (`legato.safety`) are refused; moving the mouse
  into a corner of the screen stops the batch.
- **Your own:** see below.

The harness on OSWorld is its QwenAgent (it needs `--osworld`), with its model call routed through
`legato.rl.policy`. On every other backend it is `legato.Harness` (`--harness legato`). `--harness fake` tests the
wiring only: it ignores what the model says to do. With a planner in the options of `--harness legato`, a strong
model names the actions and `--model` only finds things on the screen:

```bash
legato rl collect ... --harness legato --max-steps 150 --harness-opts '{"coords": "norm1000", "max_calls": 50,
    "planner": {"model": "openai/gpt-6.1-terra", "base_url": "https://openrouter.ai/api/v1",
                "api_key_env": "OPENROUTER_API_KEY", "style": "point"}}'
```

### Your own backend

`--backend path/to/file.py:Class` (or `module:Class`) runs your environment. Each worker process imports the file and
builds `Class(**options)`, with options from `--backend-opts`. Subclass `legato.rl.Desktop` and write four methods:

```python
from legato.rl import Desktop

class MyDesktop(Desktop):
    reward_source = "my grader"                # written on every rollout

    def reset(self, task: dict) -> None:       # the task's object from tasks.json; set self.instruction
        self.instruction = task["instruction"]
    def observe(self) -> dict:                 # {"screenshot": PNG bytes}
        ...
    def act(self, action) -> tuple:            # -> (observation, done, None)
        ...
    def evaluate(self) -> float:               # the reward of the attempt that just ended
        ...
```

`action` is pyautogui code as a string (`"pyautogui.click(120, 80)"`), or `"DONE"` or `"FAIL"`, which end the episode.
With `--harness legato` its `.action` is the parsed `legato.Action` (`type`, `x`, `y`, `text`, `keys`...).
`wait_after_reset`, `wait_before_eval` and `close` are optional.
[examples/rl_custom_backend.py](../examples/rl_custom_backend.py) is a working one: a button to click, no VM.

## Sampling for GRPO

Each harness keeps its own temperature unless you give one: 0 for `legato` and `reference`, 1 for `fake`, and
QwenAgent's own for `qwen`. Benchmarks rely on these defaults.

- `--temperature T` sets the temperature of every policy request. A `temperature` in `--extra-body` or
  `--harness-opts` does the same (`--temperature` wins).
- `--seed N` gives each rollout its own seed, derived from N, the task and the attempt. Its requests carry that seed,
  then one more per request. The same N reproduces the batch's seeds on a server that honors them.
- Each rollout records `sampling` (`seed`, and the temperature its requests were sent with); each step records its
  request's `temperature`, `top_p` and `seed`.

At temperature 0 a group's rollouts are near-identical and its advantages are about 0. `collect` warns when `--group`
is above 1 at temperature 0 (not with `--eval-only`).

## GRPO with a local vLLM server

The recipe, not a measured result. Serve the policy, collect sampled groups, export, train, repeat with the next
version:

```bash
# 1. Serve the policy
vllm serve Qwen/Qwen3.5-9B --enable-auto-tool-choice --tool-call-parser hermes --max-model-len 32768

# 2. Check that it returns logprobs and token ids
legato rl probe --policy-url http://localhost:8000/v1 --model Qwen/Qwen3.5-9B

# 3. Eight sampled attempts per task at temperature 1. On fake, rewards come from tasks.json (see Backends);
#    swap --backend fake for --osworld ~/osworld on a KVM host
legato rl collect --tasks tasks.json --policy-url http://localhost:8000/v1 --model Qwen/Qwen3.5-9B \
    --version step-0 --out runs/step-0 --harness legato --group 8 --temperature 1.0 --seed 0 --backend fake

# 4. One training sample per policy step
legato rl export runs/step-0 --out runs/step-0/train.jsonl
```

Each line of `train.jsonl` is one sample: `messages` is the prompt, `response_token_ids` and `response_logprobs` are
what was sampled, and `advantage` weights it. Skip or re-tokenize lines with `tokens_cover_response` false. Load the
new weights into the server, then collect again with `--version step-1`.

## Knobs

| Option | Default | What it does |
|---|---|---|
| `--group` | 4 | Attempts per task: one GRPO group |
| `--temperature` / `--seed` | the harness's / none | Sampling of every policy request (see above) |
| `--oversample` | 0 | Extra attempts per group. The group closes once `group` attempts finish, and the rest are cancelled at their next step (stragglers do not hold the batch) |
| `--max-inflight` | none | Cap on concurrent model requests across all desktops. Set it to about 32–48 per H100 for a 9B model |
| `--step-timeout` / `--retries` | 60 s / 1 | Per-step bound on a model call |
| `--rollout-timeout` | none | Wall-clock cap per rollout. A rollout that hits it is graded and marked `truncated` |
| `--env-retries` | 1 | A desktop failure (VM or setup) re-queues the attempt on another try |
| `--no-ready-signal --fixed-wait S` | off | OSWorld-style fixed sleeps, for baselines |
| `--no-fast-reset` | off | Snapshot revert every rollout, for baselines |
| `--image-max` | 20 | Screenshots kept in the prompt |
| `--fake-opts` | none | Fake desktop settings: `reward`, `size`, `reset_s`, `act_s`, `fail_reset_every`, `fail_act_every` |
| `--backend-opts` | none | Keyword arguments for your own backend class, or for `local` |
| `--harness` | `qwen` on osworld, else `legato` | The agent loop: `qwen`, `legato`, `reference` (Anthropic's reference loop) or `fake` |
| `--require-logprobs` | off | Ask the server one small question first and stop, before any rollout, unless it returns logprobs; exit 1 if any step of the batch lacks them |
| `--eval-only` | off | Do not ask for logprobs or token ids |
| `--start-stagger` | 2 s (osworld), 0 (fake) | Wait between starting one worker and the next, so VMs don't all boot at once |

## Output: `legato.rollout/v0`

`DIR/rollouts.jsonl` holds one record per attempt:

- **Identity:** `rollout_id`, `task_id`, `domain`, `group_id`, `attempt`, `policy` {`model`, `version`},
  `sampling` {`seed`, `temperature`}.
- **Outcome:** `status`, `reward` (the task's OSWorld evaluator score, run inside the desktop after the task),
  `reward_source` (`osworld evaluator`, `legato grader`, `fake`, or your backend's),
  `error`, `n_steps`, and `retried_after` (the desktop errors of this attempt's earlier tries, when it was re-run). `status` is `ok` (ended by the agent or the step limit), `truncated` (time limit), `cancelled`
  (its group closed first), `skipped` (its group was full before it started), `surplus` (finished after its group
  closed), `retried` (desktop failure, re-run) or `error` (kind `policy` or `env`).
- **Timing:** `timing`, seconds by phase: `reset_s`, `wait_after_reset_s`, `model_s`, `act_s`, `wait_s`, `obs_s`,
  `wait_before_eval_s`, `eval_s`, `total_s`.
- **Steps:** `steps[]`, one per policy call:
  - `request`: the prompt as message hashes, each message stored once in `DIR/messages/`, images as
    `legato-image://<sha>` in `DIR/images/`. `legato.rl.load_request` rebuilds it. Also the `temperature`, `top_p`
    and `seed` sent.
  - `response`: `content`, `reasoning`, `token_ids` (when the server returns them), `logprobs` (one float per token),
    `tokens` (only without token ids), `finish_reason`, `tool_calls`, `text` (the generated text, when tool calls
    make it differ from `content`) and `tokens_cover_response`.
  - `model`: seconds, attempts, and prompt, completion and cached tokens. `attempts` counts every request sent:
    a busy server's 429 or 503 is waited out and sent again without using up `--retries`, so it can exceed
    `--retries` + 1. `retry_reasons` says why each earlier request was sent again.
  - `actions[]`: the `action` as the desktop got it (pyautogui code), `parsed` (from `legato.Harness`:
    `{"type": "click", "x": 64, "y": 20}`), plus `act_s`, `wait_s`, `ready_s`, `obs_s`.

`legato rl export` writes one line per step of every `ok` or `truncated` rollout: `rollout_id`, `group_id`, `task_id`,
`attempt`, `step`, `messages` (image references, or
data URLs with `--inline-images`), `sampling`, `response_text`, `response_reasoning`, `response_token_ids`,
`response_logprobs`, `response_tokens`, `response_content`, `response_tool_calls`, `tokens_cover_response`,
`finish_reason`, `actions` (each with `action` and `parsed`, as above), `reward`, `reward_source`, `advantage` and
`policy_version`. The advantage is (reward − group mean) / group std over
the group's completed attempts, and 0 when all rewards in the group are equal.

`response_text` is the action the model produced, tool calls included. When the server's tokens hold the tool-call
markup (vLLM returns every generated token), it is that text. Otherwise it is the content followed by one JSON object
per call, `{"name", "arguments"}`, and `tokens_cover_response` is false: the token ids and logprobs miss the call.
`response_content` is the content alone.

`DIR/summary.json` (and `profile`) reports status counts, success rate, rollouts per hour (completed rollouts per
hour of `wall_s`, the wall clock from the start of `collect` to the end of the last rollout, worker start-up
included), time share by phase,
model-call p50/p95/p99, retried calls, policy and env errors, prefix-cache hit rate, and the share of steps with
logprobs, with token ids, and whose tokens cover the whole response.

## Notes

- **Token ids:** requested as `return_token_ids` (recent vLLM). If the server refuses, the client keeps logprobs only;
  re-tokenize `response_text` with the model's chat template.
- **Policy versions:** legato.rl does not sync weights. Load the new weights or adapter on your server, then pass the
  new `--version` (and model name, if it changed).
