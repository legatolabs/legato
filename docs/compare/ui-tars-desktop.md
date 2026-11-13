# Legato vs UI-TARS-desktop: which should you use for computer use?

UI-TARS-desktop is a desktop app and TypeScript SDK that operates your own computer, a browser or an Android device
with ByteDance's UI-TARS and Seed vision models. Legato is a Python harness that drives a sandbox you attach, with any
OpenAI-compatible model that returns tool calls.

Not benchmarked against each other; this page compares architecture only.

| | UI-TARS-desktop (`@ui-tars/sdk` and app) | Legato (`legato.Harness`) |
|---|---|---|
| Models | Any OpenAI-compatible `baseURL`; the prompt and parser expect UI-TARS's `Thought:` / `Action: click(start_box=...)` text format | Tool calls, or JSON actions in the reply text; it does not parse UI-TARS's text format |
| Desktops | Your local computer through nut-js; browser, Browserbase and ADB operators; remote computer and browser operators in the app | E2B Desktop, or any sandbox given a screenshot function and an execute function; no local-machine operator |
| Interface | Electron app for Windows and macOS; TypeScript SDK | Python library and CLI |
| Wait after action | App: 1 s between turns, settable from 0 to 3 s; SDK: none unless `loopIntervalInMs` is set; `wait` action: 5 s | Until the screen stops changing, capped at 2 s per action |
| Screenshots kept | Last 5, a sliding window | Last 8, dropped 4 at a time |
| Actions per turn | Every parsed action | Up to 3, with one screenshot after them |
| Call bounds | 30 s timeout per call; no retries by default; 65,535 output tokens for UI-TARS-1.5, 1,000 otherwise; 100 turns | 4,096 output tokens with a cut-off note; 60 s timeout; 1 retry; 30 turns by default |
| License | Apache-2.0 | Apache-2.0 |

## When to use UI-TARS-desktop instead

- **You want an app, not a library,** to automate your own Windows or macOS computer or a browser without code.
- **You run UI-TARS-1.5 or Seed models.** Its SDK speaks their output format; Legato does not, and UI-TARS models are
  untested with Legato.
- **You build in TypeScript,** or need its Android (ADB), browser, Browserbase or remote operators.

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

- [bytedance/UI-TARS-desktop](https://github.com/bytedance/UI-TARS-desktop), commit 3ac2cb8
- [`sdk/src/Model.ts#L106-L121`](https://github.com/bytedance/UI-TARS-desktop/blob/3ac2cb8d946d78d5ff92ad17b318eaafa18c178c/packages/ui-tars/sdk/src/Model.ts#L106-L121),
  [`#L264`](https://github.com/bytedance/UI-TARS-desktop/blob/3ac2cb8d946d78d5ff92ad17b318eaafa18c178c/packages/ui-tars/sdk/src/Model.ts#L264)
- [`sdk/src/constants.ts#L12-L35`](https://github.com/bytedance/UI-TARS-desktop/blob/3ac2cb8d946d78d5ff92ad17b318eaafa18c178c/packages/ui-tars/sdk/src/constants.ts#L12-L35)
- [`packages/ui-tars/operators`](https://github.com/bytedance/UI-TARS-desktop/tree/3ac2cb8d946d78d5ff92ad17b318eaafa18c178c/packages/ui-tars/operators),
  [`README.md#L76`](https://github.com/bytedance/UI-TARS-desktop/blob/3ac2cb8d946d78d5ff92ad17b318eaafa18c178c/README.md#L76),
  [`#L243-L274`](https://github.com/bytedance/UI-TARS-desktop/blob/3ac2cb8d946d78d5ff92ad17b318eaafa18c178c/README.md#L243-L274),
  [`apps/ui-tars/package.json`](https://github.com/bytedance/UI-TARS-desktop/blob/3ac2cb8d946d78d5ff92ad17b318eaafa18c178c/apps/ui-tars/package.json)
- [`store/setting.ts#L27-L28`](https://github.com/bytedance/UI-TARS-desktop/blob/3ac2cb8d946d78d5ff92ad17b318eaafa18c178c/apps/ui-tars/src/main/store/setting.ts#L27-L28),
  [`Settings.tsx#L56`](https://github.com/bytedance/UI-TARS-desktop/blob/3ac2cb8d946d78d5ff92ad17b318eaafa18c178c/apps/ui-tars/src/renderer/src/pages/settings/Settings.tsx#L56),
  [`nut-js/src/index.ts#L171-L173`](https://github.com/bytedance/UI-TARS-desktop/blob/3ac2cb8d946d78d5ff92ad17b318eaafa18c178c/packages/ui-tars/operators/nut-js/src/index.ts#L171-L173)
- [`GUIAgent.ts#L296`](https://github.com/bytedance/UI-TARS-desktop/blob/3ac2cb8d946d78d5ff92ad17b318eaafa18c178c/packages/ui-tars/sdk/src/GUIAgent.ts#L296),
  [`#L352`](https://github.com/bytedance/UI-TARS-desktop/blob/3ac2cb8d946d78d5ff92ad17b318eaafa18c178c/packages/ui-tars/sdk/src/GUIAgent.ts#L352),
  [`#L424-L428`](https://github.com/bytedance/UI-TARS-desktop/blob/3ac2cb8d946d78d5ff92ad17b318eaafa18c178c/packages/ui-tars/sdk/src/GUIAgent.ts#L424-L428)
- [`shared/src/constants/vlm.ts#L6-L7`](https://github.com/bytedance/UI-TARS-desktop/blob/3ac2cb8d946d78d5ff92ad17b318eaafa18c178c/packages/ui-tars/shared/src/constants/vlm.ts#L6-L7),
  [`sdk/src/utils.ts#L58-L88`](https://github.com/bytedance/UI-TARS-desktop/blob/3ac2cb8d946d78d5ff92ad17b318eaafa18c178c/packages/ui-tars/sdk/src/utils.ts#L58-L88)
- [`LICENSE`](https://github.com/bytedance/UI-TARS-desktop/blob/3ac2cb8d946d78d5ff92ad17b318eaafa18c178c/LICENSE)
- Legato: [`harness.py`](../../legato/harness.py), [`env.py`](../../legato/env.py),
  [`settle.py`](../../legato/settle.py), [`rl/policy.py`](../../legato/rl/policy.py)
- [Benchmarks](../benchmarks.md#what-these-results-do-not-show)
