# Legato vs Agent S3: how do the two computer-use loops differ?

Agent S3 is a multi-model agent: a step can call a main model, a reflection model and a separate grounding model, and
it controls the local machine or OSWorld and WindowsAgentArena VMs. Legato makes one call to one model per turn and
drives a sandbox you attach.

Not benchmarked against each other; this page compares architecture only.

| | Agent S3 (`gui-agents`) | Legato (`legato.Harness`) |
|---|---|---|
| Models | A main model (OpenAI, Anthropic, Gemini, Azure OpenAI, OpenRouter, vLLM and others) plus a required grounding model; Simular recommends UI-TARS-1.5-7B | One OpenAI-compatible model that both chooses actions and gives coordinates |
| Model calls per step | A reflection call (from step 2, on by default), the worker call (asked again, up to 3 times, if its format check fails), and one grounding call per described target, two for a drag | One per turn |
| Desktops | The local machine through pyautogui (Linux, macOS, Windows); OSWorld; WindowsAgentArena | E2B Desktop, or any sandbox given a screenshot function and an execute function |
| Wait after action | CLI: 1 s before and 1 s after each action; 5 s for `wait` | Until the screen stops changing, capped at 2 s per action |
| Screenshots kept | 8 (`max_trajectory_length`) | 8, dropped 4 at a time |
| Actions per step | 1, enforced by a format checker | Up to 3, with one screenshot after them |
| Call bounds | Up to 3 attempts per call; 4,096 output tokens on most engines, none set on the OpenAI engine | 4,096 output tokens with a cut-off note; 60 s timeout; 1 retry; 30 turns by default |
| License | Apache-2.0 | Apache-2.0 |

## When to use Agent S3 instead

- **You want published OSWorld results.** Simular reports them for S3, with and without Behavior Best-of-N;
  `legato.Harness` has no OSWorld score.
- **Your main model is weak at pointing.** S3's split lets a strong planner work with a separate grounding model.
- **You want to control your own Mac, Windows or Linux machine, or need S3's extra tools:** a code agent that runs
  Python and Bash (`--enable_local_env`), a `set_cell_values` action for spreadsheets, and a notes buffer. Legato has
  no local-machine adapter and offers only the `computer` tool.

## Code

```python
# pip install "legatolabs[e2b]"
import os
import legato
from e2b_desktop import Sandbox

agent = legato.Harness(model="qwen/qwen3.8-27b", base_url="https://openrouter.ai/api/v1",
                       api_key=os.environ["OPENROUTER_API_KEY"])
desk = Sandbox.create()                     # needs E2B_API_KEY
try:
    result = agent.run("Add a Total row under the table and save", legato.attach(desk))
    print(result.report())
finally:
    desk.kill()
```

## Sources

- [simular-ai/Agent-S](https://github.com/simular-ai/Agent-S), commit 0fff0e6
- [`gui_agents/s3/README.md#L21`](https://github.com/simular-ai/Agent-S/blob/0fff0e634869dab72027435b4586e62300185d41/gui_agents/s3/README.md#L21),
  [`#L62-L72`](https://github.com/simular-ai/Agent-S/blob/0fff0e634869dab72027435b4586e62300185d41/gui_agents/s3/README.md#L62-L72),
  [`#L110-L132`](https://github.com/simular-ai/Agent-S/blob/0fff0e634869dab72027435b4586e62300185d41/gui_agents/s3/README.md#L110-L132)
- [`worker.py#L30`](https://github.com/simular-ai/Agent-S/blob/0fff0e634869dab72027435b4586e62300185d41/gui_agents/s3/agents/worker.py#L30),
  [`#L199-L202`](https://github.com/simular-ai/Agent-S/blob/0fff0e634869dab72027435b4586e62300185d41/gui_agents/s3/agents/worker.py#L199-L202),
  [`#L308-L318`](https://github.com/simular-ai/Agent-S/blob/0fff0e634869dab72027435b4586e62300185d41/gui_agents/s3/agents/worker.py#L308-L318)
- [`grounding.py#L230-L242`](https://github.com/simular-ai/Agent-S/blob/0fff0e634869dab72027435b4586e62300185d41/gui_agents/s3/agents/grounding.py#L230-L242),
  [`#L484-L485`](https://github.com/simular-ai/Agent-S/blob/0fff0e634869dab72027435b4586e62300185d41/gui_agents/s3/agents/grounding.py#L484-L485)
- [`cli_app.py#L201-L216`](https://github.com/simular-ai/Agent-S/blob/0fff0e634869dab72027435b4586e62300185d41/gui_agents/s3/cli_app.py#L201-L216)
- [`formatters.py#L16`](https://github.com/simular-ai/Agent-S/blob/0fff0e634869dab72027435b4586e62300185d41/gui_agents/s3/utils/formatters.py#L16)
- [`common_utils.py#L39`](https://github.com/simular-ai/Agent-S/blob/0fff0e634869dab72027435b4586e62300185d41/gui_agents/s3/utils/common_utils.py#L39),
  [`#L72`](https://github.com/simular-ai/Agent-S/blob/0fff0e634869dab72027435b4586e62300185d41/gui_agents/s3/utils/common_utils.py#L72),
  [`engine.py#L60`](https://github.com/simular-ai/Agent-S/blob/0fff0e634869dab72027435b4586e62300185d41/gui_agents/s3/core/engine.py#L60),
  [`#L244`](https://github.com/simular-ai/Agent-S/blob/0fff0e634869dab72027435b4586e62300185d41/gui_agents/s3/core/engine.py#L244)
- [`LICENSE`](https://github.com/simular-ai/Agent-S/blob/0fff0e634869dab72027435b4586e62300185d41/LICENSE)
- Legato: [`harness.py`](../../legato/harness.py), [`env.py`](../../legato/env.py),
  [`settle.py`](../../legato/settle.py), [`rl/policy.py`](../../legato/rl/policy.py)
- [Benchmarks](../benchmarks.md#what-these-results-do-not-show)
