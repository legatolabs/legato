# Legato vs OSWorld's Qwen agent: which harness should you run an open model on?

On Qwen3.8-27B, Legato and OSWorld's QwenAgent scaffold took about the same time per task, and Legato solved more
tasks: 71 of 96 rollouts against 53 of 96 over two runs on independent task sets. Use QwenAgent for OSWorld
evaluations; use Legato to run an open model on your own sandboxes with bounded model calls.

Run 1: 36/48 vs 26/48. Run 2, on fresh data: 35/48 vs 27/48, with agent time 0.953 [0.798, 1.134] of QwenAgent's.
Run 1 also looked faster, but that gap came from four runaway 32,768-token QwenAgent replies and did not replicate.
Office tasks, not OSWorld; QwenAgent's model calls went through Legato's HTTP client ([benchmarks](../benchmarks.md)).

| | OSWorld QwenAgent (`mm_agents/qwen`) | Legato (`legato.Harness`) |
|---|---|---|
| Models | Any OpenAI-compatible endpoint; prompts and XML tool-call parsing written for Qwen | Any OpenAI-compatible chat API that accepts images |
| Desktops | OSWorld VMs (AWS, Azure, Docker, VMware, VirtualBox); actions are pyautogui code run in the VM | E2B Desktop, or any sandbox given a screenshot function and an execute function |
| Wait after action | 0 s, the OSWorld runner's default for this agent | Until the screen stops changing, capped at 2 s per action |
| Screenshots kept | 20, folded 10 at a time | 8, dropped 4 at a time |
| Actions per turn | Every tool call in the reply | Up to 3, with one screenshot after them |
| Call bounds | 32,768 output tokens; 130 s timeout; up to 5 attempts; 50 steps in the runner | 4,096 output tokens with a cut-off note; 60 s timeout; 1 retry; 30 turns by default |
| License | Apache-2.0 | Apache-2.0 |

## When to use QwenAgent instead

- **OSWorld evaluation.** It is the scaffold OSWorld ships for Qwen models. Use it when your numbers must be
  comparable with other runs of it.
- **Its own features:** expanded actions, DashScope `enable_thinking` handling, an infeasible-task fallback, and
  OSWorld's VM providers with no adapter to write.
- **Long reasoning.** It allows 32,768-token replies; Legato cuts at 4,096 unless you raise `max_tokens`.

## Code

```python
# pip install "legatolabs[e2b]"
import os
import legato
from e2b_desktop import Sandbox

agent = legato.Harness(model="qwen/qwen3.8-27b", base_url="https://openrouter.ai/api/v1",
                       api_key=os.environ["OPENROUTER_API_KEY"])   # or your vLLM server's /v1 URL
# non-Claude models get 0 to 1000 coordinates by default (coords="norm1000"), the Qwen-VL convention
desk = Sandbox.create()                                          # needs E2B_API_KEY
try:
    result = agent.run("Add a Total row under the table and save", legato.attach(desk))
    print(result.report())
finally:
    desk.kill()
```

## Sources

- [xlang-ai/OSWorld](https://github.com/xlang-ai/OSWorld), benchmarked at commit 8903ebe
- [`mm_agents/qwen/README.md`](https://github.com/xlang-ai/OSWorld/blob/8903ebeb0efdf74331bd7dc37ed91ccbbff22317/mm_agents/qwen/README.md)
- [`main.py#L27-L36`](https://github.com/xlang-ai/OSWorld/blob/8903ebeb0efdf74331bd7dc37ed91ccbbff22317/mm_agents/qwen/main.py#L27-L36),
  [`#L44`](https://github.com/xlang-ai/OSWorld/blob/8903ebeb0efdf74331bd7dc37ed91ccbbff22317/mm_agents/qwen/main.py#L44),
  [`#L53-L54`](https://github.com/xlang-ai/OSWorld/blob/8903ebeb0efdf74331bd7dc37ed91ccbbff22317/mm_agents/qwen/main.py#L53-L54),
  [`#L81`](https://github.com/xlang-ai/OSWorld/blob/8903ebeb0efdf74331bd7dc37ed91ccbbff22317/mm_agents/qwen/main.py#L81)
- [`history.py#L7-L12`](https://github.com/xlang-ai/OSWorld/blob/8903ebeb0efdf74331bd7dc37ed91ccbbff22317/mm_agents/qwen/history.py#L7-L12)
- [`actions.py#L169-L170`](https://github.com/xlang-ai/OSWorld/blob/8903ebeb0efdf74331bd7dc37ed91ccbbff22317/mm_agents/qwen/actions.py#L169-L170)
- [`client.py#L9`](https://github.com/xlang-ai/OSWorld/blob/8903ebeb0efdf74331bd7dc37ed91ccbbff22317/mm_agents/qwen/client.py#L9),
  [`#L69-L78`](https://github.com/xlang-ai/OSWorld/blob/8903ebeb0efdf74331bd7dc37ed91ccbbff22317/mm_agents/qwen/client.py#L69-L78)
- [`run_multienv_qwen.py#L47-L48`](https://github.com/xlang-ai/OSWorld/blob/8903ebeb0efdf74331bd7dc37ed91ccbbff22317/scripts/python/run_multienv_qwen.py#L47-L48),
  [`#L72`](https://github.com/xlang-ai/OSWorld/blob/8903ebeb0efdf74331bd7dc37ed91ccbbff22317/scripts/python/run_multienv_qwen.py#L72)
- [`desktop_env.py#L491-L530`](https://github.com/xlang-ai/OSWorld/blob/8903ebeb0efdf74331bd7dc37ed91ccbbff22317/desktop_env/desktop_env.py#L491-L530)
- [`README.md`](https://github.com/xlang-ai/OSWorld/blob/9466ba2c7e19ad6dea076ad9cf27a2eca6f11336/README.md),
  [`LICENSE`](https://github.com/xlang-ai/OSWorld/blob/8903ebeb0efdf74331bd7dc37ed91ccbbff22317/LICENSE)
- Legato: [`harness.py`](../../legato/harness.py), [`env.py`](../../legato/env.py),
  [`settle.py`](../../legato/settle.py), [`rl/policy.py`](../../legato/rl/policy.py),
  [`rl/collect.py`](../../legato/rl/collect.py)
- [Benchmarks](../benchmarks.md)
