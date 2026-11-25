# Legato for Claude Code

Gives Claude Code a computer it can see and use. Claude opens a desktop, clicks and types in it, and gets the new
screenshot as soon as the screen stops changing. Use it to test the app you are building, see a bug the way a user
would, or do the GUI part of a task such as a web form, an admin console or a desktop app.

The desktop can be:

- **One app's window on your Mac, in the background** (the default on a Mac): Claude works in its own window of
  Safari, TextEdit or another app without moving your mouse or taking the screen, so you keep using the Mac.
- **Your whole screen** on macOS, Windows or Linux (the default on Windows and Linux): Claude uses your real mouse and
  keyboard. Move the mouse into a corner of the screen to stop it.
- **A desktop container** on your machine (Docker), which can't touch your screen or files.
- **A cloud desktop** (E2B), or **your own sandbox or VM**.

Legato is the open-source computer-use runtime ([legatolabs/legato](https://github.com/legatolabs/legato),
Apache-2.0).

## Install

```
/plugin install legato --marketplace legatolabs/legato
```

It needs [uv](https://docs.astral.sh/uv/): the server runs with `uvx`. To choose a desktop other than the default, run
`uvx legatolabs setup` once and pick one ([desktops](https://legato.computer/docs/sandboxes)). On a Mac, allow Claude
Code's terminal in System Settings → Privacy & Security, under both Screen Recording and Accessibility.

## What it adds

- **The `legato` MCP server**, with `desktop_open`, `desktop_act` (click, type, key, scroll, drag),
  `desktop_screenshot`, `desktop_read`, `desktop_wait`, `desktop_shell`, `desktop_run_task` and `desktop_close`
  ([reference](https://legato.computer/docs/mcp)).
- **Two skills:** `desktop`, on using the desktop tools well, and `use-legato`, on setting up Legato and running a
  whole task with a model of your choice.

Then ask Claude to use its Legato desktop, for example: "Open my app at http://localhost:3000 on your Legato desktop
and check that sign-up works."

## What it runs, fetches and sends

- **Runs** `legato mcp` from the `legatolabs` package on PyPI, at the version pinned in `.mcp.json`. uv downloads the
  package the first time. It uses the desktop that `legato setup` saved, or the default above.
- **On your Mac or your screen,** Claude sees and works in the windows it opens, and `desktop_shell` runs commands on
  your computer, as Claude Code's own shell does. Risky actions (quitting an app, a typed `rm` or `sudo`) are refused
  unless you start the server with `--yes`.
- **With a desktop container,** it fetches the image `ghcr.io/legatolabs/desktop` from GitHub's container registry (if
  that fails, Docker builds it from `docker/desktop` in the legato repo) and runs it on your machine. Claude then works
  inside the container.
- **Sends** screenshots and tool results back to Claude Code, as any MCP tool does.
- **Only if Claude calls `desktop_run_task`:** Legato's own agent runs the task with a model you choose, and sends the
  task and the desktop's screenshots to that model's provider (such as OpenRouter, or a server you name), with your
  key for it. Keys come from `legato setup`, which saves them in `~/.config/legato/config.json` readable by you only,
  or from the server's environment.
- Nothing else: no telemetry and no account.
