# Which desktops and sandboxes Legato can drive

E2B Desktop works out of the box, and so does any sandbox you can screenshot and send input to. OSWorld's Docker VMs
work too, for benchmarks and RL.

```python
import legato
from e2b_desktop import Sandbox

env = legato.attach(Sandbox.create())                         # E2B Desktop; E2B_API_KEY in the environment
env = legato.attach(screenshot=grab_png, execute=run_action)  # any other sandbox
```

- **E2B Desktop.** Legato uploads a small helper. Each action is then one remote call: the helper runs it with
  xdotool, polls the framebuffer until the screen stops changing, and returns the settled screenshot. If the helper
  cannot run, Legato falls back to the E2B SDK and waits from the client. It also turns off caret blinking, which
  would keep any wait from settling.
- **Any sandbox.** `grab_png()` returns PNG bytes. `run_action(action)` receives a `legato.Action` in screen pixels:
  `type` (click, double_click, right_click, type, key, scroll, drag or wait), `x`, `y`, `x2`, `y2`, `text`, `keys`
  (xdotool names such as `ctrl+s`), `direction` and `amount`. The wait then runs on the client (`legato.Waiter`), one
  screenshot per poll, never longer than the 2 s sleep it replaces.
  `legato.env.xdotool_cmds(action)` gives the xdotool command for an action, if your sandbox has a shell and X11.
- **OSWorld VMs.** `legato rl collect --backend osworld --osworld PATH_TO_OSWORLD`, on a Linux host with KVM. Legato
  replaces OSWorld's fixed waits with a ready signal and adds a fast reset that keeps the VM up and restores the task
  state. See [rl.md](rl.md).
