# Legato MCP server: give your coding agent its own computer

Add Legato to Claude Code, Cursor or any other MCP client, and your agent gets a computer to use: a desktop container on
your machine, your own screen, a sandbox you already run, or a fresh desktop in the cloud. It can open apps, click, type and see the screen. Use it to
test a GUI you are building, check a page in a real browser, or hand a whole desktop task to a cheaper open model.
Each action comes back with a screenshot as soon as the screen stops changing, so your agent never waits longer than
it has to.

```bash
pip install "legatolabs[mcp,local]"   # [mcp,e2b] for E2B desktops

claude mcp add legato -- legato mcp --sandbox docker                 # a desktop container on your machine
claude mcp add legato -- legato mcp --local                          # your own screen
claude mcp add legato -- legato mcp --sandbox my_desktop.py:desktop  # your sandbox: SSH, Daytona, a VM
claude mcp add legato -e E2B_API_KEY=your_key -- legato mcp          # a cloud desktop on E2B
```

- **A desktop container:** needs only Docker. The agent gets its own Linux desktop with a terminal and Firefox, and
  can't touch your screen.
- **Your own screen:** macOS, Windows, or Linux on X11. The agent moves your real mouse and keyboard; move the mouse
  into a corner of the screen to stop it.
- **Your sandbox:** a few lines of Python that start it and run a command in it ([how](sandboxes.md)).
- **Claude Desktop:** download [legato.mcpb](https://github.com/legatolabs/legato/releases/latest/download/legato.mcpb)
  and open it, then choose where the desktop runs (docker, local or e2b) in the extension's settings.
- **Claude Code plugin:** `/plugin install legato --marketplace legatolabs/legato` adds the server (Docker) and a skill.
- **One click:** [![Add to Cursor](https://cursor.com/deeplink/mcp-install-dark.svg)](https://cursor.com/link/mcp/install?name=legato&config=eyJjb21tYW5kIjoidXZ4IiwiYXJncyI6WyItLWZyb20iLCJsZWdhdG9sYWJzW21jcF0iLCJsZWdhdG8iLCJtY3AiLCItLXNhbmRib3giLCJkb2NrZXIiXX0%3D) [![Install in VS Code](https://img.shields.io/badge/VS_Code-Install_Legato-0098FF?logo=visualstudiocode&logoColor=white)](https://vscode.dev/redirect?url=vscode%3Amcp%2Finstall%3F%257B%2522name%2522%253A%2522legato%2522%252C%2522type%2522%253A%2522stdio%2522%252C%2522command%2522%253A%2522uvx%2522%252C%2522args%2522%253A%255B%2522--from%2522%252C%2522legatolabs%255Bmcp%255D%2522%252C%2522legato%2522%252C%2522mcp%2522%252C%2522--sandbox%2522%252C%2522docker%2522%255D%257D) [![Add to Kiro](https://kiro.dev/images/add-to-kiro.svg)](https://kiro.dev/launch/mcp/add?name=legato&config=%7B%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22--from%22%2C%22legatolabs%5Bmcp%5D%22%2C%22legato%22%2C%22mcp%22%2C%22--sandbox%22%2C%22docker%22%5D%2C%22disabled%22%3Afalse%2C%22autoApprove%22%3A%5B%5D%7D)
- **Other clients** (Cursor, Windsurf, Claude Desktop): `{"mcpServers": {"legato": {"command": "legato", "args":
  ["mcp", "--local"]}}}`, with the same arguments as above.

| tool | what it does |
|---|---|
| `desktop_open(width=1280, height=800)` | opens a desktop (your screen with `--local`, else a fresh sandbox); returns its id and first screenshot |
| `desktop_act(desktop_id, action, x, y, text, keys, direction, amount, end_x, end_y)` | click, double_click, triple_click, right_click, type, key, scroll, drag or wait, in pixels of the latest screenshot; returns the settled screenshot and how long the screen took |
| `desktop_screenshot(desktop_id)` | the current screen |
| `desktop_run_task(desktop_id, task, model, base_url, api_key_env, max_steps)` | hands a whole task to `legato.Harness` with any OpenAI-compatible model (default: Qwen3.8-27B on OpenRouter, key from `OPENROUTER_API_KEY`); returns the run report and the final screen |
| `desktop_close(desktop_id)` | stops the desktop |

- **`desktop_act` lets your agent drive.** Use it when your agent's own model (for example Claude in Claude Code) does
  the computer use.
- **See what the agent did:** `legato mcp --record DIR` saves every screenshot the agent got, with a `log.jsonl` of its
  actions, under `DIR/<desktop_id>/`.
- **`desktop_run_task` delegates.** Use it to offload a whole GUI task to a cheaper open model while your agent keeps
  working.
