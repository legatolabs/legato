---
name: desktop
description: Use the Legato MCP desktop tools to see and operate a GUI, such as testing a web or desktop app you are building, filling in a form, or checking what a page really looks like. Use when a task needs eyes and clicks on a screen, not just files and a shell.
license: Apache-2.0
---

# Using the Legato desktop

The `legato` MCP server gives you a desktop: by default a Linux container on the user's machine with a terminal and
Firefox (`legato mcp --sandbox docker`). Each action returns the new screenshot once the screen has stopped changing.

1. **Open once.** Call `desktop_open` and keep its `desktop_id` for the whole task.
2. **Start the app with the desktop.** `desktop_open(url="http://host.docker.internal:3000")` or
   `desktop_open(app="...")` returns once its window has drawn. In the container, `host.docker.internal` is the
   user's machine, so a dev server they run on port 3000 is reachable there. `desktop_shell` starts anything else and
   is the fast way to read files, check logs and confirm results.
3. **Act.** `desktop_act` with `click`, `double_click`, `right_click` (`x`, `y`), `type` (`text`), `key` (`keys`, such
   as `ctrl+l` or `Return`), `scroll` (`direction`, `amount`) or `drag`. Coordinates are pixels of the latest
   screenshot. You do not need `desktop_screenshot` after an action: the action already returns the settled screen.
4. **Check what you see.** Judge the result from the screenshot, and say what you saw when you report back.
5. **Close.** Call `desktop_close` when you are done.

- **Long GUI chores:** `desktop_run_task` hands a whole task to Legato's own loop and model (set in the server's
  environment) and returns its report and the final screen.
- **The user's own screen** (`legato mcp --local`): these are their real windows. To do a task there, call
  `desktop_run_task(task=...)` once; it opens the desktop itself. Do not close windows or change settings unless
  asked, and ask before anything destructive. On macOS, risky actions wait for the user's Allow in a dialog you
  cannot answer.
- **Setup problems:** run `legato doctor --json` in a shell.
