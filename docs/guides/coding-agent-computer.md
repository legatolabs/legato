# Give Claude Code, Cursor or Codex a computer

Coding agents can read and write files, but they can't see or click a GUI. Legato's MCP server gives them a desktop:
the agent opens it, clicks and types, and gets a screenshot back after every action, as soon as the screen settles.
Use it to check the app you are building, fill in a web form, or hand a whole GUI task to a cheaper open model.

Pick the desktop, then add the server to your client. The safest default is a container on your own machine, which
needs only Docker and keeps the agent off your real screen.

```bash
pip install "legatolabs[mcp,local]"

claude mcp add legato -- legato mcp --sandbox docker        # Claude Code
codex mcp add legato -- legato mcp --sandbox docker         # Codex CLI
```

In Claude Code you can install the plugin instead, which adds the same server and a skill: `/plugin install legato
--marketplace legatolabs/legato`. One click for Cursor, VS Code and Kiro (these run the server with `uvx`):

[![Add to Cursor](https://cursor.com/deeplink/mcp-install-dark.svg)](https://cursor.com/link/mcp/install?name=legato&config=eyJjb21tYW5kIjoidXZ4IiwiYXJncyI6WyItLWZyb20iLCJsZWdhdG9sYWJzW21jcF0iLCJsZWdhdG8iLCJtY3AiLCItLXNhbmRib3giLCJkb2NrZXIiXX0%3D) [![Install in VS Code](https://img.shields.io/badge/VS_Code-Install_Legato-0098FF?logo=visualstudiocode&logoColor=white)](https://vscode.dev/redirect?url=vscode%3Amcp%2Finstall%3F%257B%2522name%2522%253A%2522legato%2522%252C%2522type%2522%253A%2522stdio%2522%252C%2522command%2522%253A%2522uvx%2522%252C%2522args%2522%253A%255B%2522--from%2522%252C%2522legatolabs%255Bmcp%255D%2522%252C%2522legato%2522%252C%2522mcp%2522%252C%2522--sandbox%2522%252C%2522docker%2522%255D%257D) [![Add to Kiro](https://kiro.dev/images/add-to-kiro.svg)](https://kiro.dev/launch/mcp/add?name=legato&config=%7B%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22--from%22%2C%22legatolabs%5Bmcp%5D%22%2C%22legato%22%2C%22mcp%22%2C%22--sandbox%22%2C%22docker%22%5D%2C%22disabled%22%3Afalse%2C%22autoApprove%22%3A%5B%5D%7D)

Or add it by hand. For Cursor (`~/.cursor/mcp.json`), Claude Desktop, Windsurf or Gemini CLI (`settings.json`):

```json
{"mcpServers": {"legato": {"command": "legato", "args": ["mcp", "--sandbox", "docker"]}}}
```

For VS Code (`.vscode/mcp.json`): `{"servers": {"legato": {"type": "stdio", "command": "legato", "args": ["mcp",
"--sandbox", "docker"]}}}`.

| `args` | the agent gets |
|---|---|
| `["mcp", "--sandbox", "docker"]` | a fresh Linux desktop container with a terminal and Firefox |
| `["mcp", "--local"]` | your own screen (macOS, Windows, Linux): it moves your real mouse |
| `["mcp", "--sandbox", "my_desktop.py:desktop"]` | your sandbox: SSH, Daytona, a VM ([how](../sandboxes.md)) |
| `["mcp"]` with `E2B_API_KEY` set | a cloud desktop on E2B |

Then ask, for example: "Open http://host.docker.internal:3000 in Firefox on the Legato desktop and check that the login
form works." From inside the container, `host.docker.internal` is your machine, so the agent can test the app you are
running. We ran this flow with Claude Code end to end: it started Firefox from the desktop's terminal, filled in a
signup form served from the host, and reported the page's validation message. The tools are listed on the
[MCP page](../mcp.md).
