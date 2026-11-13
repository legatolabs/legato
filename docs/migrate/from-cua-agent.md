# cua-agent is no longer maintained: how do I move a ComputerAgent loop to Legato?

**Short answer.** Cua marks `cua-agent` as deprecated: it "is no longer maintained by Cua and will not receive updates
or fixes", and its last release, 0.9.1, stays on PyPI [1][2]. Cua's own recommendation is a coding agent connected to
Cua Driver over MCP, or one run inside a sandbox with `sandbox.agents()` [2]; if you want to keep a screenshot-and-click
loop that you control from Python, `legato.Harness` maps closely onto `ComputerAgent` and can be attached to the same
Cua sandbox through two functions. We have not benchmarked cua-agent against Legato.

## At a glance

| | cua-agent 0.9.1 (`ComputerAgent`) | Legato (`legato.Harness`) |
|---|---|---|
| Models | Any model through LiteLLM; a loop is picked by model-name pattern (Claude, OpenAI computer-use-preview, UI-TARS, OpenCUA, Qwen, Gemini and others), and `planner+grounder` strings combine two models [3] | Any OpenAI-compatible chat API that accepts images [12] |
| Desktops | A `cua_sandbox.Sandbox`, or a dict of functions where only `screenshot` is required [4] | E2B Desktop, or any sandbox given a screenshot function and an execute function [13] |
| Wait after action | 0.5 s before each screenshot (`screenshot_delay`) [5] | Waits until the screen stops changing, capped at 2 s per action [14] |
| Screenshots kept | All by default; `only_n_most_recent_images=N` keeps the last N, so one drops per step [6] | Last 8, dropped 4 at a time [12] |
| Actions per turn | Every computer call in the reply, each followed by a screenshot [5] | Up to 3, with one screenshot after them [12] |
| Call bounds | 3 retries on transient errors; no step limit; optional cost budget [7] | 4,096 output tokens with a cut-off note; 60 s timeout; 1 retry; 30 turns by default [12] |
| License | MIT [8] | Apache-2.0 |
| Status | Deprecated; tests, builds and publishing switched off [1] | Version 0.1.0 |

## Concept map

| cua-agent | Legato |
|---|---|
| `ComputerAgent(model="anthropic/...")` (LiteLLM name) | `legato.Harness(model=..., base_url=..., api_key=...)` (OpenAI-compatible endpoint) |
| `tools=[sandbox]` | `env = legato.attach(...)`, passed to `run` |
| `async for result in agent.run(messages)` | `result = agent.run(task, env, max_steps=30)`, synchronous |
| `only_n_most_recent_images=3` | `image_max=8`, `fold=4` |
| `screenshot_delay=0.5` | none: the ready signal replaces it |
| `max_retries=3` | `retries=1`, `step_timeout_s=60.0` |
| `use_prompt_caching=True` | `cache_breakpoints=True` (on by default for Claude) |
| `instructions="..."` | `system="..."` replaces the whole system prompt; it goes through `str.format` with `{w}`, `{h}`, `{coords}` and `{batch}`, so double any other braces |
| `trajectory_dir=`, `callbacks=[...]` | `on_step=fn(turn, actions, timing)`, then `result.actions`, `result.timings`, `result.report()` |
| `max_trajectory_budget=` | no budget stop; `result.usage["cost_usd"]` when the provider reports cost |
| Python function tools, `planner+grounder` models | none: the model gets only the `computer` tool |

## Before and after

Before, as in cua-agent's example and Cua's sandbox guide [9][10]:

```python
import asyncio
from cua_agent import ComputerAgent
from cua_sandbox import Image, Sandbox

async def main():
    async with Sandbox.ephemeral(Image.linux()) as sb:
        agent = ComputerAgent(model="anthropic/claude-sonnet-4-5-20250929", tools=[sb], only_n_most_recent_images=3)
        messages = [{"role": "user", "content": "Add a Total row under the table and save"}]
        async for result in agent.run(messages):
            for item in result["output"]:
                if item["type"] == "message":
                    print(item["content"][0]["text"])

asyncio.run(main())
```

After, on the same Cua sandbox through `cua_sandbox.sync`, Cua's blocking wrapper [11]:

```bash
pip install legatolabs cua-sandbox
```

```python
import os, time
import legato
from cua_sandbox import Image
from cua_sandbox.sync import Sandbox

KEYS = {"return": "enter", "escape": "esc", "prior": "page_up", "next": "page_down", "super": "cmd"}

def cua_env(sb):
    w, h = sb.get_dimensions()
    def execute(a):                                   # a: legato.Action, in screen pixels
        x, y = (a.x, a.y) if a.x is not None else (w // 2, h // 2)
        if a.type == "click": sb.mouse.click(x, y)
        elif a.type == "double_click": sb.mouse.double_click(x, y)
        elif a.type == "right_click": sb.mouse.right_click(x, y)
        elif a.type == "type": sb.keyboard.type(a.text)
        elif a.type == "key":                         # "ctrl+s"; "Tab Tab" is a sequence
            for combo in a.keys.split():
                sb.keyboard.keypress([KEYS.get(k.lower(), k.lower()) for k in combo.split("+")])
        elif a.type == "scroll":                      # in cua, positive scroll_y scrolls up
            n = a.amount or 3
            sb.mouse.scroll(x, y, scroll_x={"right": n, "left": -n}.get(a.direction, 0),
                            scroll_y={"up": n, "down": -n}.get(a.direction, 0))
        elif a.type == "drag": sb.mouse.drag(a.x, a.y, a.x2, a.y2)
        elif a.type == "wait": time.sleep(1.0)
    return legato.attach(screenshot=sb.screenshot, execute=execute)

agent = legato.Harness(model="anthropic/claude-sonnet-5.5", base_url="https://openrouter.ai/api/v1",
                       api_key=os.environ["OPENROUTER_API_KEY"])
with Sandbox.ephemeral(Image.linux()) as sb:
    result = agent.run("Add a Total row under the table and save", cua_env(sb))
    print(result.report())
```

We checked these calls against the Cua source at the commit below but have not run this example end to end. The key
names follow cua-agent's own mapping. Legato waits for the screen from the client side here, one screenshot per poll.
On E2B Desktop the whole adapter is `legato.attach(e2b_desktop.Sandbox.create())`.

## When to use Cua's replacement or cua-agent instead

- **Cua's recommendation** [2]: connect Claude Code, Codex, Cursor or another agent to the Cua Driver MCP server, run a
  coding agent in a sandbox with `sandbox.agents()` or `cua agent run`, and score agents with Cua Bench. Choose this
  for a general coding agent that can also use the screen, maintained by Cua, or to operate apps on your own machine.
- **Stay on cua-agent 0.9.1 for now** if you rely on what Legato lacks: model-specific loops (computer-use-preview,
  UI-TARS, OpenCUA and others), planner-plus-grounder models, Python function tools, a cost budget stop, callbacks
  such as PII anonymization and OpenTelemetry, or an async API. It keeps installing but gets no fixes.
- **Cua Cloud ended on 18 October 2026.** `on="cloud"` now fails; local sandboxes and your own AWS, Google Cloud or
  Modal account still work [2].

## Sources

Cua links are to [trycua/cua](https://github.com/trycua/cua) at commit ee1f7f5faabe9ae0629e894640d0619d5bbcd849.

1. Deprecation notice: [`libs/python/agent/README.md#L3-L4`](https://github.com/trycua/cua/blob/ee1f7f5faabe9ae0629e894640d0619d5bbcd849/libs/python/agent/README.md#L3-L4)
2. Cua's migration guide: [`docs/content/docs/cua-sdk/guides/cua-cloud-ended.mdx#L1-L46`](https://github.com/trycua/cua/blob/ee1f7f5faabe9ae0629e894640d0619d5bbcd849/docs/content/docs/cua-sdk/guides/cua-cloud-ended.mdx#L1-L46), [`#L48-L72`](https://github.com/trycua/cua/blob/ee1f7f5faabe9ae0629e894640d0619d5bbcd849/docs/content/docs/cua-sdk/guides/cua-cloud-ended.mdx#L48-L72), [`#L117-L126`](https://github.com/trycua/cua/blob/ee1f7f5faabe9ae0629e894640d0619d5bbcd849/docs/content/docs/cua-sdk/guides/cua-cloud-ended.mdx#L117-L126)
3. Models: [`libs/python/agent/pyproject.toml#L25`](https://github.com/trycua/cua/blob/ee1f7f5faabe9ae0629e894640d0619d5bbcd849/libs/python/agent/pyproject.toml#L25), [`cua_agent/loops/`](https://github.com/trycua/cua/tree/ee1f7f5faabe9ae0629e894640d0619d5bbcd849/libs/python/agent/cua_agent/loops), [`loops/composed_grounded.py#L123`](https://github.com/trycua/cua/blob/ee1f7f5faabe9ae0629e894640d0619d5bbcd849/libs/python/agent/cua_agent/loops/composed_grounded.py#L123)
4. Computers: [`cua_agent/computers/__init__.py#L24-L53`](https://github.com/trycua/cua/blob/ee1f7f5faabe9ae0629e894640d0619d5bbcd849/libs/python/agent/cua_agent/computers/__init__.py#L24-L53), [`computers/custom.py#L17-L29`](https://github.com/trycua/cua/blob/ee1f7f5faabe9ae0629e894640d0619d5bbcd849/libs/python/agent/cua_agent/computers/custom.py#L17-L29)
5. Constructor and action handling: [`cua_agent/agent.py#L256-L275`](https://github.com/trycua/cua/blob/ee1f7f5faabe9ae0629e894640d0619d5bbcd849/libs/python/agent/cua_agent/agent.py#L256-L275), [`#L740-L792`](https://github.com/trycua/cua/blob/ee1f7f5faabe9ae0629e894640d0619d5bbcd849/libs/python/agent/cua_agent/agent.py#L740-L792)
6. Image retention: [`agent.py#L332-L334`](https://github.com/trycua/cua/blob/ee1f7f5faabe9ae0629e894640d0619d5bbcd849/libs/python/agent/cua_agent/agent.py#L332-L334), [`callbacks/image_retention.py`](https://github.com/trycua/cua/blob/ee1f7f5faabe9ae0629e894640d0619d5bbcd849/libs/python/agent/cua_agent/callbacks/image_retention.py)
7. Retries, loop end, budget: [`agent.py#L213-L246`](https://github.com/trycua/cua/blob/ee1f7f5faabe9ae0629e894640d0619d5bbcd849/libs/python/agent/cua_agent/agent.py#L213-L246), [`#L343-L348`](https://github.com/trycua/cua/blob/ee1f7f5faabe9ae0629e894640d0619d5bbcd849/libs/python/agent/cua_agent/agent.py#L343-L348), [`#L936-L940`](https://github.com/trycua/cua/blob/ee1f7f5faabe9ae0629e894640d0619d5bbcd849/libs/python/agent/cua_agent/agent.py#L936-L940)
8. [`LICENSE.md`](https://github.com/trycua/cua/blob/ee1f7f5faabe9ae0629e894640d0619d5bbcd849/LICENSE.md)
9. [`libs/python/agent/example.py`](https://github.com/trycua/cua/blob/ee1f7f5faabe9ae0629e894640d0619d5bbcd849/libs/python/agent/example.py)
10. [`docs/content/docs/cua-sdk/guides/own-harness.mdx`](https://github.com/trycua/cua/blob/ee1f7f5faabe9ae0629e894640d0619d5bbcd849/docs/content/docs/cua-sdk/guides/own-harness.mdx) and [`README.md#L172`](https://github.com/trycua/cua/blob/ee1f7f5faabe9ae0629e894640d0619d5bbcd849/README.md#L172)
11. Sandbox API: [`cua_sandbox/sync/__init__.py`](https://github.com/trycua/cua/blob/ee1f7f5faabe9ae0629e894640d0619d5bbcd849/libs/python/cua-sandbox/cua_sandbox/sync/__init__.py), [`interfaces/mouse.py`](https://github.com/trycua/cua/blob/ee1f7f5faabe9ae0629e894640d0619d5bbcd849/libs/python/cua-sandbox/cua_sandbox/interfaces/mouse.py), [`interfaces/keyboard.py`](https://github.com/trycua/cua/blob/ee1f7f5faabe9ae0629e894640d0619d5bbcd849/libs/python/cua-sandbox/cua_sandbox/interfaces/keyboard.py), key names in [`cua_agent/computers/sandbox.py#L52-L97`](https://github.com/trycua/cua/blob/ee1f7f5faabe9ae0629e894640d0619d5bbcd849/libs/python/agent/cua_agent/computers/sandbox.py#L52-L97)
12. Legato loop and defaults: [`legato/harness.py`](../../legato/harness.py), [`legato/rl/policy.py`](../../legato/rl/policy.py)
13. Sandbox adapters: [`legato/env.py`](../../legato/env.py), [Sandboxes](../sandboxes.md)
14. Ready signal: [`legato/settle.py`](../../legato/settle.py)
