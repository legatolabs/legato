# Set up Legato

Legato is a fast, open-source computer-use agent: it uses a computer (clicks, types, reads the screen) with the model
you choose. Setup takes about two minutes.

**Before you start:** you need a model key (OpenRouter, Anthropic, OpenAI, another provider, or a model server of
your own with no key), and on a Mac, Accessibility and Screen Recording for the app that runs Legato (your terminal,
or the Mac app). `legato run` prints what each task cost when it ends. Setup saves your choices in
`~/.config/legato/config.json`, readable by you only; delete that file and `pip uninstall legatolabs` to remove
everything. Legato sends no telemetry: screenshots and the task go to the model you chose and nowhere else. Risky
actions (a typed `rm`, quitting an app) wait for your yes.

**On a Mac, with no code:** [download Legato for Mac](https://github.com/legatolabs/legato/releases/latest/download/Legato.dmg),
open it and follow its first screen. The rest of this page is for the terminal and for coding agents.

**In a terminal:**

```bash
pip install legatolabs     # or: uv tool install legatolabs, or pipx install legatolabs
legato setup
```

`legato setup` asks three things (which model and key, where it works, which coding agents get it), checks the model
with one call, and offers a first task you can watch. What it saves is used by `legato run`, by `legato mcp` and by
`legato.run()` in Python. Run it again to change anything; `legato doctor` shows what is set up.

**From a coding agent:** tell Claude Code, Codex or Cursor *"Set up Legato for me: https://legato.computer/setup"*.
It follows the steps below.

## Steps for coding agents

You are setting Legato up on your user's computer. Run the commands yourself; ask the user only where a step says so.

1. **Install.** Python 3.10 or newer. Use the first that works:
   `uv tool install legatolabs`, `pipx install legatolabs`, `python3 -m pip install --user legatolabs`
   (on Windows: `py -m pip install legatolabs`, or `python -m pip install legatolabs`).
   Check with `legato --version`. If `legato` is not found (pip says its folder is not on PATH, which is usual on
   Windows), use `python -m legato` in place of `legato` in every command below, and tell the user which folder to
   add to PATH; `python -m legato doctor` names it. Do not change their PATH without asking. (The PyPI name is `legatolabs`; there is no `pip install legato`.)
2. **See what is missing:** `legato setup --json`. It prints one JSON object: `ready`, `missing` (each with its fix),
   the model, the desktop, and the coding agents it found. Do step 3 even if `ready` is already `true` (a key in the
   environment counts): it saves the choice for the user's other terminals and coding agents.
3. **The model.** If a key is already in the environment (`OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`), run
   `legato setup --yes --mcp none`: it saves which variable to read, not the key, so that variable must also be set
   where the user runs Legato (their shell profile). Otherwise ask the user which provider they use and for a key;
   never make one up. Pass the key on stdin, so it stays out of the command line, and it is saved readable by the user
   only: `printf '%s' "$KEY" | legato setup --yes --mcp none --provider openrouter --api-key -`
   (PowerShell: `$KEY | legato setup --yes --mcp none --provider openrouter --api-key -`).
   Providers: `openrouter` (one key for Qwen, Claude, GPT and others; the default, with `qwen/qwen3.8-27b`),
   `anthropic`, `openai`, `gemini`, `xai`, `together`, `fireworks`, `groq`, `cerebras`, `local` (a model server on
   this machine: vLLM, Ollama or LM Studio; no key), `custom` (with `--base-url`). Add `--model NAME` to choose the
   model. The model must accept images.
4. **Where it works.** The default is right for most people: on a Mac, one app's window in the background (the user
   keeps using the Mac), elsewhere the whole screen: on Windows and Linux Legato uses the real mouse, keyboard and
   screen while a task runs, so tell the user before the first task. Ask only if the user mentioned something else:
   - `--desktop screen`: the Mac's whole screen.
   - `--desktop docker`: a desktop container on this machine that cannot touch their screen or files (needs Docker).
   - `--desktop e2b`: an E2B cloud desktop (`pip install "legatolabs[e2b]"` and `E2B_API_KEY`).
   - **Their own sandbox or VM** (any Linux machine with a screen that you can run a command in): a function that
     starts it and returns `legato.attach(shell=run)` ([Desktops](sandboxes.md)). Check it with
     `legato run --sandbox path/to/desktop.py:desktop "Open a terminal and type hello"`, then save it as the default:
     `legato setup --yes --desktop path/to/desktop.py:desktop`.
5. **Give yourself a computer too.** `legato setup --yes --mcp claude` (or `codex`, `cursor`, `claude-desktop`)
   adds Legato's MCP server to that client and keeps the rest of the setup. Add only the client you are, unless the
   user asks for more (`--yes` without `--mcp` adds every client it finds). Tell the user to restart the client (in
   Claude Code: a new session, or `/mcp`) so the `desktop_*` tools appear.
6. **macOS permissions** (skip this step on Windows and Linux). If `missing` lists Accessibility or Screen Recording, you cannot switch them on: tell the
   user which app to switch on (the app that runs you, such as Terminal, iTerm, Ghostty or VS Code) in System
   Settings → Privacy & Security → Accessibility, and Screen & System Audio Recording. The fix line has a command
   that opens the right pane. They may need to restart that app afterwards.
7. **Check it works:**
   `legato run "On Wikipedia, look up the Eiffel Tower and tell me how tall it is" --json`.
   It prints one JSON object; `done` should be `true` and `answer` should give the height. If it fails, `error` says
   why. On a Mac it works in a Safari window in the background, whatever the default browser, so the user can watch
   that window.
8. **Tell the user** what was set up (the model, where it works, which coding agents got it) and how to use it:
   `legato run "..."` in a terminal; in their coding agent, ask it to use its Legato desktop; in Python,
   `legato.run("...")`.

All in one, when a key is in the environment and the defaults suit:

```bash
legato setup --yes --mcp all && legato run "On Wikipedia, look up the Eiffel Tower and tell me how tall it is"
```

In Windows PowerShell, which has no `&&`, run the two commands one after the other.

## If something fails

- `legato doctor` (or `legato doctor --json`) lists what is installed and set up, with the fix for each.
- **"no model is set up yet"**: run `legato setup`, or export the key (`OPENROUTER_API_KEY=...`).
- **Clicks do nothing, or screenshots are only the wallpaper** (macOS): Accessibility and Screen Recording, as in
  step 6.
- **"... did not open within 30 s"** (background mode): the Mac is busy or the app is slow to start the first time;
  open the app once, then run again.
- **"... has no window on screen"** (background mode): the app is minimized or on another Space; bring one of its
  windows onto this desktop.
- **The model answers but never acts:** it may not accept images, or it may need `--coords pixels`
  ([models](models.md)).

Next: [Terminal and Python](python.md) · [Coding agents](mcp.md) · [Mac app](mac.md) · [Desktops](sandboxes.md)
