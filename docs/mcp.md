# Coding agents: give Claude Code, Codex or Cursor a computer

With Legato's MCP server, your coding agent gets a computer to look at and click: it can open your app, see the bug,
fix it and check the fix, or do the GUI part of a task (a web form, an admin console, a spreadsheet). Each action
comes back with a screenshot as soon as the screen stops changing, so your agent never waits longer than it has to.

```bash
pip install legatolabs
legato setup            # adds Legato to the coding agents it finds (Claude Code, Codex, Cursor, Claude Desktop)
```

Or tell your agent *"Set up Legato for me: https://legato.computer/setup"*. Then start a new session and ask it to use
its Legato desktop.

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
- **Claude Code plugin:** `/plugin install legato --marketplace legatolabs/legato` adds the server (Docker) and a skill.
- **One click:** [![Add to Cursor](https://cursor.com/deeplink/mcp-install-dark.svg)](https://cursor.com/link/mcp/install?name=legato&config=eyJjb21tYW5kIjoidXZ4IiwiYXJncyI6WyItLWZyb20iLCJsZWdhdG9sYWJzW21jcF0iLCJsZWdhdG8iLCJtY3AiLCItLXNhbmRib3giLCJkb2NrZXIiXX0%3D) [![Install in VS Code](https://img.shields.io/badge/VS_Code-Install_Legato-0098FF?logo=visualstudiocode&logoColor=white)](https://vscode.dev/redirect?url=vscode%3Amcp%2Finstall%3F%257B%2522name%2522%253A%2522legato%2522%252C%2522type%2522%253A%2522stdio%2522%252C%2522command%2522%253A%2522uvx%2522%252C%2522args%2522%253A%255B%2522--from%2522%252C%2522legatolabs%255Bmcp%255D%2522%252C%2522legato%2522%252C%2522mcp%2522%252C%2522--sandbox%2522%252C%2522docker%2522%255D%257D) [![Add to Kiro](https://kiro.dev/images/add-to-kiro.svg)](https://kiro.dev/launch/mcp/add?name=legato&config=%7B%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22--from%22%2C%22legatolabs%5Bmcp%5D%22%2C%22legato%22%2C%22mcp%22%2C%22--sandbox%22%2C%22docker%22%5D%2C%22disabled%22%3Afalse%2C%22autoApprove%22%3A%5B%5D%7D)
  (these run a desktop container with `uvx`).
- **Other clients:** `{"mcpServers": {"legato": {"command": "legato", "args": ["mcp"]}}}`, with the same arguments
  as above.

| tool | what it does |
|---|---|
| `desktop_open(width=1280, height=800, url=None, app=None)` | opens a desktop (your screen with `--local`, else a fresh sandbox); returns its id and first screenshot. With `url` or `app` it starts that first and returns once the window has drawn (`app` is an app name on macOS, a command on Linux) |
| `desktop_act(desktop_id, action, x, y, text, keys, direction, amount, end_x, end_y)` | click, double_click, triple_click, right_click, type, key, scroll, drag or wait, in pixels of the latest screenshot; returns the settled screenshot and how long the screen took |
| `desktop_screenshot(desktop_id)` | the current screen |
| `desktop_read(desktop_id, max_chars=8000)` | in the background on a Mac only: the window's text from its Accessibility tree, as an outline (`- heading "Counter"`, `- text "Count: 1"`, `- button "+1"`); in a browser, the page with its title and address. Other desktops answer with an error |
| `desktop_shell(desktop_id, command, timeout_s=30)` | runs a shell command on the desktop's computer (the container, the sandbox, or yours with `--local` or `--background`, where it is not sandboxed); returns its output and exit code |
| `desktop_run_task(desktop_id, task, model, base_url, api_key_env, max_steps)` | hands a whole task to Legato's own agent on a desktop from `desktop_open`, with the model `legato setup` saved unless you name one; returns the answer, the run report and the final screen |
| `desktop_close(desktop_id)` | stops the desktop (a container or sandbox is deleted; in the background, the app and its window stay open) |

- **`desktop_act` lets your agent drive.** Use it when your agent's own model (for example Claude in Claude Code) does
  the computer use.
- **Screenshots are JPEG** (quality 85), the same pixels in about a fifth of the bytes, as the harness sends them.
  `legato mcp --image-format png` sends lossless PNG.
- **On your own Mac, with other apps open:** `legato mcp --local --wait-scope app` waits on the front app's windows
  only, so a chat or video animating elsewhere doesn't hold every action to the wait's cap.
- **In the background on your Mac:** `legato mcp --background` forces it whatever was saved. It takes no app name:
  the app is `desktop_open(app=...)`'s, else the one `legato setup --app` saved, else Safari or Chrome, whichever
  is your default browser.
- **`desktop_read` checks text without a screenshot.** Your agent can check that a page says `Count: 1` as it would
  with Playwright's snapshot. It reads the window that `desktop_act` works in.
- **A fixed-sleep baseline:** `legato mcp --fixed-wait 2` sleeps after every action instead of waiting for the screen.
- **See what the agent did:** `legato mcp --record DIR` saves every screenshot the agent got, with a `log.jsonl` of its
  actions, under `DIR/<desktop_id>/`.
- **`desktop_shell` skips the screen** for what a command does faster: starting apps, reading files, checking results.
- **`desktop_run_task` delegates.** Use it to offload a whole GUI task to a cheaper open model while your agent keeps
  working.

If something fails, `legato doctor` (or `legato doctor --json`) says what is installed and set up, with the fix for
each. On a Mac it also takes one real window capture, as background mode does, and reports whether it works, stalled
(macOS's capture service is busy: Legato falls back to the slower `screencapture`) or failed. With a model on your
machine (`localhost`), it checks that the server answers.
