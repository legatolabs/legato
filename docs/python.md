# Terminal and Python

Both use the model and desktop that `legato setup` saved ([setup](setup.md)), or the ones you name.

## Terminal

```bash
legato run "Find the next Caltrain from SF to Palo Alto"
```

It prints each turn with a timer, the answer if the task asked a question, and where the time went. It exits 0 when
the task is done.

| flag | what it does |
|---|---|
| `--app NAME` | macOS: work in that app's window in the background (`--app Notes`, `--app "Google Chrome"`) |
| `--screen` | use this whole screen; it moves your real mouse (macOS, Windows, Linux) |
| `--sandbox docker` | a fresh Linux desktop container (needs Docker); also `e2b`, or your own `file.py:function` ([desktops](sandboxes.md)) |
| `--model M --base-url URL --api-key-env VAR` | another model for this run |
| `--max-steps N` | model turns before it gives up (default 40) |
| `--json` | one JSON object at the end (`done`, `answer`, `actions`, `wall_s`, `usage`), for scripts and agents |
| `--yes` | on this computer, do not ask before risky actions (a typed `rm`, Cmd+Q...) |

With no flag, on a Mac it works in the background in the app the task names ("... in Notes", "... on Spotify"),
else in Safari; elsewhere on the whole screen. `legato setup --desktop docker` (or `screen`) changes that
default.

## Python

```python
import legato

result = legato.run("On Wikipedia, look up the Eiffel Tower and tell me how tall it is")
print(result.done, result.answer)
print(result.report())        # done or failed, the turns, and where the time went (model, screen, actions)
```

`legato.run(task, desktop=None, *, model=None, base_url=None, api_key=None, app=None, max_steps=40, on_step=None)`
opens the desktop, runs the task and closes the desktop. `desktop` is a name (`"background"`, `"screen"`,
`"docker"`, `"e2b"`) or a desktop you opened yourself.

### Choose each part yourself

The whole loop is `agent.run(task, desktop)`:

```python
import os, legato

agent = legato.Harness(model="qwen/qwen3.8-27b", base_url="https://openrouter.ai/api/v1",
                       api_key=os.environ["OPENROUTER_API_KEY"])
desk = legato.local(background="Safari")      # one app's window on a Mac, in the background
result = agent.run("Open example.com and check the page title", desk, max_steps=30,
                   on_step=lambda turn, actions, timing: print(turn, [a.describe() for a in actions if a]))
```

- **Desktops:** `legato.local()` (this screen), `legato.local(background="App")` (macOS),
  `legato.providers.docker(1280, 800)` (returns `(stop, env)`), `legato.attach(Sandbox.create())` (E2B, with
  `pip install "legatolabs[e2b]"`), `legato.attach(shell=run)` (any Linux sandbox you can run a command in),
  `legato.attach(screenshot=..., execute=...)` (anything else). [Desktops](sandboxes.md) has each.
- **`legato.Harness()` with no model** uses the saved one. `legato.agent_for(desk)` returns a Harness set up the way
  the Mac app and the CLI run on that desktop: on a Mac's whole screen, one plan per turn with a shell and checked
  AppleScript beside the screen.
- **Two models:** a strong model plans each action and a small one finds it on the screen. It is how our Windows
  runs work (GPT-6.1 Terra with UI-TARS-1.5-7B):
  `legato.Harness("bytedance/ui-tars-1.5-7b", url, key, coords="pixels", native_tools=False,
  planner=legato.Planner("openai/gpt-6.1-terra", url, key, style="point"))`.
- **Tools beside the screen:** `legato.Harness(..., tools=[legato.tools.bash(env)])` gives the model a shell in that
  desktop; `legato.Tool(name, description, json_schema, handler)` wraps your own function, whose text result goes
  back to the model.
- **The result:** `done`, `failed`, `answer` (what the model said at the end), `actions`, `steps`, `wall_s`,
  `timings` (per turn: model, actions, waiting for the screen, screenshots), `usage` (tokens and the cost the
  provider reports), `error`, `stopped`, and `report()`.
- **Stopping it** on your own screen: move the mouse into a corner, or hold Escape on a Mac. Risky actions wait for
  your yes in the terminal ([what counts](sandboxes.md#this-computer)).
- **Models:** coordinates, thinking models and settings for each are in [models](models.md).

### Write a starting point

`legato init` writes `agent.py`, a working agent with your saved model and desktop, to run as it is or copy into your
own code: `python agent.py "your task"`.

## RL rollouts and benchmarks

`legato rl collect` runs graded rollouts with token logprobs and GRPO groups ([RL rollouts](rl.md)); `legato bench`
runs the office benchmark and head-to-head comparisons ([benchmarks](benchmarks.md)).
