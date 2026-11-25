# Legato for Claude Code

Gives Claude Code a computer: a Linux desktop in a Docker container on your machine, with a terminal and Firefox.
Claude opens it, clicks and types in it, and gets the new screenshot as soon as the screen stops changing. Use it to
test the app you are building, see a bug the way a user would, or do the GUI part of a task such as a web form or an
admin console.

Legato is the open-source computer-use runtime ([legatolabs/legato](https://github.com/legatolabs/legato),
Apache-2.0).

## Install

```
/plugin install legato --marketplace legatolabs/legato
```

It needs [uv](https://docs.astral.sh/uv/) (the server runs with `uvx`) and Docker, running.

## What it adds

- **The `legato` MCP server**, with `desktop_open`, `desktop_act` (click, type, key, scroll, drag),
  `desktop_screenshot`, `desktop_read`, `desktop_wait`, `desktop_shell`, `desktop_run_task` and `desktop_close`
  ([reference](https://legato.computer/docs/mcp)).
- **Two skills:** `desktop`, on using the desktop tools well, and `use-legato`, on setting up Legato and running a
  whole task with a model of your choice.

Then ask Claude to open its Legato desktop, for example: "Open my app at http://host.docker.internal:3000 on your
Legato desktop and check that sign-up works."

## What it runs, fetches and sends

- **Runs** `legato mcp --sandbox docker` from the `legatolabs` package on PyPI, at the version pinned in `.mcp.json`.
  uv downloads the package the first time.
- **Fetches** the desktop image `ghcr.io/legatolabs/desktop` from GitHub's container registry (if that fails, Docker
  builds it from `docker/desktop` in the legato repo on GitHub) and starts it as a container on your machine. Claude works
  inside that container: it has no access to your screen, and none to your files unless you mount them yourself
  (`LEGATO_DOCKER_ARGS`). `desktop_shell` runs commands inside the container. Apps on your machine are reachable from
  it at `http://host.docker.internal:PORT`.
- **Sends** screenshots and tool results back to Claude Code, as any MCP tool does.
- **Only if Claude calls `desktop_run_task`:** Legato's own agent runs the task with a model you choose, and sends the
  task and the desktop's screenshots to that model's provider (such as OpenRouter, or a server you name), with your
  key for it. Keys come from `legato setup`, which saves them in `~/.config/legato/config.json` readable by you only,
  or from the server's environment.
- Nothing else: no telemetry and no account.

## Other ways to run it

Your whole screen, one app's window in the background on a Mac, or your own sandbox: install the package with
`pip install legatolabs` and run `legato setup`. See the [docs](https://legato.computer/docs/mcp).
