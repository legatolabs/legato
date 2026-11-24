# Changelog

## 0.3.1

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
  20 of 20 Safari questions with an empty done (legato-research EXP-0102), often in the same plan as the
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
- macOS: a sum ("1269*5693=") is typed key by key like a number, since Calculator ignores a pasted sum (legato-research
  EXP-0085: the Calculator task 0.375x the time). The harness reads an action a model left open before its own
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
  OpenRouter it halved a call with the next action unchanged (legato-research EXP-0052), and on 27 live Mac tasks it
  cut a median task from 23.7 to 18.5 s with passes held (EXP-0053). JPEG is the default; `image_format="png"` sends
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
