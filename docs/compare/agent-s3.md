# Legato vs Agent S3: how do the two computer-use loops differ?

**Short answer.** Agent S3 is a multi-model agent: a step can call a main model, a reflection model and a separate
grounding model, and it controls the local machine or OSWorld and WindowsAgentArena VMs. Legato makes one call to one
model per turn and drives a sandbox you attach. We have not benchmarked Legato against Agent S3, so this page compares
architecture only; a same-model run needs an S3 adapter for our desktops and is deferred.

## At a glance

| | Agent S3 (`gui-agents`) | Legato (`legato.Harness`) |
|---|---|---|
| Models | A main model (OpenAI, Anthropic, Gemini, Azure OpenAI, OpenRouter, vLLM and others) plus a required grounding model; Simular recommends UI-TARS-1.5-7B [1] | One OpenAI-compatible model that both chooses actions and gives coordinates [8] |
| Model calls per step | A reflection call (from step 2, on by default), the worker call (asked again, up to 3 times, if its format check fails), and one grounding call per described target, two for a drag [2][3] | One per turn [8] |
| Desktops | The local machine through pyautogui (Linux, macOS, Windows); OSWorld; WindowsAgentArena [1][4] | E2B Desktop, or any sandbox given a screenshot function and an execute function [9] |
| Wait after action | CLI: 1 s before and 1 s after each action; 5 s for `wait` [4] | Waits until the screen stops changing, capped at 2 s per action [10] |
| Screenshots kept | 8 (`max_trajectory_length`) [5] | 8, dropped 4 at a time [8] |
| Actions per step | 1, enforced by a format checker [6] | Up to 3, with one screenshot after them [8] |
| Call bounds | Up to 3 attempts per call; 4,096 output tokens on most engines, none set on the OpenAI engine; request timeout not verified [7] | 4,096 output tokens with a cut-off note; 60 s timeout; 1 retry; 30 turns by default [8][11] |
| License | Apache-2.0 [12] | Apache-2.0 |
| Status | Active (commit 0fff0e6, 21 October 2026); Agent S4 released 20 October 2026 | Version 0.1.0 |

## Benchmarks

We have no measurement of Agent S3. For reference, Simular's own figures (self-reported, on OSWorld, not comparable
with Legato's office-task numbers):
- S3 alone: 66% at 100 steps; 72.6% with Behavior Best-of-N, which picks from several rollouts (S3 README) [1].
- The S3 paper reports 35.1 model calls and 891 s per task with GPT-5 at 100 steps (arXiv 2510.02250, Table 2) [12].

## What the designs trade

- **Grounding.** S3 asks a dedicated grounding model for coordinates, so the main model only describes the target.
  Legato needs one model that returns coordinates itself, on a 0 to 1000 grid by default for non-Claude models.
- **Time per step.** S3's CLI sleeps 2 s around every action and, with reflection on (the default), makes at least
  two model calls per step from the second step on. Legato waits for the screen to settle and makes one call. Whether
  S3's extra calls cost or save time end to end depends on the task; we have not measured it.
- **Tools.** S3 adds a code agent that runs Python and Bash (`--enable_local_env`), a `set_cell_values` action for
  spreadsheets, text-span selection and a notes buffer. Legato offers only the `computer` tool.

## When to use Agent S3 instead

- **You want published OSWorld results.** Simular reports them for S3, with and without Behavior Best-of-N;
  `legato.Harness` has no OSWorld score.
- **Your main model is weak at pointing.** S3's split lets a strong planner work with a separate grounding model.
- **You want to control your own Mac, Windows or Linux machine** out of the box. Legato has no local-machine adapter;
  you would write the `execute` function yourself.
- **You need its extra tools:** the code agent, spreadsheet cell writes, or the knowledge notes.
- **You evaluate on WindowsAgentArena,** for which the repository ships a setup guide [1].
- **You want Agent S4,** which evolves the agent's tool interfaces from task experience [1].

## Code

```bash
pip install legato
pip install "legato[e2b]"   # E2B Desktop
```

```python
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

Agent S links are to [simular-ai/Agent-S](https://github.com/simular-ai/Agent-S) at commit
0fff0e634869dab72027435b4586e62300185d41.

1. Models, grounding, options and scores: [`gui_agents/s3/README.md#L21`](https://github.com/simular-ai/Agent-S/blob/0fff0e634869dab72027435b4586e62300185d41/gui_agents/s3/README.md#L21), [`#L62-L72`](https://github.com/simular-ai/Agent-S/blob/0fff0e634869dab72027435b4586e62300185d41/gui_agents/s3/README.md#L62-L72), [`#L110-L132`](https://github.com/simular-ai/Agent-S/blob/0fff0e634869dab72027435b4586e62300185d41/gui_agents/s3/README.md#L110-L132); Agent S4 and WindowsAgentArena: [`README.md#L47`](https://github.com/simular-ai/Agent-S/blob/0fff0e634869dab72027435b4586e62300185d41/README.md#L47), [`WAA_setup.md`](https://github.com/simular-ai/Agent-S/blob/0fff0e634869dab72027435b4586e62300185d41/WAA_setup.md)
2. Reflection and worker calls: [`gui_agents/s3/agents/worker.py#L199-L202`](https://github.com/simular-ai/Agent-S/blob/0fff0e634869dab72027435b4586e62300185d41/gui_agents/s3/agents/worker.py#L199-L202), [`#L308-L318`](https://github.com/simular-ai/Agent-S/blob/0fff0e634869dab72027435b4586e62300185d41/gui_agents/s3/agents/worker.py#L308-L318)
3. Grounding calls: [`gui_agents/s3/agents/grounding.py#L230-L242`](https://github.com/simular-ai/Agent-S/blob/0fff0e634869dab72027435b4586e62300185d41/gui_agents/s3/agents/grounding.py#L230-L242), [`#L484-L485`](https://github.com/simular-ai/Agent-S/blob/0fff0e634869dab72027435b4586e62300185d41/gui_agents/s3/agents/grounding.py#L484-L485)
4. Local control and sleeps: [`gui_agents/s3/cli_app.py#L201-L216`](https://github.com/simular-ai/Agent-S/blob/0fff0e634869dab72027435b4586e62300185d41/gui_agents/s3/cli_app.py#L201-L216)
5. History length: [`worker.py#L30`](https://github.com/simular-ai/Agent-S/blob/0fff0e634869dab72027435b4586e62300185d41/gui_agents/s3/agents/worker.py#L30)
6. Single action: [`gui_agents/s3/utils/formatters.py#L16`](https://github.com/simular-ai/Agent-S/blob/0fff0e634869dab72027435b4586e62300185d41/gui_agents/s3/utils/formatters.py#L16)
7. Retries and token limits: [`gui_agents/s3/utils/common_utils.py#L39`](https://github.com/simular-ai/Agent-S/blob/0fff0e634869dab72027435b4586e62300185d41/gui_agents/s3/utils/common_utils.py#L39), [`#L72`](https://github.com/simular-ai/Agent-S/blob/0fff0e634869dab72027435b4586e62300185d41/gui_agents/s3/utils/common_utils.py#L72), [`gui_agents/s3/core/engine.py#L60`](https://github.com/simular-ai/Agent-S/blob/0fff0e634869dab72027435b4586e62300185d41/gui_agents/s3/core/engine.py#L60), [`#L244`](https://github.com/simular-ai/Agent-S/blob/0fff0e634869dab72027435b4586e62300185d41/gui_agents/s3/core/engine.py#L244)
8. Legato loop and defaults: [`legato/harness.py`](../../legato/harness.py)
9. Sandbox adapters: [`legato/env.py`](../../legato/env.py)
10. Ready signal: [`legato/settle.py`](../../legato/settle.py)
11. Call timeout and retry: [`legato/rl/policy.py`](../../legato/rl/policy.py)
12. S3 paper: [arXiv 2510.02250](https://arxiv.org/abs/2510.02250), Table 2. Simular's license: [`LICENSE`](https://github.com/simular-ai/Agent-S/blob/0fff0e634869dab72027435b4586e62300185d41/LICENSE)
13. Why there is no head-to-head yet: [Benchmarks](../benchmarks.md#what-these-results-do-not-show)
