# Legato for Mac

A menu bar app that runs Legato on your own Mac with your own model API.

```bash
app/macos/build.sh --open      # builds app/macos/build/Legato.app (Swift shell + this repo's .venv as the engine)
```

How to use it: [docs/mac.md](../../docs/mac.md) (legato.computer/docs/mac). This page is for working on it.

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

## Releases

`release.sh` builds a self-contained, signed and notarized `Legato.dmg`: the Swift app plus its own Python engine
(python-build-standalone with `legato[local]` from this checkout). The `mac-app` workflow runs it on each `v*` tag and
attaches the `.dmg` and the Sparkle feed (`appcast.xml`) to the release, so installed apps update themselves.

## Not yet

- The main display only. No background mode (one app's window while you keep working) in the app yet; the terminal,
  Python and the MCP server have it.
