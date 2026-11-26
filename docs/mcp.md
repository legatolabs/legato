# Coding agents: give Claude Code, Codex or Cursor a computer

With Legato's MCP server, your coding agent gets a computer to look at and click: it can open your app, see the bug,
fix it and check the fix, or do the GUI part of a task (a web form, an admin console, a spreadsheet). Each action
comes back with a screenshot as soon as the screen stops changing, so your agent never waits longer than it has to.

```bash
pip install legatolabs
legato setup            # adds Legato to the coding agents it finds (Claude Code, Codex, Cursor, Claude Desktop)
```

Or tell your agent *"Set up Legato for me: https://legato.computer/setup"*. Then start a new session and ask it to use
its Legato desktop. This path works everywhere, Windows included: it needs only Python, and `legato setup --mcp cursor`
(or `claude`, `codex`, `claude-desktop`) adds the server to one client.

By hand, the server is `legato mcp`, with the desktop `legato setup` saved (on a Mac, one app's window in the
background; elsewhere the whole screen), or one you name:

```bash
claude mcp add legato -s user -- legato mcp                          # the saved desktop
claude mcp add legato -- legato mcp --sandbox docker                 # a desktop container: needs only Docker
claude mcp add legato -- legato mcp --local                          # your whole screen
claude mcp add legato -- legato mcp --sandbox my_desktop.py:desktop  # your sandbox: SSH, Daytona, a VM
codex mcp add legato -- legato mcp                                   # Codex: the same arguments
```

- **In the background on your Mac:** your agent works in one app's window (`desktop_open(app="Safari", url=...)`,
  default your browser) without moving your pointer or taking the screen, so you keep working
  ([what it can and can't do](sandboxes.md#in-the-background-while-you-keep-working-macos)).
- **A desktop container:** needs only Docker. The agent gets its own Linux desktop with a terminal and Firefox, and
  can't touch your screen. Apps on your machine are at `http://host.docker.internal:PORT` inside it.
- **Your own screen:** macOS, Windows, or Linux on X11. The agent moves your real mouse and keyboard; move the mouse
  into a corner of the screen to stop it.
- **Your sandbox:** a few lines of Python that start it and run a command in it ([how](sandboxes.md)).
- **Claude Desktop:** `legato setup --mcp claude-desktop`, or download
  [legato.mcpb](https://github.com/legatolabs/legato/releases/latest/download/legato.mcpb) and open it.
- **Claude Code plugin:** `/plugin install legato --marketplace legatolabs/legato` adds the server, with the desktop `legato setup` saved as above, and two skills.
- **One click:** [![Add to Cursor](https://cursor.com/deeplink/mcp-install-dark.svg)](https://cursor.com/link/mcp/install?name=legato&config=eyJjb21tYW5kIjoidXZ4IiwiYXJncyI6WyItLWZyb20iLCJsZWdhdG9sYWJzW21jcF0iLCJsZWdhdG8iLCJtY3AiXX0%3D) [![Install in VS Code](https://img.shields.io/badge/VS_Code-Install_Legato-0098FF?logo=visualstudiocode&logoColor=white)](https://vscode.dev/redirect?url=vscode%3Amcp%2Finstall%3F%257B%2522name%2522%253A%2522legato%2522%252C%2522type%2522%253A%2522stdio%2522%252C%2522command%2522%253A%2522uvx%2522%252C%2522args%2522%253A%255B%2522--from%2522%252C%2522legatolabs%255Bmcp%255D%2522%252C%2522legato%2522%252C%2522mcp%2522%255D%257D) [![Add to Kiro](https://kiro.dev/images/add-to-kiro.svg)](https://kiro.dev/launch/mcp/add?name=legato&config=%7B%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22--from%22%2C%22legatolabs%5Bmcp%5D%22%2C%22legato%22%2C%22mcp%22%5D%2C%22disabled%22%3Afalse%2C%22autoApprove%22%3A%5B%5D%7D)
  These run `legato mcp` with `uvx`, so they need [uv](https://docs.astral.sh/uv/), and use the desktop `legato setup`
  saved, as above.
- **Other clients:** the server speaks MCP over stdio. `legato setup` writes the command with its full path, so the
  client finds it whatever its `PATH`; on Windows it is this Python with `-m legato`:

  ```json
  {"mcpServers": {"legato": {
    "command": "C:\\Program Files\\Python312\\python.exe", "args": ["-m", "legato", "mcp"],
    "env": {"OPENROUTER_API_KEY": "sk-or-..."}}}}
  ```

  On macOS and Linux, `"command": "legato", "args": ["mcp"]` with `legato` on the `PATH`, and the same arguments as
  above. `env` is for `desktop_run_task`'s model keys when `legato setup` saved none: many clients start the server
  without your shell's environment ([Any model, your keys](#any-model-your-keys)).

| tool | what it does |
|---|---|
| `desktop_open(width, height, url=None, app=None)` | opens a desktop (your screen with `--local`, else a fresh sandbox); returns its id, size and first screenshot, and `{desktop_id, width, height, started, start_error}` as structured content. `width` and `height` size a new container or cloud desktop (default 1280x800); a real screen keeps its own size, which the result gives. With `url` or `app` it starts that first and returns once the window has drawn (`app` is an app name on macOS, a command on Linux and Windows); one that did not start is marked as a tool error (`isError`) with the reason in `start_error`, and the desktop is open all the same |
| `desktop_act(desktop_id, action, x, y, text, keys, direction, amount, end_x, end_y)` | click, double_click, triple_click, right_click, type, key, scroll, drag or wait, in pixels of the latest screenshot; returns the settled screenshot and how long the screen took |
| `desktop_screenshot(desktop_id)` | the current screen |
| `desktop_read(desktop_id, max_chars=8000)` | the window's text without a screenshot, as an outline (`- heading "Counter"`, `- text "Count: 1"`, `- button "+1"`) of at most `max_chars` characters; in a browser, the page with its title and address. In the background on a Mac (the desktop's window, from its Accessibility tree) and on a Windows screen (the foreground window, from UI Automation). A custom desktop can implement `env.read(*, max_chars)` to return the text of its own target. Text waits use that reader too |
| `desktop_wait(desktop_id, text=None, timeout_s=10)` | with `text`: waits until the window's text contains it (where `desktop_read` works) and returns the lines that have it; without: waits until the screen differs from the last screenshot and holds still, on any desktop, and returns it. Text that never came is a tool error that says what the window read |
| `desktop_shell(desktop_id, command, timeout_s=30)` | runs a shell command on the desktop's computer (the container, the sandbox, or yours with `--local` or `--background`, where it is not sandboxed); returns its output and exit code |
| `desktop_run_task(task, desktop_id=None, model, provider, base_url, api_key_env, max_steps, timeout_s)` | hands a whole task to Legato's own agent, with the model `legato setup` saved unless you name one (see [Any model, your keys](#any-model-your-keys)), on a desktop from `desktop_open` or, with no `desktop_id`, on one it opens for the run and closes after; returns the run report and the final screen, and `{status, answer, seconds, steps, error, cost_usd}` as structured content to branch on (`cost_usd` where the provider reports it, as OpenRouter does, else null). A run that failed, timed out or errored is marked as a tool error (`isError`). `timeout_s` is a deadline for the whole run |
| `desktop_close(desktop_id)` | stops the desktop (a container or sandbox is deleted; in the background, the browser window the desktop opened is closed, a new document stays open, and the app stays open) |

- **`desktop_act` lets your agent drive.** Use it when your agent's own model (for example Claude in Claude Code) does
  the computer use.
- **Screenshots are JPEG** (quality 85), the same pixels in about a fifth of the bytes, as the harness sends them.
  `legato mcp --image-format png` sends lossless PNG.
- **On your own Mac, with other apps open:** `legato mcp --local --wait-scope app` waits on the front app's windows
  only, so a chat or video animating elsewhere doesn't hold every action to the wait's cap.
- **In the background on your Mac:** `legato mcp --background` forces it whatever was saved. It takes no app name:
  the app is `desktop_open(app=...)`'s, else the one `legato setup --app` saved, else Safari (the only browser
  background mode works in).
- **Each background desktop gets a window of its own.** `desktop_open(app="Safari", url=...)` opens a new Safari
  window through its menu and address field (no Apple Events permission), so your tabs stay as they were and out of
  the agent's sight, and returns once the page has loaded and has the keyboard focus. TextEdit and other document apps
  get a new document; an app with neither is worked in its front window, which `desktop_open` names. Two desktops in
  one app each keep their own window.
- **`desktop_read` checks text without a screenshot.** Your agent can check that a page says `Count: 1` as it would
  with Playwright's snapshot. On a Mac in the background it reads the window that `desktop_act` works in; on Windows
  the foreground window: an Edge, Chrome or Firefox page, Notepad, a dialog, Settings. It uses the UI Automation that
  PowerShell ships with, so there is nothing to install.
- **`desktop_wait` waits for what is on its way.** A window started with `desktop_shell` takes a moment to appear:
  `desktop_wait(text="Sign in")` returns when the window says so, and `desktop_wait()` when the screen has changed
  and holds still.
- **A fixed-sleep baseline:** `legato mcp --fixed-wait 2` sleeps after every action instead of waiting for the screen.
- **See what the agent did:** `legato mcp --record DIR` saves every screenshot the agent got, with a `log.jsonl` of its
  actions, under `DIR/<desktop_id>/`, including each step of a `desktop_run_task`.
- **`desktop_shell` skips the screen** for what a command does faster: starting apps, reading files, checking results.
- **`desktop_run_task` delegates.** Use it to offload a whole GUI task to a cheaper open model while your agent keeps
  working.

## Any model, your keys

`desktop_run_task` runs the model `legato setup` saved, or the one your agent names. The model's name says where it
runs, each with that provider's own key:

| `model` | runs on | key |
|---|---|---|
| `claude-sonnet-5-5`, `claude-...` | Anthropic's API | `ANTHROPIC_API_KEY` |
| `gpt-...`, `o3`, `o4-mini` | OpenAI's API | `OPENAI_API_KEY` |
| `gemini-...` | Google's API | `GEMINI_API_KEY` |
| `grok-...` | xAI's API | `XAI_API_KEY` |
| `qwen/qwen3.8-27b`, `anthropic/claude-sonnet-5.5` (vendor/model) | OpenRouter | `OPENROUTER_API_KEY` |

`provider` names one instead (`together`, `fireworks`, `groq`, `cerebras` too), and `base_url` with `api_key_env`
any other OpenAI-compatible server. A server of your own saved with `legato setup` (a gateway, a proxy) keeps every
model name.

Give the server a key in either of two ways:

```bash
legato setup --add-key anthropic          # saved for this user; your default model stays as it is
claude mcp add legato -e ANTHROPIC_API_KEY=sk-ant-... -- legato mcp   # or in the client's env for the server
```

A key goes only to its own provider. With no key for the model it was asked for, the tool says which key is missing,
how to add it, and which providers it does have a key for. The Claude Desktop extension asks for OpenRouter, Anthropic
and OpenAI keys in its settings, all optional.

If something fails, `legato doctor` (or `legato doctor --json`) says what is installed and set up, with the fix for
each. On a Mac it also takes one real window capture, as background mode does, and reports whether it works, stalled
(macOS's capture service is busy: Legato falls back to the slower `screencapture`) or failed. With a model on your
machine (`localhost`), it checks that the server answers.
