# MCP personas (`legato mcp`: a computer for a coding agent)

The people most likely to use it, most important first.

## 1. Developer testing their own app with a coding agent
- **Who:** builds a web or desktop app in Claude Code, Cursor or Codex.
- **Wants:** the agent opens the app, sees the bug, fixes it and checks the fix, without the developer clicking around.
- **Uses:** `--sandbox docker` (their app at `host.docker.internal`), or `--local` / `--background`.
- **Cares about:** speed per action, the agent not breaking their real screen, and a one-command install.

## 2. Claude Code user without built-in computer use
- **Who:** on Linux, a Team plan, Bedrock or Vertex, or running agents in CI or in parallel, where Claude Code's own computer use isn't available.
- **Wants:** the same capability anywhere, with any number of desktops at once.
- **Cares about:** it working like the built-in one, only faster.
- **Gap:** the head-to-head benchmark (#21) to prove "faster".

## 3. Power user handing desktop chores to their agent
- **Who:** already lives in Claude Code and wants it to also do the GUI part of a task, such as a web form, an admin console or a spreadsheet.
- **Wants:** `--background` so they keep working while it runs; `desktop_run_task` to hand a whole task to a cheaper model.
- **Gap:** apps beyond Safari, Chrome and TextEdit in background mode.

## 4. Agent builder using other MCP clients
- **Who:** builds on Claude Desktop, Gemini CLI, Kiro, VS Code or their own MCP client.
- **Wants:** a desktop tool that plugs in without writing a loop.
- **Uses:** the packaged installs: `.mcpb`, the Gemini extension, the one-click links.
