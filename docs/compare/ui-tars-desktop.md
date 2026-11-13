# Legato vs UI-TARS-desktop: which should you use for computer use?

**Short answer.** UI-TARS-desktop is a desktop app and TypeScript SDK that operates your own computer, a browser or an
Android device with ByteDance's UI-TARS and Seed vision models. Legato is a Python harness that drives a sandbox you
attach, with any OpenAI-compatible model that returns tool calls. We have not benchmarked them against each other, so
this page compares architecture only; a same-desktop run needs a custom UI-TARS operator and is deferred.

## At a glance

| | UI-TARS-desktop (`@ui-tars/sdk` and app) | Legato (`legato.Harness`) |
|---|---|---|
| Models | Any OpenAI-compatible `baseURL` [1]; the prompt and parser expect UI-TARS's `Thought:` / `Action: click(start_box=...)` text format [2] | Tool calls, or JSON actions in the reply text [8]; it does not parse UI-TARS's text format |
| Desktops | Your local computer through nut-js; browser, Browserbase and ADB operators; remote computer and browser operators in the app [3] | E2B Desktop, or any sandbox given a screenshot function and an execute function [9]; no local-machine operator |
| Interface | Electron app for Windows and macOS; TypeScript SDK [3][13] | Python library and CLI |
| Wait after action | App: 1 s between turns, settable from 0 to 3 s; SDK: none unless `loopIntervalInMs` is set; `wait` action: 5 s [4] | Waits until the screen stops changing, capped at 2 s per action [10] |
| Screenshots kept | Last 5, a sliding window [5] | Last 8, dropped 4 at a time [8] |
| Actions per turn | Every parsed action [6] | Up to 3, with one screenshot after them [8] |
| Call bounds | 30 s timeout per call; no retries by default; 65,535 output tokens for UI-TARS-1.5, 1,000 otherwise; 100 turns [1][5][6] | 4,096 output tokens with a cut-off note; 60 s timeout; 1 retry; 30 turns by default [8][11] |
| License | Apache-2.0 [7] | Apache-2.0 |
| Status | Active (commit 3ac2cb8, 9 October 2026) | Version 0.1.0 |

## Benchmarks

None. We have not run UI-TARS-desktop or a UI-TARS model through Legato. The two loops expect different model output
formats, so a fair test needs the same desktops behind both: a custom `Operator` for UI-TARS's SDK, and a UI-TARS model
that Legato can parse, or a parser for its format [12].

## What the designs trade

- **Where it runs.** UI-TARS-desktop acts on the machine it runs on, or through the app's remote operators. Legato
  acts on a sandbox, usually remote, and on the machine running Python only if you write an `execute` function for it.
- **Model fit.** UI-TARS's prompt, coordinate factors and parser are built for UI-TARS and Seed models. Legato's
  `computer` tool is built for models that make tool calls, such as the Qwen and Claude models we measured.
- **Cache behaviour.** Past 5 screenshots, the SDK removes the oldest one every turn, so the history changes near its
  start each turn [5]. Legato drops old screenshots in chunks, so a server's prefix cache keeps matching between drops.

## When to use UI-TARS-desktop instead

- **You want an app, not a library,** to automate your own Windows or macOS computer or a browser without code.
- **You run UI-TARS-1.5 or Seed models.** Its SDK speaks their output format; Legato does not, and UI-TARS models are
  untested with Legato.
- **You build in TypeScript,** or need its Android (ADB), browser or Browserbase operators.
- **You want remote computer and browser operators** that the app sets up for you [3].
- **You also want Agent TARS,** the multimodal agent CLI in the same repository.

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

UI-TARS links are to [bytedance/UI-TARS-desktop](https://github.com/bytedance/UI-TARS-desktop) at commit
3ac2cb8d946d78d5ff92ad17b318eaafa18c178c.

1. Model client: [`packages/ui-tars/sdk/src/Model.ts#L106-L121`](https://github.com/bytedance/UI-TARS-desktop/blob/3ac2cb8d946d78d5ff92ad17b318eaafa18c178c/packages/ui-tars/sdk/src/Model.ts#L106-L121) and [`#L264`](https://github.com/bytedance/UI-TARS-desktop/blob/3ac2cb8d946d78d5ff92ad17b318eaafa18c178c/packages/ui-tars/sdk/src/Model.ts#L264)
2. Output format: [`packages/ui-tars/sdk/src/constants.ts#L12-L35`](https://github.com/bytedance/UI-TARS-desktop/blob/3ac2cb8d946d78d5ff92ad17b318eaafa18c178c/packages/ui-tars/sdk/src/constants.ts#L12-L35)
3. Operators and platforms: [`packages/ui-tars/operators`](https://github.com/bytedance/UI-TARS-desktop/tree/3ac2cb8d946d78d5ff92ad17b318eaafa18c178c/packages/ui-tars/operators), [`README.md#L76`](https://github.com/bytedance/UI-TARS-desktop/blob/3ac2cb8d946d78d5ff92ad17b318eaafa18c178c/README.md#L76), [`#L243-L274`](https://github.com/bytedance/UI-TARS-desktop/blob/3ac2cb8d946d78d5ff92ad17b318eaafa18c178c/README.md#L243-L274)
4. Waits: [`apps/ui-tars/src/main/store/setting.ts#L27-L28`](https://github.com/bytedance/UI-TARS-desktop/blob/3ac2cb8d946d78d5ff92ad17b318eaafa18c178c/apps/ui-tars/src/main/store/setting.ts#L27-L28), [`apps/ui-tars/src/renderer/src/pages/settings/Settings.tsx#L56`](https://github.com/bytedance/UI-TARS-desktop/blob/3ac2cb8d946d78d5ff92ad17b318eaafa18c178c/apps/ui-tars/src/renderer/src/pages/settings/Settings.tsx#L56), [`packages/ui-tars/sdk/src/GUIAgent.ts#L424-L428`](https://github.com/bytedance/UI-TARS-desktop/blob/3ac2cb8d946d78d5ff92ad17b318eaafa18c178c/packages/ui-tars/sdk/src/GUIAgent.ts#L424-L428), [`packages/ui-tars/operators/nut-js/src/index.ts#L171-L173`](https://github.com/bytedance/UI-TARS-desktop/blob/3ac2cb8d946d78d5ff92ad17b318eaafa18c178c/packages/ui-tars/operators/nut-js/src/index.ts#L171-L173)
5. Image window and turn limit: [`packages/ui-tars/shared/src/constants/vlm.ts#L6-L7`](https://github.com/bytedance/UI-TARS-desktop/blob/3ac2cb8d946d78d5ff92ad17b318eaafa18c178c/packages/ui-tars/shared/src/constants/vlm.ts#L6-L7), [`packages/ui-tars/sdk/src/utils.ts#L58-L88`](https://github.com/bytedance/UI-TARS-desktop/blob/3ac2cb8d946d78d5ff92ad17b318eaafa18c178c/packages/ui-tars/sdk/src/utils.ts#L58-L88)
6. Action loop and retries: [`GUIAgent.ts#L296`](https://github.com/bytedance/UI-TARS-desktop/blob/3ac2cb8d946d78d5ff92ad17b318eaafa18c178c/packages/ui-tars/sdk/src/GUIAgent.ts#L296), [`#L352`](https://github.com/bytedance/UI-TARS-desktop/blob/3ac2cb8d946d78d5ff92ad17b318eaafa18c178c/packages/ui-tars/sdk/src/GUIAgent.ts#L352)
7. [`LICENSE`](https://github.com/bytedance/UI-TARS-desktop/blob/3ac2cb8d946d78d5ff92ad17b318eaafa18c178c/LICENSE)
8. Legato loop, tool and parser: [`legato/harness.py`](../../legato/harness.py)
9. Sandbox adapters: [`legato/env.py`](../../legato/env.py)
10. Ready signal: [`legato/settle.py`](../../legato/settle.py)
11. Call timeout and retry: [`legato/rl/policy.py`](../../legato/rl/policy.py)
12. Why there is no head-to-head yet: [Benchmarks](../benchmarks.md#what-these-results-do-not-show)
13. Electron dependency: [`apps/ui-tars/package.json`](https://github.com/bytedance/UI-TARS-desktop/blob/3ac2cb8d946d78d5ff92ad17b318eaafa18c178c/apps/ui-tars/package.json)
