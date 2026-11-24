# Desktops: which computers Legato can drive

Bring your own. Legato drives one app's window on your Mac in the background, your own screen (macOS, Windows or
Linux), a desktop container on your machine, a cloud sandbox (E2B built in; Daytona, Modal,
Namespace or any VM you can SSH into with a short recipe), E2B cloud desktops, or anything you can screenshot and send input to.

`legato setup` saves which one `legato run`, `legato mcp` and `legato.run()` use: on a Mac the default is the
background, elsewhere the whole screen.

```python
import legato

env = legato.local(background="Safari")                       # one app's window on a Mac, in the background
env = legato.local()                                          # this computer's whole screen
stop, env = legato.providers.docker(1280, 800)                # a desktop container on this machine
env = legato.attach(shell=run)                                # any Linux sandbox: run(cmd) returns stdout
env = legato.attach(Sandbox.create())                         # E2B: from e2b_desktop import Sandbox
env = legato.attach(screenshot=grab_png, execute=run_action)  # anything else
```

From the shell and for coding agents, `--sandbox` takes the same choices: `local`, `docker` (or
`docker:IMAGE`), `e2b` (with `pip install "legatolabs[e2b]"` and `E2B_API_KEY`), or your own function as `path/to/file.py:function` or `module:function`. The function gets `(width, height)` and returns a
`legato.attach(...)` result, or `(handle, env)` so Legato can call `handle.kill()` (or `close`, `stop`, `delete`) at
the end. For example, a container on another machine over SSH works the same way as this one:

```python
# my_desktop.py
import subprocess, legato

def desktop(width, height):
    cid = subprocess.run(["docker", "run", "-d", "ghcr.io/legatolabs/desktop"], capture_output=True, text=True).stdout.strip()
    run = lambda cmd: subprocess.run(["docker", "exec", cid, "sh", "-c", cmd], capture_output=True, text=True).stdout
    return (lambda: subprocess.run(["docker", "rm", "-f", cid])), legato.attach(shell=run)
```

`legato run "..." --sandbox my_desktop.py:desktop` or `legato mcp --sandbox my_desktop.py:desktop`. A plain function
works as the handle too: Legato calls it if it has none of those methods.

## This computer

`legato.local()` drives the screen you are looking at, with `mss` for screenshots and `pyautogui` for input. Pair it
with a model on your own GPU ([models](models.md#on-your-own-gpu)) and nothing leaves your machine.

- **It is your real mouse and keyboard.** Use a spare account or a VM for anything you would not hand to a person.
- **Stop it** by moving the mouse into a corner of the screen, or on macOS by holding Escape. It is checked before
  each action (not during a model call), and the run ends with `result.stopped` set.
- **It asks first** before an action on a short list (`legato/safety.py`), in the terminal, and a no is reported to
  the model as "not run":
  - keys: Cmd+Delete with any other modifier (Trash, Empty Trash), Cmd+Q or Ctrl+Q with any other modifier (quit,
    log out), Alt+F4, Shift+Delete, Ctrl+Alt+Delete;
  - typed text that is run (it ends with a newline, or Return is pressed after it): `rm`, `rmdir`, `unlink`,
    `shred`, `find -delete`, `sudo`, `doas`, `mkfs`, `dd of=`, `diskutil erase…`, `git push --force/--delete/--mirror`,
    `git reset --hard`, `git clean -f`, `> file`, `truncate`, and anything piped into a shell.

  A click is only coordinates, so a click on Delete, Send or Buy, a menu item, a drag to the Trash, the Delete key
  alone and a command behind an alias are **not** caught. `legato.local(confirm=fn)` takes your own
  `fn(description) -> bool`; `confirm=legato.safety.deny` refuses the list; `confirm=None`, `legato run --yes` or
  `LEGATO_CONFIRM=off` asks nothing. Without a terminal (a script's stdin is a pipe, an MCP server, CI) nothing is
  asked either.
- **macOS:** allow your terminal (or MCP client) under System Settings → Privacy & Security, in both Screen Recording
  and Accessibility. `legato doctor` shows whether each is on and which app to allow, and `legato.local()` prints
  the same when one is off. Screenshots are scaled to the logical screen size, and Ctrl shortcuts are sent as Cmd
  (`legato.local(mac_cmd=False)` turns that off). `legato.local(wait_scope="app")` makes the wait for the
  screen watch only the front app's windows, so another app animating (a terminal spinner, a video) doesn't hold
  each action to the wait's 2 s cap.
- **Windows:** works as is.
- **Linux:** an X11 session (not Wayland) and `python3-tk`, which `pyautogui` needs.
- Only the primary screen is used.
- **Tested** on every change to `legato/local.py`, on GitHub's macOS (Apple Silicon) and Windows Server runners:
  screenshots, clicks, typing, and Ctrl shortcuts sent as Cmd on macOS
  ([workflow](https://github.com/legatolabs/legato/blob/main/.github/workflows/local-desktop.yml)). Those screens are
  not Retina, so the Retina scaling is covered only by unit tests.

### In the background, while you keep working (macOS)

`legato.local(background="TextEdit")` works in one app's front window and leaves the rest of the Mac to you: your
pointer, the app in front and the clipboard stay yours. It is the default on a Mac: `legato run "..."` works in the
app the task names ("... in Notes", "... on Spotify"), else in your browser (Safari, unless your default is Chrome);
`--app NAME` picks one. In the MCP server: `desktop_open(app="Safari", url=...)`.

```python
env = legato.local(background="Safari")
env.open_url("https://example.com")
agent.run("Sign up with the name Ada Lovelace", env)
```

- **What the agent sees** is that window only, captured even when other windows cover it. Coordinates are points in
  the window.
- **Typing and keys** are posted to that app's process, not to the system. A shortcut that is a menu item (Cmd+S) presses
  the menu item, since an app in the back never sees its menus' keys; Cmd+A in a field selects its text.
- **Clicks go through Accessibility,** because a mouse click posted to an app in the back lands nowhere. The element at
  the point is pressed (buttons, links, checkboxes, menu items), focused (fields), or selected (rows), and in text the
  insertion point goes to that character; a double or triple click there selects the word or the line.
- **Scrolling** moves the scroll bar under the point; a page with none (Chrome) scrolls with the arrow keys.
- **If the app takes the front** (Chrome does when it opens a URL), it is given back to the app you were in.
- **Not possible in the background,** and reported to the model as "this desktop can't do that": drags, a click on
  something that can't be pressed, focused or selected (a canvas, an empty area), a click with keys held, copy, cut and
  paste (the clipboard is yours), and anything outside the app (the Dock, the menu bar, Spotlight, other apps).
- **Don't use the same app yourself** while it works: its keys go to whatever has the focus in that app. Give it its own
  app, or its own browser (Chrome while you use Safari).
- The window must stay on screen: not minimized, and on the current Space. Locking the Mac or sleep stops it. An app
  hidden with Cmd+H is shown again (behind your app); the agent's own Cmd+H and Cmd+M are not run.
- **One app per task.** A task that moves between apps needs the whole screen (`--screen`).
- **Firefox** works, but slowly in the background: use Safari or Chrome.
- Your stop signals (a corner, Escape) don't apply, since the mouse and keyboard are yours: stop it from the program
  that runs it. Risky keys and typed commands still wait for your yes.
- **Tested** with `tests/test_background.py` (any OS) and, on a Mac, `LEGATO_TEST_BACKGROUND=1 pytest
  tests/test_background_live.py`: TextEdit, Safari and Chrome in the back while Terminal stays in front, checking
  after each test that the front app, the pointer and the clipboard are unchanged.

## A desktop container on this machine

`--sandbox docker` starts a fresh container from `ghcr.io/legatolabs/desktop`: a small Linux desktop with a terminal and
Firefox ([Dockerfile](https://github.com/legatolabs/legato/blob/main/docker/desktop/Dockerfile)). It needs only
Docker: no key, no cloud, and the agent can't touch your own screen. The first use pulls the image, or builds it from
the repo. `--sandbox docker:IMAGE` uses your own image: one built `FROM ghcr.io/legatolabs/desktop` with your apps, or
any image with an X display on `:0`, `python3`, `xdotool` and `scrot`. `LEGATO_DOCKER_ARGS` adds `docker run`
arguments, such as `-v "$PWD:/home/user/project"` ([testing a desktop app](guides/desktop-apps.md)).

## Cloud sandboxes

E2B is built in (`--sandbox e2b`, with `pip install "legatolabs[e2b]"` and `E2B_API_KEY`). Any other provider works
through a short function: start a sandbox from `ghcr.io/legatolabs/desktop` (Debian with a screen, a terminal and
Firefox; amd64 and arm64), start its screen, and give Legato a way to run a command in it. Save one of the recipes
below as `~/.config/legato/desktop.py`, then:

```bash
legato run --sandbox ~/.config/legato/desktop.py:desktop "Open a terminal and type hello"   # check it
legato setup --desktop ~/.config/legato/desktop.py:desktop                                 # make it the default
```

Each task gets a fresh sandbox and deletes it at the end. To add your own apps, build an image `FROM
ghcr.io/legatolabs/desktop` and put its name in the recipe.

### Modal

`pip install modal`, then `modal setup`.

```python
import modal
import legato

SCREEN = "Xvfb :0 -screen 0 {w}x{h}x24 -nolisten tcp & sleep 1; fluxbox & xterm & exec sleep infinity"
READY = "for i in $(seq 100); do [ -e /tmp/.X11-unix/X0 ] && exit 0; sleep 0.1; done"

def desktop(width, height):
    app = modal.App.lookup("legato", create_if_missing=True)
    image = modal.Image.from_registry("ghcr.io/legatolabs/desktop:latest")
    sb = modal.Sandbox.create("sh", "-c", SCREEN.format(w=width, h=height), app=app, image=image, timeout=3600)

    def run(cmd):
        p = sb.exec("sh", "-c", cmd)
        out = p.stdout.read()
        p.wait()
        return out

    run(READY)
    return sb.terminate, legato.attach(shell=run)
```

### Daytona

`pip install "daytona[v0-sdk]>=2,<3"` and `DAYTONA_API_KEY`. Daytona needs a pinned image tag, not `latest`.

```python
import shlex
from daytona import Daytona
from daytona.v0 import CreateSandboxFromImageParams, Resources
import legato

IMAGE = "ghcr.io/legatolabs/desktop:9809376c1ae6ab5f95e105fea36cddcd9c9e26da"
SCREEN = "Xvfb :0 -screen 0 {w}x{h}x24 -nolisten tcp & sleep 1; fluxbox & xterm & exec sleep infinity"
READY = "for i in $(seq 100); do [ -e /tmp/.X11-unix/X0 ] && exit 0; sleep 0.1; done"

def desktop(width, height):
    daytona = Daytona()
    sb = daytona.v0.create(CreateSandboxFromImageParams(image=IMAGE, resources=Resources(cpu=2, memory=4)), timeout=0)

    def run(cmd):
        return sb.process.exec(f"sh -c {shlex.quote(cmd)}").result

    run(f"nohup sh -c {shlex.quote(SCREEN.format(w=width, h=height))} >/dev/null 2>&1 &")
    run(READY)
    return (lambda: daytona.v0.delete(sb)), legato.attach(shell=run)
```

### Namespace, or any VM you can SSH into

A Debian or Ubuntu machine you can reach with `ssh` and no password prompt. The first run installs the screen;
one shared connection keeps each action to a single round trip.

```python
import shlex, subprocess
import legato

HOST = "me@my-desktop-vm"
SSH = ["ssh", "-o", "ControlMaster=auto", "-o", "ControlPath=~/.ssh/legato-%C", "-o", "ControlPersist=10m", HOST]
INSTALL = ("command -v Xvfb >/dev/null || (sudo apt-get update -qq && "
           "sudo apt-get install -y -qq xvfb fluxbox xterm xdotool scrot python3)")
SCREEN = "Xvfb :0 -screen 0 {w}x{h}x24 -nolisten tcp & sleep 1; fluxbox & xterm & exec sleep infinity"
READY = "for i in $(seq 100); do [ -e /tmp/.X11-unix/X0 ] && exit 0; sleep 0.1; done"

def desktop(width, height):
    def run(cmd):
        return subprocess.run(SSH + [cmd], capture_output=True, text=True).stdout

    run(INSTALL)
    run(f"nohup sh -c {shlex.quote(SCREEN.format(w=width, h=height))} >/dev/null 2>&1 &")
    run(READY)
    return (lambda: run("pkill xterm; pkill fluxbox; pkill Xvfb")), legato.attach(shell=run)
```

On Namespace, create the machine with `nsc create`, use `["nsc", "ssh", "-T", INSTANCE_ID]` in place of `SSH`, and
delete it with `nsc destroy INSTANCE_ID --force` when you are done.

## Any Linux sandbox

`legato.attach(shell=run)` needs `run(cmd)`, which runs a shell command in the sandbox and returns its stdout, and an
X display there with `python3`, `xdotool` and `scrot`. Legato installs a small helper in the sandbox. Each action is
then one call: the helper acts with xdotool, polls the framebuffer until the screen stops changing, and returns the
settled screenshot. E2B Desktop works the same way. On x86_64 and arm64 the helper is a small static binary
([native/legato-step](https://github.com/legatolabs/legato/tree/main/native/legato-step)), copied to `/tmp` once per
sandbox, and `python3` and `scrot` are not needed. Elsewhere, or with `LEGATO_HELPER=python`, it is a Python script.

## Anything else

`grab_png()` returns PNG bytes. `run_action(action)` receives a `legato.Action` in screen pixels: `type` (click,
double_click, triple_click, right_click, type, key, scroll, drag or wait), `x`, `y`, `x2`, `y2`, `text`, `keys` (xdotool names such
as `ctrl+s`), `direction` and `amount`. The wait then runs on the client (`legato.Waiter`), one screenshot per poll,
never longer than the 2 s sleep it replaces.

## OSWorld VMs

`legato rl collect --backend osworld --osworld PATH_TO_OSWORLD`, on a Linux host with KVM. Legato replaces OSWorld's
fixed waits with a ready signal and adds a fast reset that keeps the VM up and restores the task state. See
[rl.md](rl.md).
