# cua-agent is no longer maintained: how do I move a ComputerAgent loop to Legato?

Cua marks `cua-agent` as deprecated: it "is no longer maintained by Cua and will not receive updates or fixes", and
its last release, 0.9.1, stays on PyPI. `legato.Harness` maps closely onto `ComputerAgent` and attaches to the same Cua
sandbox through two functions.

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

## After

On the same Cua sandbox, through `cua_sandbox.sync`, Cua's blocking wrapper:

```python
# pip install legatolabs cua-sandbox
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

Checked against the Cua source, not run end to end. The key names follow cua-agent's own mapping. On E2B Desktop the
whole adapter is `legato.attach(e2b_desktop.Sandbox.create())`.

## When to use Cua instead

- **Cua's recommendation:** connect Claude Code, Codex, Cursor or another agent to the Cua Driver MCP server, or run a
  coding agent in a sandbox with `sandbox.agents()` or `cua agent run`.
- **Stay on cua-agent 0.9.1** for model-specific loops, planner-plus-grounder models, Python function tools, a cost
  budget stop, callbacks or an async API. It keeps installing but gets no fixes.
- **Cua Cloud ended on 18 October 2026.** `on="cloud"` now fails; local sandboxes and your own AWS, Google Cloud or
  Modal account still work.

## Sources

- [trycua/cua](https://github.com/trycua/cua), commit ee1f7f5
- [`libs/python/agent/README.md#L3-L4`](https://github.com/trycua/cua/blob/ee1f7f5faabe9ae0629e894640d0619d5bbcd849/libs/python/agent/README.md#L3-L4)
- [`cua-cloud-ended.mdx#L1-L46`](https://github.com/trycua/cua/blob/ee1f7f5faabe9ae0629e894640d0619d5bbcd849/docs/content/docs/cua-sdk/guides/cua-cloud-ended.mdx#L1-L46),
  [`#L48-L72`](https://github.com/trycua/cua/blob/ee1f7f5faabe9ae0629e894640d0619d5bbcd849/docs/content/docs/cua-sdk/guides/cua-cloud-ended.mdx#L48-L72),
  [`#L117-L126`](https://github.com/trycua/cua/blob/ee1f7f5faabe9ae0629e894640d0619d5bbcd849/docs/content/docs/cua-sdk/guides/cua-cloud-ended.mdx#L117-L126)
- [`cua_agent/agent.py#L213-L246`](https://github.com/trycua/cua/blob/ee1f7f5faabe9ae0629e894640d0619d5bbcd849/libs/python/agent/cua_agent/agent.py#L213-L246),
  [`#L256-L275`](https://github.com/trycua/cua/blob/ee1f7f5faabe9ae0629e894640d0619d5bbcd849/libs/python/agent/cua_agent/agent.py#L256-L275),
  [`#L332-L334`](https://github.com/trycua/cua/blob/ee1f7f5faabe9ae0629e894640d0619d5bbcd849/libs/python/agent/cua_agent/agent.py#L332-L334),
  [`#L740-L792`](https://github.com/trycua/cua/blob/ee1f7f5faabe9ae0629e894640d0619d5bbcd849/libs/python/agent/cua_agent/agent.py#L740-L792),
  [`#L936-L940`](https://github.com/trycua/cua/blob/ee1f7f5faabe9ae0629e894640d0619d5bbcd849/libs/python/agent/cua_agent/agent.py#L936-L940)
- [`cua_agent/loops/`](https://github.com/trycua/cua/tree/ee1f7f5faabe9ae0629e894640d0619d5bbcd849/libs/python/agent/cua_agent/loops),
  [`callbacks/image_retention.py`](https://github.com/trycua/cua/blob/ee1f7f5faabe9ae0629e894640d0619d5bbcd849/libs/python/agent/cua_agent/callbacks/image_retention.py)
- [`cua_sandbox/sync/__init__.py`](https://github.com/trycua/cua/blob/ee1f7f5faabe9ae0629e894640d0619d5bbcd849/libs/python/cua-sandbox/cua_sandbox/sync/__init__.py),
  [`interfaces/mouse.py`](https://github.com/trycua/cua/blob/ee1f7f5faabe9ae0629e894640d0619d5bbcd849/libs/python/cua-sandbox/cua_sandbox/interfaces/mouse.py),
  [`interfaces/keyboard.py`](https://github.com/trycua/cua/blob/ee1f7f5faabe9ae0629e894640d0619d5bbcd849/libs/python/cua-sandbox/cua_sandbox/interfaces/keyboard.py),
  [`computers/sandbox.py#L52-L97`](https://github.com/trycua/cua/blob/ee1f7f5faabe9ae0629e894640d0619d5bbcd849/libs/python/agent/cua_agent/computers/sandbox.py#L52-L97)
- [`LICENSE.md`](https://github.com/trycua/cua/blob/ee1f7f5faabe9ae0629e894640d0619d5bbcd849/LICENSE.md)
- Legato: [`harness.py`](../../legato/harness.py), [`env.py`](../../legato/env.py),
  [`settle.py`](../../legato/settle.py), [`rl/policy.py`](../../legato/rl/policy.py), [Sandboxes](../sandboxes.md)
