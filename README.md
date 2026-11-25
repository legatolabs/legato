# Legato

The fast, open-source computer-use runtime. Bring any model.

![Legato enters three invoices from a text file into a Windows desktop app](https://legato.computer/media/invoice-desk-2x.gif)

<sub>A real run on a Windows desktop, shown at 2x with the real-time clock: Legato reads three invoices from Notepad and
enters them into a desktop app that has no API.</sub>

## Start

Tell Claude Code, Codex or Cursor:

> Set up Legato for me: https://legato.computer/setup

Or in a terminal:

```bash
pip install legatolabs
legato setup          # your model, where it works, your coding agents; offers a first task
```

Or on a Mac, with no code: [download Legato for Mac](https://github.com/legatolabs/legato/releases/latest/download/Legato.dmg),
hold Fn and say what to do.

Or add the MCP server to Cursor in one click (it needs [uv](https://docs.astral.sh/uv/) and Docker, and gives Cursor a
desktop container): [![Add to Cursor](https://cursor.com/deeplink/mcp-install-dark.svg)](https://cursor.com/link/mcp/install?name=legato&config=eyJjb21tYW5kIjoidXZ4IiwiYXJncyI6WyItLWZyb20iLCJsZWdhdG9sYWJzW21jcF0iLCJsZWdhdG8iLCJtY3AiLCItLXNhbmRib3giLCJkb2NrZXIiXX0%3D)

## One agent, four ways in

Choose your model once. Every way in runs the same agent with it.

| | How | What for |
|---|---|---|
| **Mac app** | Hold Fn, say the task | Your own apps, no code ([docs](https://legato.computer/docs/mac)) |
| **Terminal** | `legato run "On Wikipedia, look up the Eiffel Tower and tell me how tall it is"` | One task at a time, scripts, cron |
| **Your coding agent** | Claude Code, Codex or Cursor gets a computer (MCP); `legato setup` adds it | Test the app you are building, do the GUI part of a task ([docs](https://legato.computer/docs/mcp)) |
| **Python** | `legato.run("...")`, or `legato.Harness(...).run(task, desktop)` | Your own agent ([docs](https://legato.computer/docs/python)) |

**Any model** with an OpenAI-compatible API and vision: Qwen, Claude, GPT, Gemini, Grok, through OpenRouter or the
maker's own API, or on your own GPU with vLLM (Ollama and LM Studio serve the same API; untested so far), where nothing leaves your machine
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
| Claude Sonnet 5.5 | Anthropic's demo loop (our re-implementation, `legato.Harness.reference`) | 1.48x faster | 48/48 vs 48/48 |
| Qwen3.8-27B | OSWorld's Qwen agent | same | 71/96 vs 53/96 |

We wrote the 24 tasks (LibreOffice, a spreadsheet into a deck). The Claude row is one run of two attempts a task.
Check it on your own tasks: `examples/compare_with_reference.py`.

## Why it's fast

Most of a computer-use agent's time goes to waiting: on the model, and on the screen.

- It waits until the screen stops changing, not a fixed 2 s after every action.
- Up to three actions per turn, with one screenshot after them; on a Mac, one plan per turn, with a shell and
  AppleScript beside the screen where they are faster.
- Screenshots go as JPEG, and old ones are dropped in chunks so the prompt cache keeps hitting.
- A wall-clock timeout and a token budget on every model call, so nothing hangs.

## Safety and limits

- **Your own screen:** risky keys and typed commands (`rm`, `sudo`, `Remove-Item`, Cmd+Q, Alt+F4...) wait for your yes, and are refused when
  nobody can answer (an MCP server, a script; `--yes` allows them). It's a list, not a sandbox: a click on Delete
  isn't caught. Move the mouse into a corner to stop a run. `--sandbox docker` or a cloud desktop keeps the agent off
  your screen and files.
- **What it reads:** a page or document on screen can try to instruct the agent (prompt injection). In the background
  it works in your own Safari, signed in where you are; for sites you don't trust, use `--sandbox docker`.
- **Your data:** screenshots and the task go to the model you chose and nowhere else. Legato has no server of its
  own and sends no telemetry.
- **Limits you set:** `timeout_s` and `max_steps` bound a run, and `result.status` says how it ended
  ([Python](https://legato.computer/docs/python)).
- **What it is not good at yet:** tasks of many minutes across several apps, and anything that needs a drag in
  background mode (one app's window, Safari among browsers). It is an alpha: the API may change before 1.0.

## Docs

[Set up](https://legato.computer/setup) · [Mac app](https://legato.computer/docs/mac) ·
[Coding agents (MCP)](https://legato.computer/docs/mcp) · [Terminal and Python](https://legato.computer/docs/python) ·
[Desktops](https://legato.computer/docs/sandboxes) · [Models](https://legato.computer/docs/models) ·
[Benchmarks](https://legato.computer/docs/benchmarks) ·
[FAQ](https://legato.computer/docs/faq) · [Guides](https://legato.computer/docs/guides) ·
[Comparisons](https://legato.computer/docs/compare)

For coding agents: [llms.txt](https://legato.computer/llms.txt) · [AGENTS.md](AGENTS.md)

Apache-2.0.

<!-- mcp-name: io.github.legatolabs/legato -->
