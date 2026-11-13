# Which computers Legato can drive

Bring your own. Legato drives your own computer (macOS, Windows or Linux), any Linux sandbox you can run a command in
(Docker, SSH, Daytona, Modal, your own VMs), E2B cloud desktops, or anything you can screenshot and send input to.
Managed Windows and macOS cloud desktops are planned for Legato Cloud.

```python
import legato

env = legato.local()                                          # this computer: pip install "legatolabs[local]"
env = legato.attach(shell=run)                                # any Linux sandbox: run(cmd) returns stdout
env = legato.attach(Sandbox.create())                         # E2B: from e2b_desktop import Sandbox
env = legato.attach(screenshot=grab_png, execute=run_action)  # anything else
```

From the shell and for coding agents, `--sandbox` takes the same choices: `e2b` (the default), `local`, or your own
function as `path/to/file.py:function` or `module:function`. The function gets `(width, height)` and returns a
`legato.attach(...)` result, or `(handle, env)` so Legato can call `handle.kill()` (or `close`, `stop`, `delete`) at
the end. For example, a Docker container with a desktop:

```python
# my_desktop.py
import subprocess, legato

def desktop(width, height):
    cid = subprocess.run(["docker", "run", "-d", "your-desktop-image"], capture_output=True, text=True).stdout.strip()
    run = lambda cmd: subprocess.run(["docker", "exec", cid, "bash", "-lc", cmd], capture_output=True, text=True).stdout
    return (lambda: subprocess.run(["docker", "rm", "-f", cid])), legato.attach(shell=run)
```

`legato run "..." --sandbox my_desktop.py:desktop` or `legato mcp --sandbox my_desktop.py:desktop`. A plain function
works as the handle too: Legato calls it if it has none of those methods.

## This computer

`legato.local()` drives the screen you are looking at, with `mss` for screenshots and `pyautogui` for input. Pair it
with a model on your own GPU ([models](models.md#on-your-own-gpu)) and nothing leaves your machine.

- **It is your real mouse and keyboard.** Move the mouse into a corner of the screen to stop the agent. Use a spare
  account or a VM for anything you would not hand to a person.
- **macOS:** allow your terminal (or MCP client) under System Settings → Privacy & Security, in both Screen Recording
  and Accessibility. Screenshots are scaled to the logical screen size, and Ctrl shortcuts are sent as Cmd
  (`legato.local(mac_cmd=False)` turns that off).
- **Windows:** works as is.
- **Linux:** an X11 session (not Wayland) and `python3-tk`, which `pyautogui` needs.
- Only the primary screen is used.

## Any Linux sandbox

`legato.attach(shell=run)` needs `run(cmd)`, which runs a shell command in the sandbox and returns its stdout, and an
X display there with `python3`, `xdotool` and `scrot`. Legato installs a small helper in the sandbox. Each action is
then one call: the helper acts with xdotool, polls the framebuffer until the screen stops changing, and returns the
settled screenshot. E2B Desktop works the same way.

## Anything else

`grab_png()` returns PNG bytes. `run_action(action)` receives a `legato.Action` in screen pixels: `type` (click,
double_click, right_click, type, key, scroll, drag or wait), `x`, `y`, `x2`, `y2`, `text`, `keys` (xdotool names such
as `ctrl+s`), `direction` and `amount`. The wait then runs on the client (`legato.Waiter`), one screenshot per poll,
never longer than the 2 s sleep it replaces.

## OSWorld VMs

`legato rl collect --backend osworld --osworld PATH_TO_OSWORLD`, on a Linux host with KVM. Legato replaces OSWorld's
fixed waits with a ready signal and adds a fast reset that keeps the VM up and restores the task state. See
[rl.md](rl.md).
