# Changelog

## 0.1.5

- The MCP server tells the agent what kind of desktop it has (for docker: a terminal is open, and the user's apps are
  at `host.docker.internal`; for local: these are the user's real windows).
- `--sandbox docker` pulls or builds the image as the server starts, and says plainly when Docker is missing or not
  running. The image is published for arm64 too, and opens Firefox without first-run tabs.
- A Claude Desktop extension, `legato.mcpb`, attached to each release (Python 3.11 or later).
- A second skill, `desktop`, on using the desktop tools well.

## 0.1.4

- `legato doctor [--json]`: which sandboxes, extras and model keys are ready, with the fix for each.
- A Claude Code plugin in the repo: `/plugin install legato --marketplace legatolabs/legato` adds the MCP server
  (with `--sandbox docker`) and the use-legato skill. The repo also carries Agent Plugins files (`plugin.json`,
  `mcp.json`) for other editors.
- The MCP Registry entry now starts the server with `--sandbox docker`, so it needs no key.

## 0.1.3

- `--sandbox docker`: a fresh desktop container on this machine (`ghcr.io/legatolabs/desktop`: a terminal and
  Firefox), with apps on the host reachable at `http://host.docker.internal:PORT`. `--sandbox docker:IMAGE` for your
  own image. Also `legato.providers.docker(width, height)` from Python.
- `legato.providers` is importable from `legato`.

## 0.1.2

- Bring your own computer. `legato.local()` drives this computer's screen on macOS, Windows or Linux (X11), with
  `pip install "legatolabs[local]"`.
- `legato.attach(shell=run)` drives any Linux sandbox you can run a command in (Docker, SSH, Daytona, your own VMs),
  with one call per action.
- `legato run` and `legato mcp` take `--sandbox e2b|local|path/to/file.py:function`, and `legato mcp --local`.
- `legato run` on E2B now opens a 1280x800 desktop with a one-hour limit, and closes it at the end.

## 0.1.1

- `legato run --json` prints one JSON result, for scripts and coding agents.
- Clear errors when the E2B extra, `E2B_API_KEY` or the model key is missing.
- A `legatolabs` command alias, so `uvx legatolabs mcp` works.
- Ships `py.typed`.
- Listed on the MCP Registry as `io.github.legatolabs/legato`.

## 0.1.0

- `legato.Harness`: a computer-use loop for any OpenAI-compatible model. It has a ready-wait, chunked screenshot
  history, up to 3 actions per turn, bounded calls, a 4,096-token budget with a cut-off note, and per-phase timing.
- `legato.Harness.reference`: a re-implementation of Anthropic's computer-use reference loop, for comparison.
- `legato.attach`: E2B Desktop with a single-call in-sandbox wait, or any sandbox through screenshot and execute
  functions.
- `legato rl`: graded rollouts for computer-use RL on OSWorld VMs, with logprobs, GRPO groups, oversampling and
  per-batch profiles.
- `legato mcp`: an MCP server with cloud desktops (E2B) for any agent; each action returns the settled screenshot.
- `legato bench`: the Legato Office Bench (Excel-to-PowerPoint tasks with a python-pptx grader) and multi-arm reports.
