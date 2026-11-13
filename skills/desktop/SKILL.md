---
name: desktop
description: Use the Legato MCP desktop tools to see and operate a GUI, such as testing a web or desktop app you are building, filling in a form, or checking what a page really looks like. Use when a task needs eyes and clicks on a screen, not just files and a shell.
license: Apache-2.0
---

# Using the Legato desktop

The `legato` MCP server gives you a desktop: by default a Linux container on the user's machine with a terminal and
Firefox (`legato mcp --sandbox docker`). Each action returns the new screenshot once the screen has stopped changing.

1. **Open once.** Call `desktop_open` and keep its `desktop_id` for the whole task.
2. **Start the app.** In the container, the terminal is already open: type `firefox http://host.docker.internal:3000 &`
   and press Return (`desktop_act` with `action="type"`, then `action="key", keys="Return"`).
   `host.docker.internal` is the user's machine, so a dev server they run on port 3000 is reachable there.
3. **Act.** `desktop_act` with `click`, `double_click`, `right_click` (`x`, `y`), `type` (`text`), `key` (`keys`, such
   as `ctrl+l` or `Return`), `scroll` (`direction`, `amount`) or `drag`. Coordinates are pixels of the latest
   screenshot. You do not need `desktop_screenshot` after an action: the action already returns the settled screen.
4. **Check what you see.** Judge the result from the screenshot, and say what you saw when you report back.
5. **Close.** Call `desktop_close` when you are done.

- **Long GUI chores:** `desktop_run_task` hands a whole task to another model (any OpenAI-compatible endpoint; the key
  is read from the environment) and returns its report and the final screen.
- **The user's own screen** (`legato mcp --local`): these are their real windows. Do not close windows or change
  settings unless asked, and ask before anything destructive.
- **Setup problems:** run `legato doctor --json` in a shell.
