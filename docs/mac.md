# Legato for Mac

Hold Fn, say what to do, and watch Legato do it in the apps you already use, with the model you choose.

[Download Legato for Mac](https://github.com/legatolabs/legato/releases/latest/download/Legato.dmg) · Apple silicon,
macOS 14 or later · signed and notarized, and it updates itself.

## First launch

1. Drag Legato into Applications and open it. It lives in the menu bar.
2. Allow what it asks for: **Accessibility** (to click and type), **Screen Recording** (to see the screen) and
   **Input Monitoring** (for the Fn key and for noticing when you take over).
3. Choose a model in Settings: OpenAI, Claude, Gemini, Grok, OpenRouter, Together, Fireworks, Groq, Cerebras, or any
   OpenAI-compatible server, including one on your own Mac. The key is kept in your Keychain.

## Using it

- **Ask:** hold Fn and say the task, or tap Fn (or ⌥⇧Space) and type it. Legato starts where you are: the app in
  front, its window and any selected text go to the model with the task.
- **Watch:** a pill at the bottom of the screen says what it is doing, and a cursor glides to each target and names
  it ("Click · Send").
- **Take over:** move the mouse, click or press a key and it pauses; it looks at the screen again before it goes on.
  Esc stops it.
- **Risky actions wait for your yes:** clicking Send, Delete, Pay or Sign out, quitting apps, deleting files, and
  shell commands that delete, send or change settings.
- **Switch models** from the command bar (⌘1 to ⌘4, ⌘K for all).
- **Cost cap:** a task stops at $1 by default (Settings), counted from the cost the provider reports.
- **History:** every run is kept, with a screenshot per step, the actions, the time and the cost.

It uses the same agent as `legato run` on a Mac's screen: one plan per turn, with a shell and AppleScript beside the
screen where they are faster than clicking. Its choice of model is its own (in Settings); the terminal, Python and
your coding agents share theirs through `legato setup`.

## Where things are

- Runs: `~/Library/Application Support/Legato/runs/`
- Log: `~/Library/Logs/Legato/engine.log`
- Source: [`app/macos`](https://github.com/legatolabs/legato/tree/main/app/macos) (Swift) and
  [`legato/app_engine.py`](https://github.com/legatolabs/legato/blob/main/legato/app_engine.py) (the engine)

## Not yet

- The main display only.
- Background mode (working in one app while you use the Mac) is in the terminal, Python and MCP, not yet in the app.
