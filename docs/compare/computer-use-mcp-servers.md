# Computer-use MCP servers for coding agents, compared

These give Claude Code, Codex, Cursor or another MCP client a desktop to see and operate. They differ most in where
the desktop runs and how they see the screen. Checked against each project's own docs on 13 November 2026.

| project | the desktop | sees the screen through | after an action | own model loop | license |
|---|---|---|---|---|---|
| [Legato](https://github.com/legatolabs/legato) | a Docker container on your machine, your own screen (macOS, Windows, Linux), any Linux sandbox you can run a command in, or E2B | screenshots and pixel coordinates | returns once the screen stops changing, with the screenshot | yes: `desktop_run_task` hands a task to any OpenAI-compatible model | Apache-2.0 |
| [Claude Code computer use](https://code.claude.com/docs/en/computer-use) | your own screen (CLI: macOS) | screenshots | not stated | no | proprietary; Pro, Max or Ultra plan |
| [Cua Driver](https://github.com/trycua/cua) | your own computer, in the background (macOS, Windows, Linux) | accessibility tree and screenshots | waits up to 300 ms for a new window, menu or dialog | no | MIT |
| [Cua `cua mcp`](https://github.com/trycua/cua) | local containers and VMs, or your own cloud account | not stated | not stated | no | MIT |
| [Open Computer Use](https://github.com/iFurySt/open-computer-use) | your own computer (macOS; Windows and Linux experimental) | accessibility tree and screenshots | Linux: a fixed 120 ms, then a snapshot | no | MIT |
| [computer-use-mcp](https://github.com/domdomegg/computer-use-mcp) | your own computer | screenshots and coordinates | returns `ok`; the model takes a new screenshot | no | MIT |
| [munim-computer-use](https://github.com/munimtechnologies/munim-computer-use) | your own computer, in the background (macOS, Windows, Linux) | accessibility tree, OCR fallback | optional: until accessibility notifications stop | no | Apache-2.0 |
| [Windows-MCP](https://github.com/CursorTouch/Windows-MCP) | your Windows PC, or another one over HTTP | Windows UI Automation tree, optional screenshot | no automatic wait; `Wait` and `WaitFor` tools | no | MIT |
| [Peekaboo](https://github.com/openclaw/Peekaboo) | your own Mac, in the background | accessibility tree and screenshots | no automatic wait; `wait_for` on click | yes: `peekaboo agent` | MIT |
| [MacOS-MCP](https://github.com/CursorTouch/MacOS-MCP) | your Mac, or another one over HTTP | accessibility tree, then coordinates | no automatic wait; `Wait` tool | no | MIT |
| [Terminator](https://github.com/mediar-ai/terminator) | your Windows PC or a headless Windows VM | accessibility tree | no automatic wait; `wait_for_element` tool | no | MIT |

## Which to use

- **Keep the agent off your own screen, with nothing to set up but Docker:** Legato (`legato mcp --sandbox docker`).
  For VMs or your own cloud account, also look at Cua's `cua mcp`.
- **Test the app you are building:** Legato's container reaches your dev server at `host.docker.internal`
  ([guide](../guides/coding-agent-computer.md)).
- **Work in your real Mac or Windows apps without taking over your pointer:** Cua Driver, munim, Open Computer Use
  or Peekaboo. They act through the accessibility tree and send input in the background. Legato's `--local` mode moves
  your real mouse.
- **Native Windows automation:** Windows-MCP or Terminator.
- **Hand a long GUI task to a cheaper model,** or measure and train agents: Legato (`desktop_run_task`,
  [benchmarks](../benchmarks.md), [RL rollouts](../rl.md)).
- **No install, on a Claude plan, on a Mac:** Claude Code's built-in computer use.

Legato works from screenshots and pixel coordinates, the way vision-language models are trained to act. That works on
any app, including ones with a poor accessibility tree, but it gives the model no element IDs.
