# Legato vs Anthropic's computer-use demo: which loop should you use?

**Short answer.** Use Anthropic's computer-use demo if you want Claude with bash and file-editing tools, Anthropic's
own computer tool versions, or Bedrock and Vertex, in a Docker desktop you can watch. Use Legato for the same
screenshot-and-click loop with less fixed waiting, on a sandbox you already run, or with a non-Claude model. On 24
office tasks with Claude Sonnet 5.5, Legato took 0.678 of the reference loop's agent time (95% CI 0.604 to 0.757;
1.48x faster), with 48/48 successes in both arms, in one run against our re-implementation of the demo's loop.

## At a glance

| | Anthropic computer-use demo | Legato (`legato.Harness`) |
|---|---|---|
| Models | Claude only: Anthropic API, Bedrock or Vertex [1] | Any OpenAI-compatible chat API that accepts images; Claude measured through OpenRouter [8] |
| Desktop | Ubuntu in Docker; the loop runs inside the container it controls, one session at a time [2] | E2B Desktop, or any sandbox given a screenshot function and an execute function [9] |
| Wait after action | 2.0 s sleep before every screenshot [4] | Waits until the screen stops changing, capped at 2 s per action (longer for `wait`) [10] |
| Screenshots kept | All on the Anthropic API (caching turns truncation off); last 3 by default on Bedrock and Vertex [5] | Last 8; older ones dropped 4 at a time so the cached prefix holds [8] |
| Actions per turn | Every tool call in the reply, with a screenshot after each [6] | Up to 3, with one screenshot after them [8] |
| Call bounds | 4,096 output tokens; 4 SDK retries on the Anthropic API; no explicit timeout; no step limit [6] | 4,096 output tokens with a cut-off note; 60 s timeout; 1 retry; 30 turns by default [8][11] |
| License | MIT [7] | Apache-2.0 |
| Status | Active (commit 4a4224b, 6 November 2026) | Version 0.1.0 |

## What we measured

Legato Office Bench: 24 tasks that move data from LibreOffice Calc into Impress, 2 attempts each, 25-step cap, real
Ubuntu desktops, both arms at once on one host, Claude Sonnet 5.5 through OpenRouter, 1366x768 screenshots and prompt
caching in both arms [12].

| Per task | Legato | Reference loop |
|---|---|---|
| Agent time (mean) | 65.7 s | 96.9 s |
| Model time | 42.4 s | 44.1 s |
| Waiting for the screen | 14.0 s | 44.5 s |
| Model calls | 14.0 | 10.6 |
| Input tokens per call | 8,603 | 18,667 |
| Success | 48/48 | 48/48 |
| OpenRouter cost | $0.126 | $0.137 |

Almost all of the saving is fixed waiting. Legato makes more calls, but each is smaller and shorter (3.0 s vs 4.2 s).

Limits of this result:
- The reference arm is `legato.Harness.reference`, a re-implementation. It keeps a text history instead of
  `tool_result` blocks, is given the first screenshot (this favours the reference), offers only the computer tool,
  and caches through OpenRouter's top-level `cache_control` instead of per-turn breakpoints. We have not run the
  unmodified demo in this benchmark.
- One run, office tasks only. The pre-set "substantially faster" bar (ratio 0.67 or less) was missed by 0.008.
- This run used Legato's earlier 1,024-token output budget. The current default (4,096 tokens and a cut-off note) has
  not been re-measured on Claude.

## When to use Anthropic's demo instead

- **You need a shell or file editing.** The demo gives Claude bash and `str_replace_based_edit_tool` [3]; Legato
  offers only the `computer` tool.
- **You want Anthropic's computer tool itself.** The demo sends Anthropic-defined tool versions, up to
  `computer_toolset_20260801`, and Anthropic's thinking modes. Legato sends its own `computer` function in OpenAI
  format, with Claude-style action names such as `left_click` and `key`.
- **You call Claude on Bedrock or Vertex.** Legato needs an OpenAI-compatible endpoint.
- **You want a reference to read and modify,** with a Streamlit chat and a VNC view of the desktop.
- **You want Anthropic's own fix for the same costs.** The `computer-use-best-practices` quickstart (macOS only) adds
  a `computer_batch` tool and prunes screenshots at intervals so the cache holds [13]. We did not compare against it.

## Code

```bash
pip install legato
pip install "legato[e2b]"   # E2B Desktop
```

```python
import os
import legato
from e2b_desktop import Sandbox

agent = legato.Harness(model="anthropic/claude-sonnet-5.5", base_url="https://openrouter.ai/api/v1",
                       api_key=os.environ["OPENROUTER_API_KEY"])
desk = Sandbox.create()                     # needs E2B_API_KEY
try:
    result = agent.run("Add a Total row under the table and save", legato.attach(desk))
    print(result.report())                  # model, action, wait and screenshot time
finally:
    desk.kill()
```

`legato.Harness.reference(...)` takes the same arguments and runs the reference loop. To reproduce its timing, give it
a desktop adapter that sleeps 2 s per action.

## Sources

Anthropic links are to [anthropics/claude-quickstarts](https://github.com/anthropics/claude-quickstarts) at commit
4a4224bdf0e9cfb3332167b69c90457403813625.

1. Providers: [`computer_use_demo/loop.py#L124-L130`](https://github.com/anthropics/claude-quickstarts/blob/4a4224bdf0e9cfb3332167b69c90457403813625/computer-use-demo/computer_use_demo/loop.py#L124-L130)
2. Container and single session: [`computer-use-demo/README.md#L34`](https://github.com/anthropics/claude-quickstarts/blob/4a4224bdf0e9cfb3332167b69c90457403813625/computer-use-demo/README.md#L34)
3. Tool groups: [`computer_use_demo/tools/groups.py#L36-L63`](https://github.com/anthropics/claude-quickstarts/blob/4a4224bdf0e9cfb3332167b69c90457403813625/computer-use-demo/computer_use_demo/tools/groups.py#L36-L63)
4. Screenshot delay: [`tools/computer.py#L99`](https://github.com/anthropics/claude-quickstarts/blob/4a4224bdf0e9cfb3332167b69c90457403813625/computer-use-demo/computer_use_demo/tools/computer.py#L99) and [`#L273-L274`](https://github.com/anthropics/claude-quickstarts/blob/4a4224bdf0e9cfb3332167b69c90457403813625/computer-use-demo/computer_use_demo/tools/computer.py#L273-L274)
5. Image truncation: [`loop.py#L132-L139`](https://github.com/anthropics/claude-quickstarts/blob/4a4224bdf0e9cfb3332167b69c90457403813625/computer-use-demo/computer_use_demo/loop.py#L132-L139) and [`streamlit.py#L208-L209`](https://github.com/anthropics/claude-quickstarts/blob/4a4224bdf0e9cfb3332167b69c90457403813625/computer-use-demo/computer_use_demo/streamlit.py#L208-L209)
6. Token budget, retries, tool loop: [`loop.py#L101`](https://github.com/anthropics/claude-quickstarts/blob/4a4224bdf0e9cfb3332167b69c90457403813625/computer-use-demo/computer_use_demo/loop.py#L101), [`#L125`](https://github.com/anthropics/claude-quickstarts/blob/4a4224bdf0e9cfb3332167b69c90457403813625/computer-use-demo/computer_use_demo/loop.py#L125), [`#L202-L239`](https://github.com/anthropics/claude-quickstarts/blob/4a4224bdf0e9cfb3332167b69c90457403813625/computer-use-demo/computer_use_demo/loop.py#L202-L239)
7. [`computer-use-demo/LICENSE`](https://github.com/anthropics/claude-quickstarts/blob/4a4224bdf0e9cfb3332167b69c90457403813625/computer-use-demo/LICENSE)
8. Legato loop and defaults: [`legato/harness.py`](../../legato/harness.py)
9. Sandbox adapters: [`legato/env.py`](../../legato/env.py)
10. Ready signal: [`legato/settle.py`](../../legato/settle.py)
11. Call timeout and retry: [`legato/rl/policy.py`](../../legato/rl/policy.py)
12. Method, run H2H-1 (12 November 2026) and raw data: [Benchmarks](../benchmarks.md)
13. Best-practices quickstart: [`computer-use-best-practices/README.md`](https://github.com/anthropics/claude-quickstarts/blob/4a4224bdf0e9cfb3332167b69c90457403813625/computer-use-best-practices/README.md) and [`constants.py#L149-L157`](https://github.com/anthropics/claude-quickstarts/blob/4a4224bdf0e9cfb3332167b69c90457403813625/computer-use-best-practices/constants.py#L149-L157)
