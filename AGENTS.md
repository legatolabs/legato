# AGENTS.md

Legato is a fast computer-use runtime for any model, with four ways in that share one agent: the Mac app
(`app/macos`), `legato run` (terminal), `legato mcp` (MCP server for coding agents) and the Python package
(`legato.run`, `legato.Harness`, `legato.attach`). `legato setup` saves the model and desktop the last three use.
`legato rl` runs RL rollouts. PyPI name `legatolabs`, import name `legato`.

## Work on this repo

- Setup: `pip install -e ".[dev]"`
- Test: `pytest -q`. No network, model or sandbox is needed. Tests that need Xvfb or Docker skip without them
  (`LEGATO_TEST_DOCKER_IMAGE=<image>` runs the Docker one).
- Site: `cd site && npm ci && node build.mjs`. It renders `docs/*.md` into `site/dist`.
- Layout:
  - `legato/harness.py`: the loop.
  - `legato/config.py`: the saved model and desktop; `legato/setup.py`: `legato setup`; `legato/agent.py`: the agent
    each desktop gets (`agent_for`) and `legato.run`, which `legato run` and `desktop_run_task` use too.
  - `legato/env.py`: desktops (`attach`: E2B, `shell=`, screenshot/execute); `legato/local.py`: this computer.
  - `legato/providers.py`: `--sandbox` choices (e2b, local, docker, your own function).
  - `legato/settle.py`: waiting for the screen.
  - `legato/mcp_server.py`: the MCP server; `legato/doctor.py`: `legato doctor`.
  - `app/macos/`: Legato for Mac (Swift, menu bar) and `legato/app_engine.py`, its engine; `build.sh` for this Mac,
    `release.sh` for a notarized `.dmg` (the `mac-app` workflow runs it on each `v*` tag).
  - `legato/rl/`: rollouts and benchmarks.
  - `docker/desktop/`: the desktop image for `--sandbox docker`.
  - `plugins/legato/` (the Claude Code plugin, listed by `.claude-plugin/marketplace.json`; its skills and LICENSE
    are copies of the root ones), `.cursor-plugin/`, `plugin.json`, `mcp.json`, `gemini-extension.json`, `mcpb/`,
    `server.json`: how editors and registries install the MCP server. Keep their versions, and the plugin's pinned
    package, in step with `pyproject.toml` (`tests/test_versions.py`).
  - `docs/`: user docs. `docs/setup.md` is what "Set up Legato for me: https://legato.computer/setup" reads; keep
    its steps runnable by a coding agent.
  - `personas/`: who uses the harness, the MCP and the Mac app, most likely first.
  - `site/`: legato.computer.
- Rules:
  - Keep public APIs typed.
  - Keep docs short.
  - Every number in the docs comes from `docs/benchmarks.md`, which has its raw data in `benchmarks/results/`.

## Use Legato from another project

Read [llms.txt](llms.txt) first. It is a short index with install, the minimal example and the common mistakes.
