# Legato MCP server: cloud desktops for Claude Code, Cursor and other agents

**Short answer.** `legato mcp` is an MCP server. It gives any MCP-capable agent a Linux desktop (E2B) to look at and
act on. Each action returns as soon as the screen has settled, with the new screenshot attached. The agent never
sleeps or asks for extra screenshots.

```bash
pip install "legato[e2b,mcp]"
claude mcp add legato -e E2B_API_KEY=your_key -- legato mcp
```

Cursor, Windsurf, Claude Desktop and other clients:

```json
{"mcpServers": {"legato": {"command": "legato", "args": ["mcp"], "env": {"E2B_API_KEY": "your_key"}}}}
```

## Tools

| tool | what it does |
|---|---|
| `desktop_open(width=1280, height=800)` | starts a fresh desktop and returns its id and first screenshot |
| `desktop_act(desktop_id, action, x, y, text, keys, direction, amount, end_x, end_y)` | click, double_click, right_click, type, key, scroll, drag or wait. It returns the settled screenshot and how long the screen took. Coordinates are pixels of the latest screenshot. |
| `desktop_screenshot(desktop_id)` | the current screen |
| `desktop_run_task(desktop_id, task, model, base_url, api_key_env, max_steps)` | hands a whole task to `legato.Harness` with any OpenAI-compatible model (default: Qwen3.8-27B on OpenRouter, key read from `OPENROUTER_API_KEY`). It returns the run report and the final screen. |
| `desktop_close(desktop_id)` | stops the desktop |

Measured on E2B from a cloud container:
- `desktop_open` takes about 7 s.
- Each `desktop_act` returns in 0.7–0.8 s, of which 0.4–0.5 s is waiting for the screen to settle. That compares
  with the 2 s fixed sleep in Anthropic's reference loop.

## When to use which

- **`desktop_act` lets your agent drive.** Use it when your agent's own model (for example Claude in Claude Code) does
  the computer use.
- **`desktop_run_task` delegates.** Use it to offload a whole GUI task to a cheaper open model while your agent keeps
  working.
