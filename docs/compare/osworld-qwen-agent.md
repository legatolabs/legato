# Legato vs OSWorld's Qwen agent: which harness should you run an open model on?

**Short answer.** On Qwen3.8-27B, Legato and OSWorld's QwenAgent scaffold took about the same time per task, and
Legato solved more tasks: 71 of 96 rollouts against 53 of 96 over two runs on independent task sets (+21 and +17
points). Use QwenAgent for OSWorld evaluations, where it is the Qwen-CUA scaffold that OSWorld ships; use Legato to run
an open model on your own sandboxes with bounded model calls.

## At a glance

| | OSWorld QwenAgent (`mm_agents/qwen`) | Legato (`legato.Harness`) |
|---|---|---|
| Models | Any OpenAI-compatible endpoint; prompts and XML tool-call parsing written for Qwen [1] | Any OpenAI-compatible chat API that accepts images [8] |
| Desktops | OSWorld VMs (AWS, Azure, Docker, VMware, VirtualBox); actions are pyautogui code run in the VM [2] | E2B Desktop, or any sandbox given a screenshot function and an execute function [9] |
| Wait after action | 0 s, the OSWorld runner's default for this agent [3] | Waits until the screen stops changing, capped at 2 s per action [10] |
| Screenshots kept | 20, folded 10 at a time [4] | 8, dropped 4 at a time [8] |
| Actions per turn | Every tool call in the reply [5] | Up to 3, with one screenshot after them [8] |
| Call bounds | 32,768 output tokens; 130 s timeout; up to 5 attempts; 50 steps in the runner [3][6] | 4,096 output tokens with a cut-off note; 60 s timeout; 1 retry; 30 turns by default [8][11] |
| License | Apache-2.0 [7] | Apache-2.0 |
| Status | Active (commit 9466ba2, 11 November 2026) | Version 0.1.0 |

## What we measured

Legato Office Bench: 24 LibreOffice Calc-to-Impress tasks per run (fresh data in run 2), 2 attempts each, 25-step
cap, real Ubuntu desktops, both arms at once, Qwen3.8-27B through OpenRouter [12]. QwenAgent's prompting, history and
parsing ran as shipped; its model calls went through Legato's HTTP client
([`legato/rl/collect.py`](../../legato/rl/collect.py)).

| | Run 1 (H2H-1) | Run 2 (H2H-2) |
|---|---|---|
| Agent time, Legato / QwenAgent | 0.424 [0.299, 0.622], not a speed result (below) | 0.953 [0.798, 1.134] |
| Success, Legato | 36/48 | 35/48 |
| Success, QwenAgent | 26/48 | 27/48 |
| Difference | +21 points | +17 points |

- **Speed.** In run 1, four QwenAgent rollouts ran to its 32,768-token output limit, at 12 to 16 minutes of model time
  each, and drove the ratio. In run 2 its longest reply was 2,367 tokens and the times were about the same. Runaways
  are random, so Legato's per-call bound is a robustness point, not a speed claim.
- **Per call (run 2).** Legato sent about half the input (12.7k vs 24.0k tokens) and made fewer calls (19.7 vs 23.0
  per task), but each call was not faster (5.06 s vs 4.76 s) because it wrote more output (257 vs 169 tokens).
  It also waited 0.63 s per action for the screen, about 11 s per task, where QwenAgent waits 0 s.
- **Harness version.** Run 1 used Legato's earlier 1,024-token budget (about 22% of turns cut off with no action).
  Run 2 used the current 4,096 tokens and cut-off note (0.1%).

Limits of this result:
- Office tasks, not OSWorld. QwenAgent was built for OSWorld; we have not run this pair on OSWorld tasks.
- The 25-step cap is half the runner's default of 50. In run 1, QwenAgent hit the cap in 31 of 48 rollouts and Legato
  in 13. We have not tested whether a higher cap changes the gap.
- The benchmark ran `mm_agents/qwen` at commit 8903ebe, and the code links below point there. The cited files are
  identical at the current commit, 9466ba2.

## When to use QwenAgent instead

- **OSWorld evaluation.** It ships in OSWorld's repository as the scaffold for Qwen models, and its README cites the
  Qwen-CUA paper [1]. Use it when your numbers must be comparable with other runs of that scaffold.
- **Its own features:** expanded actions, DashScope `enable_thinking` handling, an infeasible-task fallback [1], and
  OSWorld's VM providers with no adapter to write.
- **No wait and a longer history:** 0 s per action and 20 screenshots instead of 8.
- **Long reasoning.** Its 32,768-token budget allows replies that Legato would cut at 4,096. In Legato, raise
  `max_tokens` if your model needs that.

## Code

```bash
pip install legatolabs
pip install "legatolabs[e2b]"   # E2B Desktop
```

```python
import os
import legato
from e2b_desktop import Sandbox

agent = legato.Harness(model="qwen/qwen3.8-27b", base_url="https://openrouter.ai/api/v1",
                       api_key=os.environ["OPENROUTER_API_KEY"])   # or your vLLM server's /v1 URL
desk = Sandbox.create()                                          # needs E2B_API_KEY
try:
    result = agent.run("Add a Total row under the table and save", legato.attach(desk))
    print(result.report())
finally:
    desk.kill()
```

Non-Claude models get 0 to 1000 coordinates by default (`coords="norm1000"`), the Qwen-VL convention.

## Sources

OSWorld links are to [xlang-ai/OSWorld](https://github.com/xlang-ai/OSWorld) at commit
9466ba2c7e19ad6dea076ad9cf27a2eca6f11336.

1. Scaffold and its features: [`mm_agents/qwen/main.py#L27-L36`](https://github.com/xlang-ai/OSWorld/blob/8903ebeb0efdf74331bd7dc37ed91ccbbff22317/mm_agents/qwen/main.py#L27-L36), [`#L81`](https://github.com/xlang-ai/OSWorld/blob/8903ebeb0efdf74331bd7dc37ed91ccbbff22317/mm_agents/qwen/main.py#L81), [`mm_agents/qwen/README.md`](https://github.com/xlang-ai/OSWorld/blob/8903ebeb0efdf74331bd7dc37ed91ccbbff22317/mm_agents/qwen/README.md)
2. Providers: [`README.md`](https://github.com/xlang-ai/OSWorld/blob/9466ba2c7e19ad6dea076ad9cf27a2eca6f11336/README.md); per-action pause: [`desktop_env/desktop_env.py#L491-L530`](https://github.com/xlang-ai/OSWorld/blob/8903ebeb0efdf74331bd7dc37ed91ccbbff22317/desktop_env/desktop_env.py#L491-L530)
3. Runner defaults: [`scripts/python/run_multienv_qwen.py#L47-L48`](https://github.com/xlang-ai/OSWorld/blob/8903ebeb0efdf74331bd7dc37ed91ccbbff22317/scripts/python/run_multienv_qwen.py#L47-L48) and [`#L72`](https://github.com/xlang-ai/OSWorld/blob/8903ebeb0efdf74331bd7dc37ed91ccbbff22317/scripts/python/run_multienv_qwen.py#L72)
4. Folding: [`main.py#L53-L54`](https://github.com/xlang-ai/OSWorld/blob/8903ebeb0efdf74331bd7dc37ed91ccbbff22317/mm_agents/qwen/main.py#L53-L54) and [`history.py#L7-L12`](https://github.com/xlang-ai/OSWorld/blob/8903ebeb0efdf74331bd7dc37ed91ccbbff22317/mm_agents/qwen/history.py#L7-L12)
5. Tool calls: [`actions.py#L169-L170`](https://github.com/xlang-ai/OSWorld/blob/8903ebeb0efdf74331bd7dc37ed91ccbbff22317/mm_agents/qwen/actions.py#L169-L170)
6. Token limit, timeout, retries: [`main.py#L44`](https://github.com/xlang-ai/OSWorld/blob/8903ebeb0efdf74331bd7dc37ed91ccbbff22317/mm_agents/qwen/main.py#L44), [`client.py#L9`](https://github.com/xlang-ai/OSWorld/blob/8903ebeb0efdf74331bd7dc37ed91ccbbff22317/mm_agents/qwen/client.py#L9), [`client.py#L69-L78`](https://github.com/xlang-ai/OSWorld/blob/8903ebeb0efdf74331bd7dc37ed91ccbbff22317/mm_agents/qwen/client.py#L69-L78)
7. [`LICENSE`](https://github.com/xlang-ai/OSWorld/blob/8903ebeb0efdf74331bd7dc37ed91ccbbff22317/LICENSE)
8. Legato loop and defaults: [`legato/harness.py`](../../legato/harness.py)
9. Sandbox adapters: [`legato/env.py`](../../legato/env.py)
10. Ready signal: [`legato/settle.py`](../../legato/settle.py)
11. Call timeout and retry: [`legato/rl/policy.py`](../../legato/rl/policy.py)
12. Method, runs H2H-1 and H2H-2 (12 November 2026) and raw data: [Benchmarks](../benchmarks.md)
