# Legato

Open-source computer-use harness for any OpenAI-compatible model: Qwen, DeepSeek, Claude, or your own vLLM server.
It drives a desktop with a `computer` tool, waits for the screen instead of sleeping, and bounds every model call.

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

Or from the shell: `legato run "Open the terminal and print the date" --model qwen/qwen3.8-27b --base-url https://openrouter.ai/api/v1`

## Results

Same model, same desktops, 24 office tasks per run. [Method and raw data](docs/benchmarks.md).

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

- [Models](docs/models.md) · [Sandboxes](docs/sandboxes.md) · [MCP server](docs/mcp.md) · [RL rollouts](docs/rl.md) · [FAQ](docs/faq.md)
- Compare: [Anthropic's demo](docs/compare/anthropic-computer-use-demo.md) · [OSWorld's Qwen agent](docs/compare/osworld-qwen-agent.md) · [Agent S3](docs/compare/agent-s3.md) · [UI-TARS-desktop](docs/compare/ui-tars-desktop.md)
- Migrate: [from cua-agent](docs/migrate/from-cua-agent.md) · [from Bytebot](docs/migrate/from-bytebot.md)
- For coding agents: [llms.txt](llms.txt) · [AGENTS.md](AGENTS.md)

Apache-2.0. Hosted desktops and models: [legato.computer](https://legato.computer).

<!-- mcp-name: io.github.legatolabs/legato -->
