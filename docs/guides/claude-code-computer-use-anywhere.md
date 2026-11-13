# Claude Code computer use on Linux, Team plans, Bedrock, CI and in parallel

Claude Code's built-in `computer-use` server only appears on a Mac, on a Pro, Max or Ultra plan, signed in through
claude.ai, in an interactive session, and one session at a time
([Anthropic's docs](https://code.claude.com/docs/en/computer-use), checked 13 November 2026). If `computer-use`
doesn't show up in `/mcp`, add Legato's MCP server instead. It is open source and works wherever Claude Code runs:

```bash
pip install "legatolabs[mcp]"
claude mcp add legato -- legato mcp --sandbox docker
```

| you are | built-in `computer-use` | Legato |
|---|---|---|
| on Linux | not available in the CLI | `--sandbox docker`, or `--local` for your own X11 screen |
| on Windows, in the CLI | not available (the Desktop app has it) | `--local` for your screen, or `--sandbox docker` with Docker Desktop |
| on a Team or Enterprise plan | not available | works: it is a plain MCP server |
| using Bedrock, Vertex AI or Foundry | not available | works with any model Claude Code runs on |
| running `claude -p` in CI or a script | not available | works: allow the tools with `--allowedTools "mcp__legato__*"` |
| running several sessions at once | one session holds a lock on your screen | each `desktop_open` starts its own container |
| wanting the agent off your real screen | it controls your actual desktop | `--sandbox docker` gives it a separate Linux desktop |

The built-in server still has things Legato doesn't: per-app approval, hiding your other apps, and the iOS Simulator
on a Mac. On a Mac with an eligible plan, use whichever fits the task.

## In CI

```bash
claude -p "Start the app with npm run dev -- --host 0.0.0.0, open http://host.docker.internal:3000 on the Legato desktop, \
and check that the sign-up form submits." --allowedTools "mcp__legato__*,Bash"
```

The container reaches the runner's own ports at `host.docker.internal`. On Linux, that only reaches servers that
listen on `0.0.0.0`, not on `127.0.0.1` alone. `legato mcp --record DIR` keeps every
screenshot the agent saw, to upload as a build artifact.

More: [MCP server](../mcp.md) · [test a desktop app](desktop-apps.md) · [all computer-use MCP servers compared](../compare/computer-use-mcp-servers.md)
