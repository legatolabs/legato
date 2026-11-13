# AGENTS.md

Legato is a computer-use harness: `legato.Harness` (the agent loop), `legato.attach` (desktops), `legato rl` (RL
rollouts), `legato mcp` (MCP server). PyPI name `legatolabs`, import name `legato`.

## Work on this repo

- Setup: `pip install -e ".[dev]"`
- Test: `pytest -q`. No network, model or sandbox is needed. Tests that need Xvfb or Docker skip without them
  (`LEGATO_TEST_DOCKER_IMAGE=<image>` runs the Docker one).
- Site: `cd site && npm ci && node build.mjs`. It renders `docs/*.md` into `site/dist`.
- Layout:
  - `legato/harness.py`: the loop.
  - `legato/env.py`: desktops (`attach`: E2B, `shell=`, screenshot/execute); `legato/local.py`: this computer.
  - `legato/providers.py`: `--sandbox` choices (e2b, local, docker, your own function).
  - `legato/settle.py`: waiting for the screen.
  - `legato/mcp_server.py`: the MCP server; `legato/doctor.py`: `legato doctor`.
  - `legato/rl/`: rollouts and benchmarks.
  - `docker/desktop/`: the desktop image for `--sandbox docker`.
  - `.claude-plugin/`, `plugin.json`, `mcp.json`, `gemini-extension.json`, `mcpb/`, `server.json`: how editors and
    registries install the MCP server. Keep their versions in step with `pyproject.toml` (`tests/test_versions.py`).
  - `docs/`: user docs.
  - `site/`: legato.computer.
- Rules:
  - Keep public APIs typed.
  - Keep docs short.
  - Every number in the docs comes from `docs/benchmarks.md`, which has its raw data in `benchmarks/results/`.

## Use Legato from another project

Read [llms.txt](llms.txt) first. It is a short index with install, the minimal example and the common mistakes.
