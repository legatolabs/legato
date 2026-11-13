# Legato

Open-source computer-use harness for any OpenAI-compatible model: Qwen, DeepSeek, Claude, or your own vLLM server.
It drives a desktop with a `computer` tool, waits for the screen instead of sleeping, and bounds every model call.
Bring your own computer: your own screen (macOS, Windows, Linux), any sandbox you already run, or a cloud desktop.

## Give your coding agent a computer

Claude Code, Codex, Cursor or any MCP client gets a desktop to look at and click: a container on your machine (needs
only Docker), your own screen, or a cloud desktop ([guide](https://legato.computer/docs/guides/coding-agent-computer)).

```bash
pip install "legatolabs[mcp]"
claude mcp add legato -- legato mcp --sandbox docker
```

![Claude Code checks a signup form on a Legato desktop](https://legato.computer/media/claude-code-legato.gif)

<sub>A real run, sped up: Claude Code opens the app on the host in Firefox, tries an invalid and a valid email, and
reports what the page said. Each frame is a screenshot the MCP server returned.</sub>

Or install the Claude Code plugin, which adds the server and a skill: `/plugin install legato --marketplace
legatolabs/legato`. One click for other editors (these run the same server with `uvx`):

[![Add to Cursor](https://cursor.com/deeplink/mcp-install-dark.svg)](https://cursor.com/link/mcp/install?name=legato&config=eyJjb21tYW5kIjoidXZ4IiwiYXJncyI6WyItLWZyb20iLCJsZWdhdG9sYWJzW21jcF0iLCJsZWdhdG8iLCJtY3AiLCItLXNhbmRib3giLCJkb2NrZXIiXX0%3D) [![Install in VS Code](https://img.shields.io/badge/VS_Code-Install_Legato-0098FF?logo=visualstudiocode&logoColor=white)](https://vscode.dev/redirect?url=vscode%3Amcp%2Finstall%3F%257B%2522name%2522%253A%2522legato%2522%252C%2522type%2522%253A%2522stdio%2522%252C%2522command%2522%253A%2522uvx%2522%252C%2522args%2522%253A%255B%2522--from%2522%252C%2522legatolabs%255Bmcp%255D%2522%252C%2522legato%2522%252C%2522mcp%2522%252C%2522--sandbox%2522%252C%2522docker%2522%255D%257D) [![Add to Kiro](https://kiro.dev/images/add-to-kiro.svg)](https://kiro.dev/launch/mcp/add?name=legato&config=%7B%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22--from%22%2C%22legatolabs%5Bmcp%5D%22%2C%22legato%22%2C%22mcp%22%2C%22--sandbox%22%2C%22docker%22%5D%2C%22disabled%22%3Afalse%2C%22autoApprove%22%3A%5B%5D%7D)

## Build an agent in Python

```bash
pip install "legatolabs[e2b]"      # PyPI name: legatolabs. Import name: legato.
```

```python
import os, legato
from e2b_desktop import Sandbox                       # needs E2B_API_KEY

agent = legato.Harness(model="qwen/qwen3.8-27b", base_url="https://openrouter.ai/api/v1",
                       api_key=os.environ["OPENROUTER_API_KEY"])
desk = Sandbox.create()
result = agent.run("Open the terminal and print the date", legato.attach(desk))
print(result.report())                                # where the time went
desk.kill()
```

`legato.local()` drives your own screen instead, `legato.providers.docker(w, h)` a desktop container on your
machine, and `legato.attach(shell=run)` any Linux sandbox you can run a command in, such as a VM
([sandboxes](https://legato.computer/docs/sandboxes)). With a model on your own GPU, nothing leaves your machine:

```bash
pip install "legatolabs[local]"
legato run "Open the terminal and print the date" --sandbox local --model Qwen/Qwen3.5-9B --base-url http://localhost:8000/v1
```

## Results

Same model, same desktops, 24 office tasks per run. [Method and raw data](https://legato.computer/docs/benchmarks).

| Model | Compared with | Time | Success |
|---|---|---|---|
| Claude Sonnet 5.5 | Anthropic's reference loop | 1.48x faster | 48/48 vs 48/48 |
| Qwen3.8-27B | OSWorld's Qwen agent | same | 71/96 vs 53/96 |

## How

- Waits until the screen stops changing instead of a fixed 2 s sleep.
- Up to 3 actions per turn, one screenshot after them.
- Drops old screenshots in chunks so the prompt cache keeps hitting.
- A wall-clock timeout and a token budget on every model call.

## More

- Guides: [a computer for your coding agent](https://legato.computer/docs/guides/coding-agent-computer) · [test a desktop app](https://legato.computer/docs/guides/desktop-apps) · [local model](https://legato.computer/docs/guides/local-model) · [office apps](https://legato.computer/docs/guides/office-tasks) · [why agents are slow](https://legato.computer/docs/guides/faster-agents)
- [Models](https://legato.computer/docs/models) · [Sandboxes](https://legato.computer/docs/sandboxes) · [MCP server](https://legato.computer/docs/mcp) · [RL rollouts](https://legato.computer/docs/rl) · [FAQ](https://legato.computer/docs/faq)
- Compare: [computer-use MCP servers](https://legato.computer/docs/compare/computer-use-mcp-servers) · [Anthropic's demo](https://legato.computer/docs/compare/anthropic-computer-use-demo) · [OSWorld's Qwen agent](https://legato.computer/docs/compare/osworld-qwen-agent) · [Agent S3](https://legato.computer/docs/compare/agent-s3) · [UI-TARS-desktop](https://legato.computer/docs/compare/ui-tars-desktop)
- Migrate: [from cua-agent](https://legato.computer/docs/migrate/from-cua-agent) · [from Bytebot](https://legato.computer/docs/migrate/from-bytebot)
- For coding agents: [llms.txt](https://legato.computer/llms.txt) · [AGENTS.md](https://github.com/legatolabs/legato/blob/main/AGENTS.md)

Apache-2.0. Hosted desktops and models: [legato.computer](https://legato.computer).

<!-- mcp-name: io.github.legatolabs/legato -->
