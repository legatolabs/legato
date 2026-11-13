# Bytebot is archived: how do I move its agent loop to Legato?

**Short answer.** Bytebot's GitHub repository was archived on 7 March 2026, and its last commit, 3d37894, is from 11
September 2025 [1]. You can keep Bytebot's desktop container (bytebotd) and replace only its agent with
`legato.Harness`, which drives the desktop through bytebotd's `/computer-use` endpoint. We have not benchmarked Bytebot
against Legato.

## At a glance

| | Bytebot agent (`bytebot-agent`) | Legato (`legato.Harness`) |
|---|---|---|
| Models | Anthropic, OpenAI and Gemini SDKs; other models through a LiteLLM proxy (`BYTEBOT_LLM_PROXY_URL`) [2] | Any OpenAI-compatible chat API that accepts images [9] |
| Desktops | Ubuntu 22.04 with XFCE in Docker; bytebotd serves the input API and noVNC on port 9990 [3] | E2B Desktop, or any sandbox given a screenshot function and an execute function, bytebotd included [10] |
| Wait after action | 750 ms before each screenshot [4] | Waits until the screen stops changing, capped at 2 s per action [11] |
| Screenshots kept | All, until token use reaches 75% of the model's context window; then the history is summarized [5] | Last 8, dropped 4 at a time [9] |
| Actions per turn | Every tool call in the reply [5] | Up to 3, with one screenshot after them [9] |
| Call bounds | 16,384 output tokens on Anthropic, 8,192 on OpenAI and the proxy; no explicit request timeout in the agent's code [6] | 4,096 output tokens with a cut-off note; 60 s timeout; 1 retry; 30 turns by default [9] |
| License | Apache-2.0 [7] | Apache-2.0 |
| Status | Archived 7 March 2026 [1] | Version 0.1.0 |

## Concept map

| Bytebot | Legato |
|---|---|
| `POST :9991/tasks {"description": ...}`, queued in Postgres and picked up by a scheduler [12] | `agent.run("...", env)` in your own process |
| `BYTEBOT_LLM_PROXY_URL` (LiteLLM) | `base_url=`: a LiteLLM proxy is an OpenAI-compatible endpoint (untested with Legato) |
| bytebotd `POST :9990/computer-use` | keep it, wrapped by `legato.attach(screenshot=..., execute=...)` |
| `screenshot` returns `{"image": <base64 PNG>}` | `screenshot()` returns PNG bytes |
| `click_mouse` (`coordinates`, `button`, `clickCount`) | `click`, `double_click`, `right_click` |
| `type_text`, `type_keys` | `type`, `key` (`ctrl+s` becomes `["ctrl", "s"]`) |
| `scroll` (`direction`, `scrollCount`), `drag_mouse` (`path`) | `scroll` (`direction`, `amount`), `drag` (`x`, `y` to `x2`, `y2`) |
| Web UI, takeover mode, file uploads, scheduled tasks [3][12] | none |

## Before and after

Before: Bytebot's agent takes a task over REST and runs it [8].

```python
import requests
requests.post("http://localhost:9991/tasks", json={"description": "Add a Total row under the table and save"})
```

After: the same desktop, driven by Legato. Request shapes follow bytebotd's validation classes [8].

```bash
pip install legato httpx
```

```python
import base64, os
import httpx
import legato

DESK = "http://localhost:9990/computer-use"            # bytebotd, from Bytebot's docker-compose
KEYS = {"Prior": "PageUp", "Next": "PageDown"}           # xdotool names that bytebotd spells differently

def post(body):
    r = httpx.post(DESK, json=body, timeout=30)
    r.raise_for_status()
    return r

def screenshot():
    return base64.b64decode(post({"action": "screenshot"}).json()["image"])

def execute(a):                                          # a: legato.Action, in screen pixels
    xy = {"x": a.x, "y": a.y}
    if a.type in ("click", "double_click", "right_click"):
        post({"action": "click_mouse", "coordinates": xy, "button": "right" if a.type == "right_click" else "left",
              "clickCount": 2 if a.type == "double_click" else 1})
    elif a.type == "type":
        post({"action": "type_text", "text": a.text})
    elif a.type == "key":                                # "ctrl+s"; "Tab Tab" is a sequence
        for combo in a.keys.split():
            post({"action": "type_keys", "keys": [KEYS.get(k, k) for k in combo.split("+")]})
    elif a.type == "scroll":
        body = {"action": "scroll", "direction": a.direction, "scrollCount": a.amount or 3}
        if a.x is not None:
            body["coordinates"] = xy
        post(body)
    elif a.type == "drag":
        post({"action": "drag_mouse", "path": [xy, {"x": a.x2, "y": a.y2}], "button": "left"})
    elif a.type == "wait":
        post({"action": "wait", "duration": 1000})       # milliseconds

agent = legato.Harness(model="qwen/qwen3.8-27b", base_url="https://openrouter.ai/api/v1",
                       api_key=os.environ["OPENROUTER_API_KEY"])
result = agent.run("Add a Total row under the table and save", legato.attach(screenshot=screenshot, execute=execute))
print(result.report())
```

We checked these shapes against the Bytebot source but have not run this example end to end. Bytebot's README shows
`"coordinate": [500, 300]` for `click_mouse`; the server code expects `coordinates: {x, y}`, `button` and
`clickCount`, as above. If a blinking caret keeps the screen changing, every wait runs to its 2 s cap: Legato turns
blinking off itself only on E2B, through the XFCE setting `/Net/CursorBlink`.

## When to use Bytebot instead

- **You want the whole product:** a web UI to create tasks and watch them, takeover mode, file uploads, password
  managers in a persistent desktop, and a task queue [3]. Legato is a library with none of these.
- **You want Bytebot's tools:** opening named applications, reading and writing files in the desktop, and pasting
  text. Legato offers only clicks, typing, keys, scrolling, drags and waits.
- **You accept running archived code** that will not get security or dependency fixes.

## Sources

Bytebot links are to [bytebot-ai/bytebot](https://github.com/bytebot-ai/bytebot) at commit
3d37894ce07ef8d8b40adc7fd309ad96c2a71313.

1. Archive status: the [repository page](https://github.com/bytebot-ai/bytebot) (archived 7 March 2026, as recorded on 12 November 2026); last commit [3d37894](https://github.com/bytebot-ai/bytebot/commit/3d37894ce07ef8d8b40adc7fd309ad96c2a71313)
2. Models: [`packages/bytebot-agent/src/anthropic/anthropic.constants.ts`](https://github.com/bytebot-ai/bytebot/blob/3d37894ce07ef8d8b40adc7fd309ad96c2a71313/packages/bytebot-agent/src/anthropic/anthropic.constants.ts), [`proxy/proxy.service.ts#L34-L46`](https://github.com/bytebot-ai/bytebot/blob/3d37894ce07ef8d8b40adc7fd309ad96c2a71313/packages/bytebot-agent/src/proxy/proxy.service.ts#L34-L46), [`docker/docker-compose.yml#L50-L53`](https://github.com/bytebot-ai/bytebot/blob/3d37894ce07ef8d8b40adc7fd309ad96c2a71313/docker/docker-compose.yml#L50-L53)
3. Desktop and features: [`README.md#L109-L124`](https://github.com/bytebot-ai/bytebot/blob/3d37894ce07ef8d8b40adc7fd309ad96c2a71313/README.md#L109-L124), [`#L234`](https://github.com/bytebot-ai/bytebot/blob/3d37894ce07ef8d8b40adc7fd309ad96c2a71313/README.md#L234), [`docker/docker-compose.yml#L16-L17`](https://github.com/bytebot-ai/bytebot/blob/3d37894ce07ef8d8b40adc7fd309ad96c2a71313/docker/docker-compose.yml#L16-L17)
4. Screenshot delay: [`packages/bytebot-agent/src/agent/agent.computer-use.ts#L186-L189`](https://github.com/bytebot-ai/bytebot/blob/3d37894ce07ef8d8b40adc7fd309ad96c2a71313/packages/bytebot-agent/src/agent/agent.computer-use.ts#L186-L189)
5. Summaries and tool loop: [`packages/bytebot-agent/src/agent/agent.processor.ts#L233-L237`](https://github.com/bytebot-ai/bytebot/blob/3d37894ce07ef8d8b40adc7fd309ad96c2a71313/packages/bytebot-agent/src/agent/agent.processor.ts#L233-L237), [`#L309-L311`](https://github.com/bytebot-ai/bytebot/blob/3d37894ce07ef8d8b40adc7fd309ad96c2a71313/packages/bytebot-agent/src/agent/agent.processor.ts#L309-L311)
6. Token limits: [`anthropic/anthropic.service.ts#L50-L64`](https://github.com/bytebot-ai/bytebot/blob/3d37894ce07ef8d8b40adc7fd309ad96c2a71313/packages/bytebot-agent/src/anthropic/anthropic.service.ts#L50-L64), [`openai/openai.service.ts#L54-L58`](https://github.com/bytebot-ai/bytebot/blob/3d37894ce07ef8d8b40adc7fd309ad96c2a71313/packages/bytebot-agent/src/openai/openai.service.ts#L54-L58), [`proxy/proxy.service.ts#L69`](https://github.com/bytebot-ai/bytebot/blob/3d37894ce07ef8d8b40adc7fd309ad96c2a71313/packages/bytebot-agent/src/proxy/proxy.service.ts#L69)
7. [`LICENSE`](https://github.com/bytebot-ai/bytebot/blob/3d37894ce07ef8d8b40adc7fd309ad96c2a71313/LICENSE)
8. APIs: [`README.md#L156-L183`](https://github.com/bytebot-ai/bytebot/blob/3d37894ce07ef8d8b40adc7fd309ad96c2a71313/README.md#L156-L183), [`packages/bytebotd/src/computer-use/dto/computer-action.dto.ts`](https://github.com/bytebot-ai/bytebot/blob/3d37894ce07ef8d8b40adc7fd309ad96c2a71313/packages/bytebotd/src/computer-use/dto/computer-action.dto.ts), [`computer-use.service.ts#L249-L257`](https://github.com/bytebot-ai/bytebot/blob/3d37894ce07ef8d8b40adc7fd309ad96c2a71313/packages/bytebotd/src/computer-use/computer-use.service.ts#L249-L257), valid key names in [`agent.constants.ts#L146-L170`](https://github.com/bytebot-ai/bytebot/blob/3d37894ce07ef8d8b40adc7fd309ad96c2a71313/packages/bytebot-agent/src/agent/agent.constants.ts#L146-L170)
9. Legato loop and defaults: [`legato/harness.py`](../../legato/harness.py), [`legato/rl/policy.py`](../../legato/rl/policy.py)
10. Sandbox adapters: [`legato/env.py`](../../legato/env.py), [Sandboxes](../sandboxes.md)
11. Ready signal: [`legato/settle.py`](../../legato/settle.py)
12. Task queue: [`packages/bytebot-agent/prisma/schema.prisma#L38-L41`](https://github.com/bytebot-ai/bytebot/blob/3d37894ce07ef8d8b40adc7fd309ad96c2a71313/packages/bytebot-agent/prisma/schema.prisma#L38-L41), [`src/agent/agent.scheduler.ts#L22-L41`](https://github.com/bytebot-ai/bytebot/blob/3d37894ce07ef8d8b40adc7fd309ad96c2a71313/packages/bytebot-agent/src/agent/agent.scheduler.ts#L22-L41)
