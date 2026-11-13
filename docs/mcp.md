# Legato MCP server: give your coding agent its own computer

Add Legato to Claude Code, Cursor or any other MCP client, and your agent gets a computer to use: your own screen, a
sandbox you already run, or a fresh desktop in the cloud. It can open apps, click, type and see the screen. Use it to
test a GUI you are building, check a page in a real browser, or hand a whole desktop task to a cheaper open model.
Each action comes back with a screenshot as soon as the screen stops changing, so your agent never waits longer than
it has to.

```bash
pip install "legatolabs[mcp,local]"   # [mcp,e2b] for E2B desktops

claude mcp add legato -- legato mcp --local                          # your own computer
claude mcp add legato -- legato mcp --sandbox my_desktop.py:desktop  # your sandbox: Docker, SSH, Daytona, a VM
claude mcp add legato -e E2B_API_KEY=your_key -- legato mcp          # a cloud desktop on E2B
```

- **Your own computer:** macOS, Windows, or Linux on X11. The agent moves your real mouse and keyboard; move the mouse
  into a corner of the screen to stop it.
- **Your sandbox:** a few lines of Python that start it and run a command in it ([how](sandboxes.md)).
- **Other clients** (Cursor, Windsurf, Claude Desktop): `{"mcpServers": {"legato": {"command": "legato", "args":
  ["mcp", "--local"]}}}`, with the same arguments as above.

| tool | what it does |
|---|---|
| `desktop_open(width=1280, height=800)` | opens a desktop (your screen with `--local`, else a fresh sandbox); returns its id and first screenshot |
| `desktop_act(desktop_id, action, x, y, text, keys, direction, amount, end_x, end_y)` | click, double_click, right_click, type, key, scroll, drag or wait, in pixels of the latest screenshot; returns the settled screenshot and how long the screen took |
| `desktop_screenshot(desktop_id)` | the current screen |
| `desktop_run_task(desktop_id, task, model, base_url, api_key_env, max_steps)` | hands a whole task to `legato.Harness` with any OpenAI-compatible model (default: Qwen3.8-27B on OpenRouter, key from `OPENROUTER_API_KEY`); returns the run report and the final screen |
| `desktop_close(desktop_id)` | stops the desktop |

- **`desktop_act` lets your agent drive.** Use it when your agent's own model (for example Claude in Claude Code) does
  the computer use.
- **`desktop_run_task` delegates.** Use it to offload a whole GUI task to a cheaper open model while your agent keeps
  working.
