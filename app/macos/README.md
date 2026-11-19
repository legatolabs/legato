# Legato for Mac

A menu bar app that runs Legato on your own Mac with your own model API.

```bash
app/macos/build.sh --open      # builds app/macos/build/Legato.app (Swift shell + this repo's .venv as the engine)
```

## Using it

- **Open:** tap Fn (🌐) on its own, or press ⌥⇧Space. Type the task and press Return. Legato starts where you were:
  the app in front, its window and any selected text go to the model with the task.
- **Watch:** a pill at the bottom of the screen shows what the agent is doing. A neon cursor glides to each target
  and names the control ("Click · Send").
- **Take over:** move the mouse, click, scroll or press a key and it pauses. When you resume, the model looks at the
  screen again before acting. Esc stops the run.
- **Risky clicks:** before clicking Send, Delete, Pay, Sign out and the like (read from the control's Accessibility
  label), and before cmd+Q, cmd+Delete or a typed `rm`/`sudo` (`legato/safety.py`), it asks first.
- **History:** every run is saved with a screenshot per turn, the actions, the time and the cost.

First launch asks for Accessibility, Screen Recording and Input Monitoring, and for a model in Settings (OpenRouter,
Anthropic, a local server or any OpenAI-compatible endpoint; the key is kept in Keychain).

## How it fits together

| Swift app (`Sources/Legato`) | Python engine (`legato/app_engine.py`) |
|---|---|
| Menu bar, command bar, HUD, ghost cursor, settings, history | The loop (`Harness.begin/decide/feed`) |
| Screenshots with ScreenCaptureKit, leaving out Legato's own windows | Input: `legato.local()`'s macOS driver, unchanged |
| Takeover, Fn and Esc (one listen-only event tap) | The run record: `run.json` and a JPEG per turn |
| The go-ahead before each action, the yes/no questions | |

The app starts `python -m legato app-engine` as its child (so the child uses Legato.app's permissions) and they talk
in JSON lines; the protocol is in the module docstring. Before every action the engine asks `will_act`. The app moves
the ghost cursor, checks the control under the target, holds the request while paused, and answers go, deny, stop or
replan.

- Runs: `~/Library/Application Support/Legato/runs/`
- Engine log: `~/Library/Logs/Legato/engine.log`
- `Legato --snapshots DIR` renders the HUD, command bar and setup window to PNGs without showing anything.

## Not yet

- A self-contained download: the engine is this repo's venv. Shipping needs a bundled Python, a Developer ID
  signature and notarization.
- The main display only. No voice, sandbox mode or background workstreams.
