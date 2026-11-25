# Desktops: which computers Legato can drive

Bring your own. Legato drives one app's window on your Mac in the background, your own screen (macOS, Windows or
Linux), a desktop container on your machine, any Linux sandbox you can run a command in (SSH, Daytona, Modal, your own
VMs), E2B cloud desktops, or anything you can screenshot and send input to.

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
works as the handle too: Legato calls it if it has none of those methods. `legato setup --desktop my_desktop.py:desktop`
makes it the default for `legato run`, `legato mcp` and `legato.run()`; each task gets a fresh one.

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
  `LEGATO_CONFIRM=off` asks nothing. Without a terminal (a script's stdin is a pipe, CI) nothing is asked either, and
  `legato.local()` runs the action; `legato.run()`, `legato run` and `legato mcp` refuse it instead (`legato run
  --yes` and `legato mcp --yes` allow it; in Python, pass `confirm`).
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

`legato.local(background="TextEdit")` works in a window of one app and leaves the rest of the Mac to you: your
pointer, the app in front, the clipboard and your own windows stay yours. It is the default on a Mac: `legato run
"..."` works in the app the task names ("... in Notes", "... on Spotify"), else in Safari; `--app NAME` picks one. In
the MCP server: `desktop_open(app="Safari", url=...)`.

**It works in a window of its own, never in yours.** When the desktop opens, Legato makes one through the app's menu
(no Apple Events permission): a new window in a browser (Safari's New Window), a new document in a document app
(TextEdit's New). Windows that open from its own actions (a document it opens, a save sheet) are followed; your
windows and tabs, and other desktops' windows, never are, and if its window closes the desktop says so rather than
moving to another. The browser window is closed when the run ends (`legato run`, `legato.run`, `desktop_close`,
`env.close()`); a new document stays open, with what the agent wrote in it. An app with neither (Calculator, Chess,
Notes, where New would add a note) is worked in its front window, and the desktop names that window when it opens.

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
- **A page opens in its window** (`env.open_url(url)`, `desktop_open(url=...)`) through the address field, with any
  query string, and is ready to read and click when the call returns. Reload, Back and Forward (Cmd+R, Cmd+[, Cmd+])
  press the toolbar's buttons, since their menu items are off while Safari is in the back.
- **Two desktops in one app** (two `desktop_open(app="Safari")`) each keep their own window: reads, screenshots, clicks
  and keys go to that window only. Keys reach it because it is made the app's main window first; a click where another
  of the app's windows is in front raises its window among the app's windows (the app stays in the back).
- **If the app takes the front** (Chrome does when it opens a URL), it is given back to the app you were in.
- **Not possible in the background,** and reported to the model as "this desktop can't do that": saving, exporting
  and printing (an app in the back turns those menu items off, by key, menu or button alike), drags, a click on
  something that can't be pressed, focused or selected (a canvas, an empty area), a click with keys held, copy, cut and
  paste (the clipboard is yours), anything outside the app (the Dock, the menu bar, other apps), system shortcuts
  (Cmd+Space for Spotlight, Cmd+Tab, Mission Control: posted to the app they would do nothing, and the search typed
  next would land in the window), and font changes such as bold and italic: an app in the back doesn't apply them,
  though its Bold button may look on (underline and alignment do work).
- **An app with no New Window or New Document** (Calculator, System Settings) is worked in its one window, which is
  yours: what it shows (Calculator's display) is changed. `describe()` and `desktop_open` say so.
- **Typed text goes in as written** in apps other than browsers (through Accessibility), so the app's automatic
  capitalisation and smart quotes don't change it. A new document stays open after the run with what was written.
- **Several agents in one app** each keep to their own windows, across processes too. A window you open yourself in
  that app while an agent is acting may be taken for the one its action opened: open yours between its actions.
- **Don't use the same app yourself** while it works: it types only while its own window has that app's keyboard, so
  your typing in the same app holds its keys back. Give it an app you aren't using.
- The window must stay on screen: not minimized, and on the current Space. Locking the Mac or sleep stops it. An app
  hidden with Cmd+H is shown again (behind your app); the agent's own Cmd+H and Cmd+M are not run, nor its clicks on the
  window's close, minimize and zoom buttons.
- **One app per task.** A task that moves between apps needs the whole screen (`--screen`).
- **Browsers:** Safari only. Other browsers are refused in the background for now: in Firefox the agent's keys and
  screenshots reached the person's own Firefox window, not the one it opened, and Chrome 158 and later ignore
  background clicks on a web page's elements. Use them with `--screen`.
- Your stop signals (a corner, Escape) don't apply, since the mouse and keyboard are yours: stop it from the program
  that runs it. Risky keys and typed commands still wait for your yes.
- **Tested** with `tests/test_background.py` (any OS) and, on a Mac, `LEGATO_TEST_BACKGROUND=1 pytest
  tests/test_background_live.py`: TextEdit and Safari in the back while Terminal stays in front, checking
  after each test that the front app, the pointer and the clipboard are unchanged.

## A desktop container on this machine

`--sandbox docker` starts a fresh container from `ghcr.io/legatolabs/desktop`: a small Linux desktop with a terminal and
Firefox ([Dockerfile](https://github.com/legatolabs/legato/blob/main/docker/desktop/Dockerfile)). It needs only
Docker: no key, no cloud, and the agent can't touch your own screen. The first use pulls the image, or builds it from
the repo. `--sandbox docker:IMAGE` uses your own image: one built `FROM ghcr.io/legatolabs/desktop` with your apps, or
any image with an X display on `:0`, `python3`, `xdotool` and `scrot`. `LEGATO_DOCKER_ARGS` adds `docker run`
arguments, such as `-v "$PWD:/home/user/project"` ([testing a desktop app](guides/desktop-apps.md)).

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
