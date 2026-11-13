# AGENTS.md

Legato is a computer-use harness: `legato.Harness` (the agent loop), `legato.attach` (desktops), `legato rl` (RL
rollouts), `legato mcp` (MCP server). PyPI name `legatolabs`, import name `legato`.

## Work on this repo

- Setup: `pip install -e ".[dev]"`
- Test: `pytest -q`. No network, model or sandbox is needed. One test needs Xvfb and skips without it.
- Site: `cd site && npm ci && node build.mjs`. It renders `docs/*.md` into `site/dist`.
- Layout:
  - `legato/harness.py`: the loop.
  - `legato/env.py`: desktops.
  - `legato/settle.py`: waiting for the screen.
  - `legato/rl/`: rollouts and benchmarks.
  - `legato/mcp_server.py`: the MCP server.
  - `docs/`: user docs.
  - `site/`: legato.computer.
- Rules:
  - Keep public APIs typed.
  - Keep docs short.
  - Every number in the docs comes from `docs/benchmarks.md`, which has its raw data in `benchmarks/results/`.

## Use Legato from another project

Read [llms.txt](llms.txt) first. It is a short index with install, the minimal example and the common mistakes.
