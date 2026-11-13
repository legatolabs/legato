# Which desktops and sandboxes Legato can drive

**Short answer.** E2B Desktop works out of the box. So does any sandbox you can take a screenshot of and send input
to, through two functions. OSWorld's Docker VMs work too, for benchmarks and RL rollouts.

## E2B Desktop

```python
from e2b_desktop import Sandbox
import legato

desk = Sandbox.create()            # E2B_API_KEY in the environment
env = legato.attach(desk)
```

- Legato uploads a small helper into the sandbox.
- Each action then takes **one remote call**: the helper runs the action with xdotool, polls the local framebuffer
  every few milliseconds until the screen stops changing, and returns the settled screenshot.
- If the helper cannot run, Legato falls back to the E2B SDK's own calls and waits from the client side.
- It also turns off caret blinking. A blinking caret changes every frame, so no wait could ever settle.

## Any sandbox

```python
env = legato.attach(screenshot=grab_png, execute=run_action)
```

- `grab_png()` returns PNG bytes.
- `run_action(action)` receives a `legato.Action` in screen pixels:
  - `type`: click, double_click, right_click, type, key, scroll, drag or wait;
  - `x`, `y` (and `x2`, `y2` for drag);
  - `text` (type);
  - `keys` (key, xdotool names such as `ctrl+s` or `Return`);
  - `direction` and `amount` (scroll).
- The wait for the screen then runs on the client (`legato.Waiter`). Each poll is a screenshot round trip, so it is
  coarser than the in-sandbox path. It still stops as soon as the screen is quiet, and never waits longer than the
  2 s sleep it replaces.

`legato.env.xdotool_cmds(action)` gives the xdotool command line for an action, if your sandbox has a shell and X11.

## OSWorld VMs (benchmarks and RL)

- `legato rl collect --backend osworld --osworld PATH_TO_OSWORLD` runs tasks on OSWorld's Docker provider.
- You need a Linux host with KVM.
- Legato adds:
  - a ready signal in place of OSWorld's fixed waits;
  - a fast reset that keeps the VM up and restores the task state: 2.2 s instead of a 20.4 s snapshot revert.
- See [rl.md](rl.md).
