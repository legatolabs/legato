# Changelog

## Unreleased

- **On Windows, a shell command that starts a program by its name starts it at once.** `Start-Process msedge
  <address>` took about 12 s before the browser appeared: PowerShell finds a program that Windows has registered
  by name (Edge, Chrome, Word...) only after looking elsewhere first. The shell tool now runs commands with those
  programs' folders at the end of `PATH`.
- **A shell command that starts a program no longer holds the tool until that program lets go.** The shell tool
  on this computer (`legato.tools.bash()`, and the Mac app's and `legato run`'s shell) read the command's output
  through a pipe, which every program started from the command kept open: `Start-Process msedge <url>` on Windows
  took 12 s or more to return, and `server &` ran to the timeout and was then killed with it. The output now goes
  to a file and the tool returns when the command itself ends.
- **`legato run --screen` and `legato.run(desktop="screen")` on a Mac wait the way the Mac app does:** cells of
  the screen that move on their own (a clock, a spinner in another app) no longer hold every action to the 2 s cap.
  `legato.local()` keeps its default (`wait="screen"`); pass `wait="cells"` for the same.
- **The plugins, the Gemini extension, the MCP Registry entry and the one-click installs start `legato mcp` with the
  desktop `legato setup` saved**, as `legato mcp` does by hand: with none saved, one app's window in the
  background on a Mac and the whole screen elsewhere. Before, they always started a Docker container.
- **The Claude Code plugin has a folder of its own, `plugins/legato/`,** ready for Anthropic's plugin directory:
  it pins the package it runs (`legatolabs[mcp]==` this version), and has a README that says what it runs, fetches
  and sends. `/plugin install legato --marketplace legatolabs/legato` installs it from there. The repo also has a
  Cursor plugin manifest (`.cursor-plugin/plugin.json`), and the README an Add to Cursor button.
- **Bring any model, with your own key for each provider.** A model's name now says where it runs: `claude-...` on
  Anthropic's API with your Anthropic key, `gpt-...` and `o3` on OpenAI's, `gemini-...` on Google's, `grok-...` on
  xAI's, and `vendor/model` on OpenRouter. This works for `desktop_run_task(model=...)`, `legato run --model`,
  `legato.run(model=...)` and `legato doctor --model`, and `provider=` / `--provider` names one instead. Before, a
  named model went to the saved provider, so `claude-sonnet-5-5` with OpenRouter saved failed.
- **`legato setup --add-key PROVIDER`** saves a key for one more provider (tried first, readable by you only), and
  the default model stays as it is. Changing the default provider keeps the old one's key. A key goes only to its own
  provider; a gateway of your own keeps every model name.
- With no key for the model it was asked for, `desktop_run_task` says which key is missing, how to add it, and which
  providers it has a key for (`legato doctor --json` lists them under `setup.keys`). The Claude Desktop extension
  asks for optional OpenRouter, Anthropic and OpenAI keys, and the MCP registry entry lists them.

From first-time users on fresh Windows machines:

- **Nobody at the keyboard is no longer asked.** An empty stdin on Windows (what a coding agent's shell gives a
  command) said it was a terminal, so `legato setup` and `legato init` printed a question and stopped, and
  `legato run` waited 120 s on "allow it?". They now go on as they do with no terminal, and a question that meets
  the end of input takes its default, as `--yes` does. `legato setup --mcp X`, `--coords` and `--api-key-env` change
  that one thing without the questions once a model is saved, and `legato init` keeps the saved desktop.
- **`> file` for a new file is no longer "overwrites a file".** A redirect to a full path under your home folder that
  does not exist yet (outside dot folders, AppData and Library) runs; an existing file, a relative path and anything
  else still ask.
- **A refused command is cleared from the prompt** (Escape on Windows, Ctrl+U elsewhere), so a later Return cannot
  run it.
- **`legato.local()` minimises the console window on Windows**, as `legato run` did, so a Python script's agent does
  not type into its own terminal (`hide_terminal=False` keeps it in front).
- A chord written with spaces (`ctrl s`, `ctrl shift s`) is pressed as a chord, not as one key after another.
- Printed actions are plain ASCII (`drag (1, 2) -> (3, 4)`, `type "..."`): the arrow printed as `?` on Windows.
- `legato init`'s agent.py prints the answer, and its `Then:` line (like `legato setup`'s `Next:`) gives a real task
  instead of `"your task"`. `legato setup --mcp` says when only the name of a key's variable is saved, which a coding
  agent may not pass on.

## 0.3.2

- **Legato describes itself as a computer-use runtime**: the package description on PyPI, the CLI banner, the README,
  the setup page, the FAQ and llms.txt.
- The benchmarks page's Windows section shows Legato's own runs, with the rule each task had.

From a security review of the whole repo and its history before launch (no keys or private data found):

- **An AppleScript can no longer run shell commands through the file it's written to.** Scripts went to the shell in
  a heredoc, so a script with the closing word on a line of its own ran the lines after it; they go as base64 now.
- **The shell guard looks inside commands:** `bash -c "..."`, backticks and `$(...)` are checked like the command
  itself; an inline `python3 -c` or `node -e` program that deletes, writes, runs commands or uses the network asks;
  printing a key (`printenv`, `$OPENROUTER_API_KEY`, the whole environment) asks, since the output goes to the model.
- **`desktop_shell` and `desktop_open(app=...)` on this computer** refuse risky commands under `legato mcp` unless it
  was started with `--yes`, as the README said; a sandbox's shell is unchanged. PowerShell's curly single quotes in a
  URL are escaped too.
- `legato setup --base-url` no longer reuses the key saved for another server, and the config file is created
  readable by its owner only (it was chmodded just after writing).

## 0.3.1

- **Bring any model, checked the way a run uses it.** `legato setup` now tries the model with the agent's own prompt,
  a test screenshot and the `computer` tool (it was a one-word colour question, which passed models that then failed
  every run with "No endpoints found that support tool use"). A model that takes images but no tool calls is asked
  for its actions as JSON instead; that and the coordinates its click used (pixels or the 0–1000 grid) are saved in
  the config (`tool_calls`, `coords`; `legato setup --coords`), so `legato run`, `legato mcp` and `legato.run()` use
  them. `Harness` also switches to JSON actions by itself when a server refuses tool calls. A text-only model is
  refused in plain words ("this model does not accept images; Legato needs a vision model") with two that work.
- **`legato doctor --model [NAME]`** tries the saved or a named model: answers, accepts images, makes tool calls,
  where its click lands, each with its fix (exit 1 when it cannot be used; `--json` has `model_check`).
- **What is true about setup:** with no setup and a key in the environment, `legato` and `legato doctor` say the
  default model is in use and that `legato setup` saves a choice, and `legato init` takes that default without
  asking. A setup that saved no key says what is missing instead of "Next: legato run". `--provider local` with no
  server names the three it looked for and how to start one, at once. Key hints use PowerShell's form on Windows.
  `legato doctor`, `legato run` and `report()` print " - " in place of the middle dot where stdout is not UTF-8
  (a Windows pipe showed it as another character).
- **Python API: answers, cost limits, traces and honest planner totals.**
  - A task that asks for information ("what", "tell me", a question mark) gets its answer in `result.answer` from
    `Harness.run` too, not only `legato.run`: the task is sent with a line asking for it, and a done without it gets
    one more turn (`ask_answer` now defaults to deciding from the task's words; `False` turns it off). GPT-6.1 Terra
    and Claude Sonnet 5.5 returned `answer: ''` before. A planner (`legato.Planner`) now gives the answer as well.
  - `Harness(plan=True)` offers only the steps that can run: no shell steps without a `bash` tool, no label steps
    without a text reader (Windows and Linux), PowerShell named where the shell is PowerShell, and a step that
    can't run is said to the model. On Windows with no tools it ran "out of steps ... 20 model turns, 0 actions".
  - `max_cost_usd=` on `legato.run` and `Harness.run` ends a run with the new status `max_cost` once the cost the
    provider reports reaches it. `on_step`'s `timing` carries `usage` (the run's tokens and cost so far).
  - `record_dir=` saves the screen after each turn, and every run has `result.trace` (what the model said, what ran,
    seconds, the screenshot's path), not only planner runs.
  - Planner runs count the planner: `steps`, `usage`, `timings` and `report()` include its calls, tokens, cost and
    seconds (they showed "0 model turns" and 0 tokens), `on_step` gets the actions each batch ran, and the planner's
    notes are in `result.planner["notes"]` and at each planner call in the trace.
  - `result.error` says why for every status but `done`: `failed` carries the model's reason (still in `answer`) and
    `max_steps` says so; both were `None`.
- **`legato mcp` refuses risky actions on this computer unless started with `--yes`.** Its stdin is the client's
  pipe, so "ask in the terminal" asked nobody and ran them: Cmd+Q or a typed `rm` through `desktop_act`, and the
  shell and AppleScript steps inside `desktop_run_task`. The client is told how to allow them. The README says what
  the list doesn't catch, and that a page on screen can try to instruct the agent.

- **Background mode opens an app that isn't running yet.** 0.3.0 waited for it with a plain sleep, and macOS only
  updates a process's list of running apps from its run loop, so an app opened after the first look was never seen:
  a first `legato run` with Safari closed failed with "Safari did not start (is it installed?)". The wait now runs
  the run loop (the app is found in about a second) and allows 30 s.
  In the MCP server, which runs tools on worker threads, the list could stay stale even so: apps are now also looked
  up by bundle id, which is always fresh (`desktop_open` on an app that isn't running: 1.6 s).
- **Background mode keeps working when macOS's window capture stalls.** With its capture service busy (a screen
  recorder running, several captures at once), `CGWindowListCreateImageFromArray` can hang 30 s and return nothing on
  every screenshot, so every run failed with "is Screen Recording on for this app?". The call now gets 3 s; after a
  stall the window is taken with `screencapture` for the next five minutes (its sheets and menus are left out then).
  A run that fails mid-way prints one line, not a traceback.
- **MCP on Windows reads text and waits.** `desktop_read` works on a Windows screen: the foreground window's text
  (an Edge or Chrome page, Notepad, a dialog, Settings) through the UI Automation that PowerShell ships with, with
  nothing to install. New `desktop_wait` waits until given text is in the window, or until the screen has changed and
  holds still. `desktop_open` gives `{desktop_id, width, height, started, start_error}` as structured content, the
  real screen size, and marks a url or app that did not start as an error; the server reports its version.
  `desktop_run_task` takes no `desktop_id` (it opens a desktop for the run and closes it), adds `cost_usd` to its
  structured result, and says when the model key is missing from the server's environment. The MCP docs say what the
  one-click buttons need (uv and Docker) and show the path that works everywhere.
- **The MCP server passes a desktop's failure to the client with its reason** (the MCP library otherwise shows only
  "Error executing tool"). The MCP docs say what `desktop_close` and `desktop_shell` do in the background.
- **`legato run --json` prints one JSON object when it fails too** (`done: false` and the `error`), not only on
  stderr, so coding agents can read why.
- **Set-up page:** clearer for coding agents. Save the choice even when a key in the environment already makes it
  ready; a key in the environment is read from its variable, not saved; `--mcp none` with `--yes`, then add only
  the client you are; Safari when the default browser is another one; what to do when an app is slow to open.
- `legato setup` says where Legato will work, and, for a local model server that doesn't answer, which servers to
  start (not "check the key"). Models docs: the local example works in one app's window instead of the whole screen,
  and says Ollama and LM Studio aren't tested end to end yet. `/docs/terminal` redirects to `/docs/python`.
- **Planner notes and shell:** a planner with `style="point"` keeps notes (`notes=False` turns them off). It can write notes that are given
  back to it in full at every call, so what it read on one screen is not lost at the next. A planner whose harness has
  the `bash` (or `applescript`) tool can name commands as actions and reads their output at its next call
  (`shell_name` says which shell that is).
- **The Mac app starts from `legato setup`'s model.** With no model chosen in the app yet, it reads the provider,
  model and base URL from `~/.config/legato/config.json` (and a key pasted into `legato setup`, if the Keychain has
  none for that provider). Choosing a model in the app makes that the app's own.
- `legato setup --desktop path/to/file.py:function` (or `module:function`) saves your own sandbox as the default, so
  `legato run`, `legato mcp` and `legato.run()` use it with no flags. Setup checks the function exists without
  starting it, and keeps the path absolute.
- **On Windows the shell tool is PowerShell.** `legato.tools.bash()` and `bash(legato.local())` run commands with
  `powershell -NoProfile -NonInteractive -Command` (it was `cmd.exe`), say so in the tool's description, and a planner
  is told which shell it has (`Tool.runs`; `Planner(shell_name=...)` overrides it).

From the persona tests (six simulated launch users on a real Mac):

- **Every run result has a `status`:** done, failed, max_steps, timeout, cancelled, stopped or error.
  `legato.run(timeout_s=, cancel=)`, `Harness.run(timeout_s=, cancel=)` and `legato run --timeout S` end a run on a
  deadline or a `threading.Event`, keeping its usage so far.
- **A failed desktop is a result, not an exception,** in `legato.run`, `legato run` and `desktop_run_task`
  (`status="error"`). `Harness.run` still raises unless `raise_desktop_errors=False`, so rollout retries are
  unchanged.
- **Provider errors keep only the status code and the message:** OpenRouter's account `user_id` no longer appears in
  `error`.
- `Harness.run` defaults to 40 steps, like `legato.run` and the CLI; `RunResult.usage` is typed as `legato.Usage`;
  `legato run` exits 2 on a model error, and `--json` output leads with `status`.
- **MCP `desktop_read`:** a background desktop's window text from its Accessibility tree, as a short outline, so a
  coding agent can check what a page says without a screenshot.
- **`legato doctor` takes a real window capture** and reports works, stalled or failed, with the fix. With a model on
  this machine, it checks the server answers, and it no longer reports a dead server as ready.
- **Background mode:**
  - It no longer presses the window's close, minimize or zoom buttons; the model is told the desktop can't.
  - An open menu or popover now shows in the screenshot (it was drawn behind the window).
  - With window capture stalled, a sheet (a Save or New Game dialog) is captured as itself.
  - The default browser is Safari: Chrome 158 and later ignore background clicks on a web page's elements. A task
    can still name Chrome.
- A missing app fails at once ("NoSuchApp could not be opened: Unable to find application..."), not after 30 s;
  `legato doctor --help` prints help.
- After a window-capture stall, the next Legato processes skip the 3 s wait too, for five minutes (a note in
  `~/.cache/legato`). `legato doctor` tries a few ordinary windows before it calls window capture failed.
- A model that doesn't answer says where and what to do ("the model at http://localhost:11434/v1 did not answer (2
  attempts): ConnectError... Is the model server running?"). `legato setup` suggests Ollama's or LM Studio's port when
  that server is installed. README and llms.txt: vLLM is the tested local server; Ollama and LM Studio aren't yet.
- `legato.run(timeout_s=)` covers opening the desktop too. New `legato.DesktopError` and `DesktopStartError` (both
  RuntimeErrors) when a desktop can't open or act. `usage["complete"]` is False when a call was cut off and may still
  be billed. A refused key (HTTP 401/403) says which key (by its variable's name, never its value) and how to fix it.
- **Unattended runs refuse risky actions by default:** with no terminal, `legato.run`'s `confirm="auto"` refuses them
  and tells the model. Pass `confirm` (a function, `None`, or `"deny"`) to choose.
- **Background mode, round two:**
  - It works in the app's real window, skipping bubbles and popovers (TextEdit's autocorrect, Chrome's help), and
    picks again when a window closes instead of blaming Screen Recording.
  - Bold, italic and font changes are refused: a background app turns those menu items off, and its format bar
    lights up without changing the text, so a model reported bold that never happened. Underline and alignment work.
  - In Safari, `desktop_open(url)` opens the page in its own window (no Apple Events permission needed), ready to
    read and click when it returns: 2.8 s, was 15.7 s. `desktop_close` closes that window; the person's tabs are
    left alone.
- An app Legato just opened gets the rest of its launch time to show its first window (a cold start was an error
  after 5 s). Docs: `usage` is read as `usage["complete"]`; an attached desktop raises its own exceptions.
- Runs are asked to finish with fail when any part of the task couldn't be done (a run ended `done` while saying
  nothing worked). The text report puts an error before its timing and shows the cost. FAQ: the apps Legato
  drives keep their own habits, e.g. TextEdit autosaving a new document to iCloud.
- **Background mode works in a window of its own and never steers the person's windows.** Every background desktop
  (`legato.run`, `legato run`, MCP `desktop_open`) opens a new window or document through the app's own New menu item;
  a browser window closes when the run ends, a document stays. An app whose New item would change the person's data
  ("New Note", "New Game") is worked in its front window, and the desktop names it. A persona test had turned three of
  the person's Safari tabs into apple.com.
  - Each desktop follows only its own windows (and ones its own actions open); two MCP desktops in one app no longer
    share a window.
  - Cmd+R, Cmd+[ and Cmd+] press Safari's toolbar buttons; system shortcuts (Cmd+Space, Cmd+Tab, Mission Control) are
    refused.
  - `desktop_run_task` returns structured output (`status`, `answer`, `seconds`, `steps`, `error`) and sets `isError`
    for failed, timeout and error; `--record` keeps its inner steps.
- **The same impossible action no longer loops to the step limit.** From its second refusal the model is told
  to call fail and say why; the third ends the run as failed with the reason. A background app's Save, Close and
  Find stay turned off however often they're pressed (a TextEdit "save it" task looped to the call limit).
- **Background mode refuses browsers other than Safari.** In Firefox a run's screenshots and keys reached the
  person's own Firefox window and changed its selected tab, though the window it opened was its own; Chrome 158
  ignores background clicks on web pages. `legato init` refuses them too; `window="front"` still works.
- **Two Legato processes in one app keep to their own windows.** Their claims were per process, so a Safari window
  another process had just opened was taken as one this run's action opened, and clicked. They are now shared
  through `~/.cache/legato/windows`; a window another desktop took is never this one's, and one it closed (macOS
  still lists it for a moment) is nobody's. Opening its own window gives the front back if the app took it.
- **Typed text goes in as written** in background apps other than browsers, through Accessibility: TextEdit had
  capitalised a typed list ("alpha" to "Alpha"), and the run spent 2.5 minutes retyping it; the same list now takes
  5 s. A field's newline is still Return.
- **Save, Duplicate, Export and Print are refused in the background by their menu title**, on or off: Cmd+S had
  opened a real Save sheet no click could reach, and Cmd+Shift+S ran TextEdit's Duplicate. A fail with no text gets
  the last refusal as its `error`.
- **Background desktops never take another window's sheet** (another tester's close prompt appeared while one acted,
  and its form reached the model), **answer their own window's close prompt** ("You have entered text…"; five
  sign-up checks each left a window open), and `legato mcp` closes its background windows when the client goes.
- **Refusals of any kind end the run at six**, and a refused Save, Export or Print says it stays off in the background:
  a "save it" task tried four ways and ran to its 120 s limit; it now fails in 16 s with the reason. A failed run's
  `report()` and `desktop_run_task` reply lead with that reason.
- **A question gets its answer.** When a task asks for information ("what", "tell me", "how many", a question mark)
  and the model calls done without the answer, it gets one more turn with the screen and is asked for it
  (`Harness(ask_answer=True)`, which `legato.run`, `legato run` and `desktop_run_task` set). GPT-6.1 Terra ended
  20 of 20 Safari questions with an empty done, often in the same plan as the
  navigation, before the page had loaded.
- MCP: `desktop_read` honors `max_chars` and drops TextEdit's ruler markers; `desktop_run_task` takes `timeout_s`; the
  Chrome refusal says Chrome ignores background clicks on web pages.
- `legato rl`: Python `collect()` raises `CollectError` when no rollout completed (and names the `__main__` guard);
  rollouts per hour come from real time; `--start-stagger`; the fake screen is 640×400 and readable; export rows have
  `attempt`, and steps record `retry_reasons`.
- **`legato rl collect --backend fake` works out of the box** (the fake harness is its default). A harness or backend
  that can't load stops before any worker starts, naming the fix, and `collect` exits 1 when no rollout completed.
- **Sampling for GRPO:**
  - `--temperature` sets it, and a temperature in `--extra-body` or `--harness-opts` now applies too.
  - `--seed` records a seed per rollout.
  - A group sampled at temperature 0 gets a warning.
- **Exported samples keep tool calls:** `response_text` includes the action. New fields: `response_content`,
  `response_tool_calls`, `tokens_cover_response`, `finish_reason`, `sampling`.
- The fake desktop's reward can be set with `--fake-opts`, and help text says `legato rl`.
- **`legato rl` works on a first try without VMs, and says what needs what.** docs/rl.md opens with "Try it in two
  minutes, without VMs". `--backend fake` now runs `legato.Harness`, so the recorded actions are the model's own (the
  old default clicked (10, 10) whatever the model said), and its reward is labelled fake in the records, the export
  and the report. `legato rl probe` also sends an image and a tool call, says in words what the server is good for
  (evaluation, policy-gradient training) and exits 1 for a model without image input; `collect --require-logprobs`
  stops before any rollout when the server returns none. Every JSON option takes `@file.json` (PowerShell drops the
  quotes inside an argument), and a bad one, a wrong model or URL, or a missing tasks file is one line, not a
  traceback. `--backend path/to/file.py:Class` runs your own environment (`legato.rl.Desktop`,
  `examples/rl_custom_backend.py`), and `--backend local` runs this computer's screen with your reset and grading.
  The export gains `actions` (with the parsed action) and `reward_source`. OSWorld's errors say it needs Linux with
  KVM. `legato bench --help` lists its commands.
- **Safer on your own screen, on Windows too.** Typed `Remove-Item`, `del`, `rd`, `format`, `reg delete`, a shutdown and
  a downloaded script piped to `iex` wait for a yes, as `rm` and `sudo` do; so does Delete right after selecting
  everything. With nobody to ask (`legato run` with its output piped, or run by a coding agent) a risky action is
  refused and said on stderr, not run unseen; `--yes` allows them. A question nobody answers in two minutes is a no, and `legato run --json` never asks. With nobody to ask, the run stops at the risky action
  (status `stopped`, the action in `error`): it used to refuse and carry on, and the model then reached the same end
  through a menu. On Windows the terminal that runs `legato run` is minimised while a task uses the screen
  (`--show-terminal` keeps it), so the agent does not type into it. A typed delete is
  still asked about when a click comes between the typing and Return.
  `legato run` says how to stop it, and no longer crashes when its output is piped on Windows (an arrow the console's
  encoding lacks).

## 0.3.0

One agent, four ways in, and one command to set it up.

- **`legato setup`:** choose the model (checked with one call) and where it works, add Legato to the coding agents it
  finds (Claude Code, Codex, Cursor, Claude Desktop), and watch a first task. `--yes` and `--json` for coding agents;
  a key can come on stdin (`--api-key -`). Saved to `~/.config/legato/config.json`, readable by you only.
- **Set up from a coding agent:** tell it "Set up Legato for me: https://legato.computer/setup". The page
  (`docs/setup.md`) is written for agents to follow.
- **`legato run "task"`** needs no flags: the saved model and desktop. On a Mac the default is one app's window in the
  background (the app the task names, else the browser), so you keep using the Mac; `--app NAME`, `--screen` and
  `--sandbox` choose. It prints each turn with a timer and the answer.
- **`legato.run(task)`**, **`legato.Harness()`** with no model, and **`legato.agent_for(desktop)`**: the saved choice in
  Python. Every way in gets the same agent for the same desktop: on a Mac's whole screen, the Mac app's setup (plans,
  a shell and checked AppleScript, 4 screenshots, the ending fix).
- **`RunResult.answer`:** what the model said at the end (done's text); `legato run` and `desktop_run_task` return it.
- **MCP:** `legato mcp` with no flags uses the saved desktop (it was E2B); `desktop_run_task` the saved model; in the
  background `desktop_open` defaults to the browser.
- **One install:** the MCP server, this computer's drivers and, on macOS, the Vision text reader that plans use are
  in `pip install legatolabs`. `[local]` and `[mcp]` still work and add nothing.
- **Background mode:** an app hidden with Cmd+H is shown again (not brought to the front); the agent's Cmd+H and
  Cmd+M are not run.
- macOS: a sum ("1269*5693=") is typed key by key like a number, since Calculator ignores a pasted sum (the Calculator
  task 0.375x the time). The harness reads an action a model left open before its own
  closing tags (Qwen3.8-27B).
- **Two models, one plans and one points (#42):** `legato.Planner(..., style="point")`: a strong model names each
  action and a small one finds it on the screen, the setup of the Windows runs (GPT-6.1 Terra with UI-TARS-1.5-7B).
- Docs: a shorter README; Set up, Mac app, and Terminal and Python pages; the sidebar from 21 links to 12.
- `legato.hosted` is no longer exported from `legato`: the managed service isn't live yet. The module stays
  (`from legato import hosted`), and the FAQ says there is no hosted version yet.
- **Easy start (#35):** `legato` alone prints what to do first: `legato init`, the three ways to use Legato, and a
  prompt to give a coding agent. `legato init` asks for the model (OpenRouter, a local server or any OpenAI-compatible
  URL) and the desktop (on a Mac: background mode in your default browser, or any app), writes a working `agent.py`
  and lists what is still missing (the key, extras, permissions). `legato run --background APP` works in one app's
  window. The README starts with a quickstart that runs on your own computer with one key.
- **Background mode (macOS):** `legato.local(background="TextEdit")` and `legato mcp --background` work in one app's
  window while you keep using the Mac: the pointer, the front app and the clipboard stay yours (#33). Keys are posted
  to the app, clicks press, focus or select through Accessibility, and what has no Accessibility equivalent (drags,
  copy and paste) is not run. New `legato.safety.Unavailable`: the model is told the desktop can't do an action,
  instead of that the person refused it. Tested on TextEdit, Safari and Chrome.

## 0.2.0

- **Legato for Mac** (`app/macos`): a menu-bar app that runs Legato on your own Mac with your own model and key.
  Open it with Fn (hold Fn to say the task), switch models from the command bar (⌘1-⌘4, ⌘K), and watch each step;
  risky actions wait for your yes. Providers: OpenAI, Claude (Anthropic), Gemini, Grok (xAI), OpenRouter, Together AI,
  Fireworks AI, Groq, Cerebras, and any OpenAI-compatible server. `app/macos/release.sh` builds a
  signed, notarized `.dmg` with its own engine inside; the `mac-app` workflow attaches it and the Sparkle update feed
  to each release, and the app updates itself.
- MCP: `--wait-scope app` (with `--local` on macOS) waits on the front app's windows only; `--fixed-wait S` sleeps
  instead of waiting, as a baseline. `benchmarks/mcp_h2h/` compares Claude Code with Legato's MCP against Claude
  Code's built-in computer use (#21); no results yet.
- MCP: screenshots go to the agent as JPEG (quality 85), like the harness since #17; `legato mcp --image-format png`
  keeps PNG. Recordings (`--record`) stay PNG.
- Plans read labels (`click_text`, `wait_for`, labelled clicks) on Linux too: where macOS Vision is not available,
  `legato.plan` uses RapidOCR if it is installed (`pip install rapidocr-onnxruntime`, CPU). A label also matches a
  line the reader ran together ("Document saved" in "Documentsaved").
- `Harness(plan=True)`: one `plan` tool call carries the steps of a turn (screen actions, `click_text` by visible
  label, shell, AppleScript, `wait_for` a label, done), run in order until one cannot go on; the model then sees what
  ran and why it stopped. Labels are read with macOS Vision. `end_fix=True`: a reply that says the task is complete
  without an action gets a note, and a second one in a row ends the task as done.
- `legato.tools.applescript(env)`: AppleScript that is compiled against the app's dictionary first and read back with
  a `check` script; an error returns the app's real terms.
- `Harness(image_format="jpeg")`: screenshots go to the model as JPEG, the same pixels in about a fifth of the bytes.
  The upload is most of a hosted call's wait for its first token on a home uplink; on replayed Mac turns through
  OpenRouter it halved a call with the next action unchanged, and on 27 live Mac tasks it
  cut a median task from 23.7 to 18.5 s with passes held. JPEG is the default; `image_format="png"` sends
  the screenshots lossless.
- `legato.local()` is safe to run on your own Mac: move the mouse into a corner or hold Escape to stop it, and risky
  keys and typed shell commands (`legato/safety.py`) wait for a yes (`confirm=`, `legato run --yes`). `legato doctor`
  checks the macOS permissions. `wait_scope="app"` waits on the front app's windows only; `wait="ax|cells"` ends a
  wait on the front app's Accessibility events.
- Off by default, for experiments: `change_log` (one screenshot and a text log of what changed on the screen, with
  `change_log_lines` and `change_log_turns`), `progress_note`, `history_scale`, `hedge_after_s`, `ocr_cache`,
  `ocr_near`, `plan_trust_first_click`, `strict_done`.

- MCP: `desktop_open(url=..., app=...)` starts a page or app first and returns once its window has drawn, so the agent
  skips typing a launch command and waiting for it.
- MCP: `desktop_shell(desktop_id, command, timeout_s=30)` runs a shell command on the desktop's computer and returns
  its output and exit code, so an agent can start apps, read files and check results without the screen.
- A native in-sandbox helper (Rust, `native/legato-step`) for Linux sandboxes and E2B, on x86_64 and arm64. Each
  action is faster: it reads the screen through shared memory instead of running `xwd`, and encodes the screenshot
  while the wait sits out its quiet time. The stop rule and the JSON it answers are unchanged. Sandboxes it can't run
  in keep the Python helper; `LEGATO_HELPER=python` chooses it.
- Requests to OpenRouter name Legato as the app (`HTTP-Referer`, `X-Title`); `LEGATO_NO_ATTRIBUTION=1` turns it off.
- `Harness(tools=[...])`: tools beside `computer`. `legato.tools.bash(env)` runs commands in the desktop's own shell
  (E2B, `attach(shell=...)`, `--sandbox docker`, `legato.local()`); `legato.Tool` wraps your own function. Calls run
  in the order the model sent them, each result goes back as text, and a tool that raises is reported to the model.
  Models without native tool calls are told the JSON form. Without `tools`, requests are unchanged.
  `result.report()` shows tool time.

## 0.1.8

- `triple_click` (selects a line or a whole field), on every desktop: Linux sandboxes, E2B, your own screen and
  OSWorld. Models trained on Claude's computer-use actions send it, and it used to be rejected.
- A busy or starting model server is waited out: a 429 or 503 is retried after its Retry-After, for up to
  `busy_wait_s` (120 s) in all, without using up `retries`. A cold GPU no longer ends a run.

## 0.1.7

- `LEGATO_DOCKER_ARGS`: extra `docker run` arguments for `--sandbox docker`, such as a volume with your project.

## 0.1.6

- `legato mcp --record DIR`: every screenshot the agent got, with a log of its actions.
- `--sandbox docker` hands over the desktop once its first window is up, so the first screenshot is not black.

## 0.1.5

- The MCP server tells the agent what kind of desktop it has (for docker: a terminal is open, and the user's apps are
  at `host.docker.internal`; for local: these are the user's real windows).
- `--sandbox docker` pulls or builds the image as the server starts, and says plainly when Docker is missing or not
  running. The image is published for arm64 too, and opens Firefox without first-run tabs.
- A Claude Desktop extension, `legato.mcpb`, attached to each release (Python 3.11 or later).
- A second skill, `desktop`, on using the desktop tools well.

## 0.1.4

- `legato doctor [--json]`: which sandboxes, extras and model keys are ready, with the fix for each.
- A Claude Code plugin in the repo: `/plugin install legato --marketplace legatolabs/legato` adds the MCP server
  (with `--sandbox docker`) and the use-legato skill. The repo also carries Agent Plugins files (`plugin.json`,
  `mcp.json`) for other editors.
- The MCP Registry entry now starts the server with `--sandbox docker`, so it needs no key.

## 0.1.3

- `--sandbox docker`: a fresh desktop container on this machine (`ghcr.io/legatolabs/desktop`: a terminal and
  Firefox), with apps on the host reachable at `http://host.docker.internal:PORT`. `--sandbox docker:IMAGE` for your
  own image. Also `legato.providers.docker(width, height)` from Python.
- `legato.providers` is importable from `legato`.

## 0.1.2

- Bring your own computer. `legato.local()` drives this computer's screen on macOS, Windows or Linux (X11), with
  `pip install "legatolabs[local]"`.
- `legato.attach(shell=run)` drives any Linux sandbox you can run a command in (Docker, SSH, Daytona, your own VMs),
  with one call per action.
- `legato run` and `legato mcp` take `--sandbox e2b|local|path/to/file.py:function`, and `legato mcp --local`.
- `legato run` on E2B now opens a 1280x800 desktop with a one-hour limit, and closes it at the end.

## 0.1.1

- `legato run --json` prints one JSON result, for scripts and coding agents.
- Clear errors when the E2B extra, `E2B_API_KEY` or the model key is missing.
- A `legatolabs` command alias, so `uvx legatolabs mcp` works.
- Ships `py.typed`.
- Listed on the MCP Registry as `io.github.legatolabs/legato`.

## 0.1.0

- `legato.Harness`: a computer-use loop for any OpenAI-compatible model. It has a ready-wait, chunked screenshot
  history, up to 3 actions per turn, bounded calls, a 4,096-token budget with a cut-off note, and per-phase timing.
- `legato.Harness.reference`: a re-implementation of Anthropic's computer-use reference loop, for comparison.
- `legato.attach`: E2B Desktop with a single-call in-sandbox wait, or any sandbox through screenshot and execute
  functions.
- `legato rl`: graded rollouts for computer-use RL on OSWorld VMs, with logprobs, GRPO groups, oversampling and
  per-batch profiles.
- `legato mcp`: an MCP server with cloud desktops (E2B) for any agent; each action returns the settled screenshot.
- `legato bench`: the Legato Office Bench (Excel-to-PowerPoint tasks with a python-pptx grader) and multi-arm reports.
