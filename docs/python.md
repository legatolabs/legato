# Terminal and Python

Both use the model and desktop that `legato setup` saved ([setup](setup.md)), or the ones you name.

## Terminal

```bash
legato run "Find the next Caltrain from SF to Palo Alto"
```

It prints each turn with a timer, the answer if the task asked a question, and where the time went. It exits 0 when
the task is done, 2 when it could not run (a setup, model or desktop error) and 1 otherwise.

| flag | what it does |
|---|---|
| `--app NAME` | macOS: work in that app's window in the background (`--app Notes`, `--app Safari`) |
| `--screen` | use this whole screen; it moves your real mouse (macOS, Windows, Linux) |
| `--sandbox docker` | a fresh Linux desktop container (needs Docker); also `e2b`, or your own `file.py:function` ([desktops](sandboxes.md)) |
| `--model M --base-url URL --api-key-env VAR` | another model for this run |
| `--max-steps N` | model turns before it gives up (default 40) |
| `--timeout S` | end the run after S seconds |
| `--json` | one JSON object at the end (`status`, `done`, `answer`, `error`, `actions`, `wall_s`, `usage`), for scripts and agents |
| `--yes` | on this computer, do not ask before risky actions (a typed `rm`, Cmd+Q...) |

With no flag, on a Mac it works in the background in the app the task names ("... in Notes", "... on Spotify"),
else in Safari; elsewhere on the whole screen. `legato setup --desktop docker` (or `screen`) changes that
default.

## Python

```python
import legato

result = legato.run("On Wikipedia, look up the Eiffel Tower and tell me how tall it is")
if result.status == "done":   # the check for success: `failed` is only "the model called fail"
    print(result.answer)
else:
    print(result.status, result.error)
print(result.report())        # how it ended, the model calls, the cost, and where the time went
```

`legato.run(task, desktop=None, *, model=None, base_url=None, api_key=None, app=None, max_steps=40, on_step=None,
confirm="auto", timeout_s=None, cancel=None, max_cost_usd=None, record_dir=None)` opens the desktop, runs the task
and closes the desktop. `desktop` is a name (`"background"`, `"screen"`, `"docker"`, `"e2b"`) or a desktop you opened
yourself. It returns a result for every way a run can end, model and desktop errors included; only a missing model
or a bad argument (a `desktop` that names none) raises.

`result.status` (typed `legato.RunStatus`) says how the run ended. Check `result.status == "done"`, not
`not result.failed`: `failed` is `False` for `max_steps`, `timeout`, `max_cost` and `error` too. For every status
but `done`, `result.error` says why.

| status | what happened | what to do |
|---|---|---|
| `done` | the model called done | read `answer` |
| `failed` | the model called fail (it judged the task impossible), or the desktop refused the same action 3 times, or 6 actions in all | `error` has the model's reason (also in `answer`) or the refusals that ended it; reword the task, or give it a desktop that can (`--screen`) |
| `max_steps` | `max_steps` ran out first | raise `max_steps`, or split the task |
| `timeout` | `timeout_s` ran out | raise `timeout_s`; `actions` shows how far it got |
| `max_cost` | the reported cost reached `max_cost_usd` | raise it; `usage["cost_usd"]` is what was spent |
| `cancelled` | your `cancel` event was set | nothing |
| `stopped` | the person at the computer stopped it | nothing; their choice |
| `error` | a model call failed (`HTTP 401: ...`, a timeout) or the desktop did (`the desktop failed: ...`) | fix the key, model or desktop that `error` names, then retry; a refused key's error names where the key came from (`$OPENROUTER_API_KEY`, the one `legato setup` saved), never the key |

- **`timeout_s`** is a deadline for the whole call, opening the desktop included: an app's launch, a docker desktop
  (image pull included) and E2B's create request stop at the time left, and so does each model call. It is checked
  between steps, so a run ends a little after it: the action in progress and the wait for the screen after it
  finish first. Your own `--sandbox` function and the first screenshot can't be cut short. An app Legato just opened
  gets the rest of its launch time for its first window, so a cold start isn't an error. **`cancel`** is a
  `threading.Event`: set it from another thread and the run ends before its next model call. Both return what was
  done so far, usage included.
- **`max_cost_usd`** ends the run once the cost its model calls report reaches it. It is checked between model
  turns, so a run can pass it by one turn, and it counts only providers that report cost (OpenRouter does; a
  server that reports none never reaches the limit, so pair it with `max_steps` or `timeout_s`).
- **`record_dir`** is a folder (made if missing) that gets `start.png` and the screen after each turn as
  `turn-001.png`, `turn-002.png`, ...; `result.trace` names each one.
- **`confirm`** decides risky actions (keys such as Cmd+Delete or Cmd+Q, a typed `rm` or `sudo`:
  [the list](sandboxes.md#this-computer)) on this computer (`"background"`, `"screen"`); other desktops ignore it.
  `"auto"` asks yes/no in the terminal when there is one; **in an unattended run (a server, a job) `legato.run`
  refuses them**, and the model is told they were not allowed. A function `confirm(description) -> bool` decides each
  one. `None` runs them all without asking; `legato.safety.deny` refuses them all, even at a terminal.
  (`legato.local()`, `legato run` and the MCP server keep `"auto"` as asking at a terminal and running them
  unattended.)

### Choose each part yourself

The whole loop is `agent.run(task, desktop)`:

```python
import os, legato

agent = legato.Harness(model="qwen/qwen3.8-27b", base_url="https://openrouter.ai/api/v1",
                       api_key=os.environ["OPENROUTER_API_KEY"])
desk = legato.local()                         # this whole screen (macOS, Windows, Linux)
# desk = legato.local(background="Safari")    # macOS only: one app's window, in the background

def on_step(turn, actions, timing):           # after each model turn
    print(turn, [a.describe() for a in actions if a], f"${timing['usage']['cost_usd']:.4f} so far")

result = agent.run("Open example.com and tell me the page title", desk, max_steps=40, on_step=on_step,
                   max_cost_usd=0.50, record_dir="run-1")
```

`agent.run(task, desk, max_steps=40, on_step=None, *, timeout_s=None, cancel=None, raise_desktop_errors=True,
max_cost_usd=None, record_dir=None)` takes the same `timeout_s`, `cancel`, `max_cost_usd` and `record_dir`. A desktop
that fails raises out of `agent.run`; pass `raise_desktop_errors=False` to get it back as a result, as `legato.run`
does.

- **`on_step(turn, actions, timing)`** is called after each model turn. `actions` are objects (`legato.Action`, and
  `ToolCall` for a tool), unlike `result.actions`, which are strings. `timing` has the turn's seconds (`model_s`,
  `act_s`, `settle_s`, `obs_s`), `timing["usage"]` (the run's tokens and cost so far, as `result.usage`) and, with
  `record_dir`, `timing["screenshot"]` (the saved screen's path).
- **`result.trace`** has one dict per turn: `say` (what the model wrote and called), `actions`, `done`, `s` (the
  model call's seconds), `results` (tool output, cut short) and, with `record_dir`, `screenshot` (a path; images are
  never kept in the result).
- **Desktops:** `legato.local()` (this screen), `legato.local(background="App")` (macOS),
  `legato.providers.docker(1280, 800)` (returns `(stop, env)`), `legato.attach(Sandbox.create())` (E2B, with
  `pip install "legatolabs[e2b]"`), `legato.attach(shell=run)` (any Linux sandbox you can run a command in),
  `legato.attach(screenshot=..., execute=...)` (anything else). [Desktops](sandboxes.md) has each.
- **`legato.Harness()` with no model** uses the saved one. `legato.agent_for(desk)` returns a Harness set up the way
  the Mac app and the CLI run on that desktop. On a Mac's whole screen: one plan per turn with a shell and checked
  AppleScript beside the screen. In a Mac app's background window: one action at a time, no shell. On Windows,
  Linux and every sandbox: a plain `legato.Harness()` (one action or a short batch per turn, no shell unless you
  pass `tools=`).
- **Two models:** a strong model plans each action and a small one finds it on the screen. It is how our Windows
  runs work (GPT-6.1 Terra with UI-TARS-1.5-7B):
  `legato.Harness("bytedance/ui-tars-1.5-7b", url, key, coords="pixels", native_tools=False,
  planner=legato.Planner("openai/gpt-6.1-terra", url, key, style="point"))`.
  `steps`, `usage`, `timings` and `report()` count both models' calls; `result.planner` is the planner's share
  (`calls`, `s`, tokens, `cost_usd`) and its final `notes`, and `result.trace` has each planner call with the
  actions it named and its notes then. `on_step` is called after each batch of actions.
- **Tools beside the screen:** `legato.Harness(..., tools=[legato.tools.bash(env)])` gives the model a shell in that
  desktop. The tool is named `bash` everywhere; on this computer under Windows it runs PowerShell, and the model
  is told so. `legato.Tool(name, description, json_schema, handler)` wraps your own function, whose text result
  goes back to the model.
- **Plans:** `legato.Harness(..., plan=True)` lets one model call carry up to `plan_max_steps=12` steps that run in
  order and stop at the first that cannot go on. It is offered only what can run: shell steps with
  `tools=[legato.tools.bash(env)]`, and steps that find a label on the screen (`click_text`, `wait_for`) where there
  is a text reader (macOS, or `pip install rapidocr-onnxruntime`). With neither it plans screen actions alone.
- **The result:** `status` (how it ended, above), `done`, `failed`, `answer` (what the model said at the end, as
  text), `actions`, `steps` (model calls), `wall_s`, `timings` (per turn: model, actions, waiting for the screen,
  screenshots), `usage`, `trace`, `error` (why, for every status but `done`), `stopped`, and `report()`.
  - `actions` are strings, one per action that ran, such as `"click (29, 316)"`, `"type 'hello'"` or
    `"not allowed: key cmd+q"`, then `"done"` or `"fail"`. For `legato.Action` objects, collect them in `on_step`,
    whose `actions` are the Actions (and `ToolCall`s) each turn ran.
  - `usage` (a dict, typed as `legato.Usage`: read it as `usage["complete"]`) counts the model calls that answered: `prompt`, `completion` and `cached` tokens, and
    `cost_usd` when the provider reports it. `complete` is `False` when a call was sent and cut off before its reply
    (a timeout, `timeout_s`) or sent twice: the provider may still bill it, and it is not in the counts.
- **Questions:** when the task's words ask for information ("what", "tell me", "how many", a question mark),
  `legato.run` and `agent.run` ask for the answer in done's text, and it comes back as `result.answer`. If the model
  calls done without it, it gets one more turn with the screen to give it: one more model call, counted in `steps`
  and `usage`. `Harness(ask_answer=True)` does that for every task and `ask_answer=False` for none. With a planner,
  the planner gives the answer.
- **Stopping it** on your own screen: move the mouse into a corner, or hold Escape on a Mac. Risky actions wait for
  your yes in the terminal ([what counts](sandboxes.md#this-computer)).
- **Models:** coordinates, thinking models and settings for each are in [models](models.md).

### Use Legato as a tool in your agent

```python
import asyncio, legato

def use_computer(task: str) -> dict:
    r = legato.run(task, timeout_s=120, max_cost_usd=0.50)
    if r.status == "done":
        return {"ok": True, "answer": r.answer}
    return {"ok": False, "status": r.status, "why": r.error, "did": r.actions}

async def use_computer_async(task: str) -> dict:
    return await asyncio.to_thread(use_computer, task)
```

`legato.run` blocks until the run ends, so inside a running event loop call it through `asyncio.to_thread`, never
directly. On your own screen (`"screen"`, `"background"`) the desktop keeps its state between calls: windows the
last task opened are still there for the next one. Run one task at a time per desktop.

### When a desktop fails

Opening a desktop yourself raises instead: `legato.local(background="NoSuchApp")`, `legato.providers.docker(...)` and
the other built-in desktops raise `legato.DesktopStartError` when they can't start, and one that fails later raises
`legato.DesktopError` out of `agent.run` (unless `raise_desktop_errors=False`). A desktop you attach yourself
(`legato.attach(screenshot=..., execute=...)`) raises whatever your functions raise. `DesktopStartError` is a
`DesktopError`, and both are `RuntimeError`s, so `except RuntimeError` still catches them.

### Write a starting point

`legato init` writes `agent.py`, a working agent with your saved model and desktop, to run as it is or copy into your
own code: `python agent.py "your task"`.

## RL rollouts and benchmarks

`legato rl collect` runs graded rollouts with token logprobs and GRPO groups ([RL rollouts](rl.md)); `legato bench`
runs the office benchmark and head-to-head comparisons ([benchmarks](benchmarks.md)).
