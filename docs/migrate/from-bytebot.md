# Bytebot is archived: how do I move its agent loop to Legato?

Bytebot's GitHub repository was archived on 7 March 2026, and its last commit, 3d37894, is from 11 September 2025.
Keep Bytebot's desktop container (bytebotd) and replace only its agent with `legato.Harness`, which drives the desktop
through bytebotd's `/computer-use` endpoint.

## Concept map

| Bytebot | Legato |
|---|---|
| `POST :9991/tasks {"description": ...}`, queued in Postgres and picked up by a scheduler | `agent.run("...", env)` in your own process |
| `BYTEBOT_LLM_PROXY_URL` (LiteLLM) | `base_url=`: a LiteLLM proxy is an OpenAI-compatible endpoint (untested with Legato) |
| bytebotd `POST :9990/computer-use` | keep it, wrapped by `legato.attach(screenshot=..., execute=...)` |
| `screenshot` returns `{"image": <base64 PNG>}` | `screenshot()` returns PNG bytes |
| `click_mouse` (`coordinates`, `button`, `clickCount`) | `click`, `double_click`, `right_click` |
| `type_text`, `type_keys` | `type`, `key` (`ctrl+s` becomes `["ctrl", "s"]`) |
| `scroll` (`direction`, `scrollCount`), `drag_mouse` (`path`) | `scroll` (`direction`, `amount`), `drag` (`x`, `y` to `x2`, `y2`) |
| Web UI, takeover mode, file uploads, scheduled tasks | none |

## After

```python
# pip install legatolabs httpx
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

Request shapes follow bytebotd's validation classes; not run end to end. Bytebot's README shows
`"coordinate": [500, 300]` for `click_mouse`, but the server expects `coordinates: {x, y}`, `button` and `clickCount`,
as above. A blinking caret keeps the screen changing, so every wait runs to its 2 s cap: Legato turns blinking off
itself only on E2B.

## When to use Bytebot instead

- **You want the whole product:** a web UI to create tasks and watch them, takeover mode, file uploads, password
  managers in a persistent desktop, and a task queue. Legato is a library with none of these.
- **You want Bytebot's tools:** opening named applications, reading and writing files in the desktop, and pasting
  text. Legato offers only clicks, typing, keys, scrolling, drags and waits.

## Sources

- [bytebot-ai/bytebot](https://github.com/bytebot-ai/bytebot) (archived 7 March 2026, as recorded on 12 November 2026),
  last commit [3d37894](https://github.com/bytebot-ai/bytebot/commit/3d37894ce07ef8d8b40adc7fd309ad96c2a71313)
- [`README.md#L109-L124`](https://github.com/bytebot-ai/bytebot/blob/3d37894ce07ef8d8b40adc7fd309ad96c2a71313/README.md#L109-L124),
  [`#L156-L183`](https://github.com/bytebot-ai/bytebot/blob/3d37894ce07ef8d8b40adc7fd309ad96c2a71313/README.md#L156-L183),
  [`#L234`](https://github.com/bytebot-ai/bytebot/blob/3d37894ce07ef8d8b40adc7fd309ad96c2a71313/README.md#L234)
- [`docker/docker-compose.yml#L16-L17`](https://github.com/bytebot-ai/bytebot/blob/3d37894ce07ef8d8b40adc7fd309ad96c2a71313/docker/docker-compose.yml#L16-L17),
  [`#L50-L53`](https://github.com/bytebot-ai/bytebot/blob/3d37894ce07ef8d8b40adc7fd309ad96c2a71313/docker/docker-compose.yml#L50-L53)
- [`proxy/proxy.service.ts#L34-L46`](https://github.com/bytebot-ai/bytebot/blob/3d37894ce07ef8d8b40adc7fd309ad96c2a71313/packages/bytebot-agent/src/proxy/proxy.service.ts#L34-L46)
- [`computer-action.dto.ts`](https://github.com/bytebot-ai/bytebot/blob/3d37894ce07ef8d8b40adc7fd309ad96c2a71313/packages/bytebotd/src/computer-use/dto/computer-action.dto.ts),
  [`computer-use.service.ts#L249-L257`](https://github.com/bytebot-ai/bytebot/blob/3d37894ce07ef8d8b40adc7fd309ad96c2a71313/packages/bytebotd/src/computer-use/computer-use.service.ts#L249-L257),
  [`agent.constants.ts#L146-L170`](https://github.com/bytebot-ai/bytebot/blob/3d37894ce07ef8d8b40adc7fd309ad96c2a71313/packages/bytebot-agent/src/agent/agent.constants.ts#L146-L170)
- [`schema.prisma#L38-L41`](https://github.com/bytebot-ai/bytebot/blob/3d37894ce07ef8d8b40adc7fd309ad96c2a71313/packages/bytebot-agent/prisma/schema.prisma#L38-L41),
  [`agent.scheduler.ts#L22-L41`](https://github.com/bytebot-ai/bytebot/blob/3d37894ce07ef8d8b40adc7fd309ad96c2a71313/packages/bytebot-agent/src/agent/agent.scheduler.ts#L22-L41)
- [`LICENSE`](https://github.com/bytebot-ai/bytebot/blob/3d37894ce07ef8d8b40adc7fd309ad96c2a71313/LICENSE)
- Legato: [`harness.py`](../../legato/harness.py), [`env.py`](../../legato/env.py),
  [`settle.py`](../../legato/settle.py), [`rl/policy.py`](../../legato/rl/policy.py), [Sandboxes](../sandboxes.md)
