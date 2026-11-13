# Legato MCP server: give your coding agent its own computer

Add Legato to Claude Code, Cursor or any other MCP client, and your agent gets a Linux desktop in the cloud. It can
open apps, click, type and see the screen, without touching your machine. Use it to test a GUI you are building, check
a page in a real browser, or hand a whole desktop task to a cheaper open model.

The desktops run on [E2B](https://e2b.dev), so you need an E2B API key. Each action comes back with a screenshot as
soon as the screen stops changing, so your agent never waits longer than it has to.

```bash
pip install "legatolabs[e2b,mcp]"
claude mcp add legato -e E2B_API_KEY=your_key -- legato mcp
```

Cursor, Windsurf, Claude Desktop and other clients:
`{"mcpServers": {"legato": {"command": "legato", "args": ["mcp"], "env": {"E2B_API_KEY": "your_key"}}}}`

Without installing anything: use `uvx --from "legatolabs[e2b,mcp]" legato mcp` as the command.

| tool | what it does |
|---|---|
| `desktop_open(width=1280, height=800)` | starts a fresh desktop; returns its id and first screenshot |
| `desktop_act(desktop_id, action, x, y, text, keys, direction, amount, end_x, end_y)` | click, double_click, right_click, type, key, scroll, drag or wait, in pixels of the latest screenshot; returns the settled screenshot and how long the screen took |
| `desktop_screenshot(desktop_id)` | the current screen |
| `desktop_run_task(desktop_id, task, model, base_url, api_key_env, max_steps)` | hands a whole task to `legato.Harness` with any OpenAI-compatible model (default: Qwen3.8-27B on OpenRouter, key from `OPENROUTER_API_KEY`); returns the run report and the final screen |
| `desktop_close(desktop_id)` | stops the desktop |

- **`desktop_act` lets your agent drive.** Use it when your agent's own model (for example Claude in Claude Code) does
  the computer use.
- **`desktop_run_task` delegates.** Use it to offload a whole GUI task to a cheaper open model while your agent keeps
  working.
