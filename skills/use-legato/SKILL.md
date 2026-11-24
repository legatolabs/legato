---
name: use-legato
description: Set up Legato or run a computer-use task with it (any OpenAI-compatible vision model; one app's window on a Mac in the background, this screen, a Docker desktop, any sandbox). Use when the user asks to set up Legato, or when a task needs clicks, typing and reading on a real GUI.
license: Apache-2.0
---

# Use Legato

**Not set up yet?** Follow https://legato.computer/setup.md. In short: `pip install legatolabs` (or
`uv tool install legatolabs`), then `legato setup --json` to see what is missing. A model key already in the
environment is used; otherwise ask the user for one and pass it on stdin
(`printf '%s' "$KEY" | legato setup --yes --provider openrouter --api-key -`). Never invent a key.

**Run a task** with the saved model and desktop:

```bash
legato run "On Wikipedia, look up the Eiffel Tower and tell me how tall it is" --json
```

It prints one JSON object: `done`, `answer` (what the model found), `actions`, `wall_s`. On a Mac it works in one
app's window in the background (the app the task names, else the browser; `--app NAME` to choose), so the user keeps
using the Mac. `--screen` uses the whole screen, `--sandbox docker` a desktop container.

From Python:

```python
import legato
result = legato.run("Open example.com and tell me the page's heading")
print(result.done, result.answer, result.report())
```

## Notes

- **Choosing each part:** `legato.Harness(model=..., base_url=..., api_key=...).run(task, desktop)`, with
  `legato.local()`, `legato.local(background="Safari")`, `legato.providers.docker(w, h)` or
  `legato.attach(shell=fn)` as the desktop. PyPI name `legatolabs`, import `legato`.
- **Coordinates:** Claude models use pixels automatically; other models a 0-1000 grid. Pass `coords=` for a model you
  have not checked. Raise `max_tokens` if `result.report()` shows cut-off turns.
- **Driving a desktop yourself** (instead of handing over the task): the `legato` MCP server's `desktop_*` tools;
  `legato setup --mcp claude` adds it.
- **Setup problems:** `legato doctor --json` reports what is installed and set up, with the fix for each.
