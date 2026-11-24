# Legato

The fast, open-source computer-use agent. Bring any model.

![Legato enters three invoices from a text file into a Windows desktop app](https://legato.computer/media/invoice-desk-2x.gif)

<sub>A real run on a Windows desktop, shown at 2x with the real-time clock: Legato reads three invoices from Notepad and
enters them into a desktop app that has no API.</sub>

## Start

Tell Claude Code, Codex or Cursor:

> Set up Legato for me: https://legato.computer/setup

Or in a terminal:

```bash
pip install legatolabs
legato setup          # your model, where it works, your coding agents; then a first task
```

Or on a Mac, with no code: [download Legato for Mac](https://github.com/legatolabs/legato/releases/latest/download/Legato.dmg),
hold Fn and say what to do.

## One agent, four ways in

Choose your model once. Every way in runs the same agent with it.

| | How | What for |
|---|---|---|
| **Mac app** | Hold Fn, say the task | Your own apps, no code ([docs](https://legato.computer/docs/mac)) |
| **Terminal** | `legato run "Find the next Caltrain from SF to Palo Alto"` | One task at a time, scripts, cron |
| **Your coding agent** | Claude Code, Codex or Cursor gets a computer (MCP); `legato setup` adds it | Test the app you are building, do the GUI part of a task ([docs](https://legato.computer/docs/mcp)) |
| **Python** | `legato.run("...")`, or `legato.Harness(...).run(task, desktop)` | Your own agent, RL rollouts ([docs](https://legato.computer/docs/python)) |

**Any model** with an OpenAI-compatible API and vision: Qwen, Claude, GPT, Gemini, Grok, through OpenRouter or the
maker's own API, or on your own GPU (vLLM, Ollama, LM Studio), where nothing leaves your machine
([models](https://legato.computer/docs/models)).

**Any computer** ([desktops](https://legato.computer/docs/sandboxes)):

- **In the background on your Mac** (the default there): it works in one app's window while you keep using the Mac.
  Your mouse and keyboard stay yours.
- **Your whole screen** on macOS, Windows or Linux.
- **A desktop container** (Docker), a cloud desktop (E2B), or any sandbox or VM you can run a command in.

```python
import legato

result = legato.run("On Wikipedia, look up the Eiffel Tower and tell me how tall it is")
print(result.answer)      # The Eiffel Tower is 330 metres (1,083 ft) tall.
print(result.report())    # done or failed, the turns, and where the time went
```

## Results

Same model, same desktops, 24 office tasks per run. [Method and raw data](https://legato.computer/docs/benchmarks).

| Model | Compared with | Time | Success |
|---|---|---|---|
| Claude Sonnet 5.5 | Anthropic's reference loop | 1.48x faster | 48/48 vs 48/48 |
| Qwen3.8-27B | OSWorld's Qwen agent | same | 71/96 vs 53/96 |

## Why it's fast

Most of a computer-use agent's time goes to waiting: on the model, and on the screen.

- It waits until the screen stops changing, not a fixed 2 s after every action.
- Up to three actions per turn, with one screenshot after them; on a Mac, one plan per turn, with a shell and
  AppleScript beside the screen where they are faster.
- Screenshots go as JPEG, and old ones are dropped in chunks so the prompt cache keeps hitting.
- A wall-clock timeout and a token budget on every model call, so nothing hangs.

## Docs

[Set up](https://legato.computer/setup) · [Mac app](https://legato.computer/docs/mac) ·
[Coding agents (MCP)](https://legato.computer/docs/mcp) · [Terminal and Python](https://legato.computer/docs/python) ·
[Desktops](https://legato.computer/docs/sandboxes) · [Models](https://legato.computer/docs/models) ·
[RL rollouts](https://legato.computer/docs/rl) · [Benchmarks](https://legato.computer/docs/benchmarks) ·
[FAQ](https://legato.computer/docs/faq) · [Guides](https://legato.computer/docs/guides) ·
[Comparisons](https://legato.computer/docs/compare)

For coding agents: [llms.txt](https://legato.computer/llms.txt) · [AGENTS.md](AGENTS.md)

Apache-2.0.

<!-- mcp-name: io.github.legatolabs/legato -->
