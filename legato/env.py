"""Attach legato to the sandbox you already use.

    env = legato.attach(e2b_sandbox)       # E2B Desktop
    obs = env.observe()                    # Obs(screenshot=PNG bytes, width, height)
    obs = env.act(action)                  # execute, settle, return the next screen

On E2B, `act` makes one remote call. A small helper uploaded into the sandbox runs the action as a single xdotool
chain, then polls the local framebuffer until the screen is quiet (milliseconds per grab instead of a network round
trip). This avoids the SDK's slow paths: a synced mouse move that can stall ~15 s when the pointer is already in
place, 75 ms gaps between typed chunks, and one remote call per primitive. If the helper cannot run, it falls back
to the SDK's own calls and client-side settle.

Any Linux sandbox where you can run a shell command (Docker, SSH, Daytona, Modal, your own VMs):
`legato.attach(shell=run)`, where `run(cmd)` runs a shell command in the sandbox and returns its stdout. The same helper
then runs inside it, so each action is one call. The sandbox needs an X display, python3, xdotool and scrot.

Anything else: `legato.attach(screenshot=fn, execute=fn)`. `fn(action)` performs an Action; legato settles
client-side from the screenshot function.
"""
from __future__ import annotations

import base64
import json
import shlex
import struct
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from legato.actions import Action
from legato.settle import LOCAL_RULES, Waiter, rule_kind

HELPER_PATH = "/tmp/legato_step.py"
HELPER = r'''
import hashlib, json, os, shutil, subprocess, sys, time
spec = json.loads(sys.argv[1])
XWD = shutil.which("xwd")
def grab():
    if XWD:
        out = subprocess.run([XWD, "-root", "-silent"], capture_output=True).stdout
        if len(out) >= 100:   # hash the pixels only: the xwd header and colormap vary between identical screens
            import struct
            h = struct.unpack(">25I", out[:100])
            out = out[h[0] + h[19] * 12:]
    else:
        subprocess.run(["scrot", "-o", "/dev/shm/legato.png"], capture_output=True)
        out = open("/dev/shm/legato.png", "rb").read()
    return hashlib.blake2b(out, digest_size=8).digest()
t0 = time.monotonic()
for cmd in spec["cmds"]:
    subprocess.run(cmd, capture_output=True)
if spec.get("sleep"):
    time.sleep(spec["sleep"])
t1 = time.monotonic()
r = spec["rule"]
last = grab(); t_change = time.monotonic(); same = 1; capped = False; grabs = 1
while True:
    if time.monotonic() - t1 >= r["cap"]:
        capped = True
        break
    time.sleep(r["poll"])
    d = grab(); grabs += 1; now = time.monotonic()
    if d != last:
        last, t_change, same = d, now, 1
    else:
        same += 1
    if now - t1 >= r["floor"] and now - t_change >= r["quiet"] and same >= r["min_same"]:
        break
res = {"act_s": t1 - t0, "settle_s": time.monotonic() - t1, "capped": capped, "grabs": grabs}
if spec.get("shot"):   # return the settled frame in the same call: one round trip per action
    import base64
    subprocess.run(["scrot", "-o", "/dev/shm/legato_obs.png"], capture_output=True)
    try:
        res["png"] = base64.b64encode(open("/dev/shm/legato_obs.png", "rb").read()).decode()
    except OSError:
        pass
print(json.dumps(res))
'''


@dataclass
class Obs:
    screenshot: bytes
    width: int
    height: int
    t: float = field(default_factory=time.monotonic)


def png_size(b: bytes) -> tuple[int, int]:
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", b[16:24])
    try:   # JPEG or anything else: decode the header with Pillow if present
        import io

        from PIL import Image
        return Image.open(io.BytesIO(b)).size
    except Exception:  # noqa: BLE001
        return (1024, 768)


TYPE_DELAY_MS = 3


CONTROL_KEYS = {"\t": "Tab", "\n": "Return", "\r": "Return"}
CONTROL_PAUSE_S = "0.08"


def type_cmds(text: str) -> list[list[str]]:
    """Plain runs are typed fast; Tab and Return become separate key presses followed by a short pause. Apps move
    focus on them (a spreadsheet changes cell), and keys sent during the move are lost: at 3 ms per key a Calc
    demo dropped one of two Tabs and filled the wrong column."""
    cmds, buf = [], ""
    for ch in text:
        if ch in CONTROL_KEYS:
            if buf:
                cmds.append(["xdotool", "type", "--delay", str(TYPE_DELAY_MS), "--", buf])
                buf = ""
            cmds.append(["xdotool", "key", CONTROL_KEYS[ch]])
            cmds.append(["sleep", CONTROL_PAUSE_S])
        else:
            buf += ch
    if buf:
        cmds.append(["xdotool", "type", "--delay", str(TYPE_DELAY_MS), "--", buf])
    return cmds


def xdotool_cmds(a: Action) -> tuple[list[list[str]], float]:
    """The xdotool argv chain for an action, and an extra sleep (for wait)."""
    x, y = str(a.x), str(a.y)
    t = a.type
    if t == "click":
        return [["xdotool", "mousemove", x, y, "click", "1"]], 0.0
    if t == "double_click":
        return [["xdotool", "mousemove", x, y, "click", "--repeat", "2", "--delay", "60", "1"]], 0.0
    if t == "right_click":
        return [["xdotool", "mousemove", x, y, "click", "3"]], 0.0
    if t == "type":
        return type_cmds(a.text or ""), 0.0
    if t == "key":
        return [["xdotool", "key", "--delay", "20", *((a.keys or "").split())]], 0.0
    if t == "scroll":
        btn = {"up": "4", "down": "5", "left": "6", "right": "7"}.get(a.direction or "down", "5")
        pre = ["mousemove", x, y] if a.x is not None else []
        return [["xdotool", *pre, "click", "--repeat", str(a.amount or 3), "--delay", "30", btn]], 0.0
    if t == "drag":
        return [["xdotool", "mousemove", x, y, "mousedown", "1", "sleep", "0.05", "mousemove", str(a.x2), str(a.y2),
                 "sleep", "0.05", "mouseup", "1"]], 0.0
    if t == "wait":
        return [], 1.0
    return [], 0.0


class Env:
    """An attached sandbox. `last` holds the timing of the latest act: act_s, settle_s, obs_s. With `fixed_wait_s` it
    sleeps that long after every action instead of waiting for the screen (to reproduce a fixed-sleep loop)."""

    def __init__(self, screenshot: Callable[[], bytes], execute: Callable[[Action], Any] | None = None,
                 step: Callable[[Action], dict] | None = None, fixed_wait_s: float | None = None):
        self._shot, self._execute, self._step = screenshot, execute, step
        self.fixed_wait_s = fixed_wait_s
        self.waiter = Waiter(screenshot)
        self.last: dict = {}

    def observe(self) -> Obs:
        t = time.monotonic()
        b = self._shot()
        w, h = png_size(b)
        self.last = {"obs_s": time.monotonic() - t}
        return Obs(b, w, h)

    def act(self, action: Action) -> Obs:
        if action.done:
            return self.observe()
        timing = {}
        if self.fixed_wait_s is not None:
            t = time.monotonic()
            self._execute(action)
            timing["act_s"] = time.monotonic() - t
            time.sleep(self.fixed_wait_s)
            timing["settle_s"] = self.fixed_wait_s
        elif self._step is not None:
            timing = self._step(action) or {}
        else:
            t = time.monotonic()
            self._execute(action)
            timing["act_s"] = time.monotonic() - t
            t = time.monotonic()
            self.waiter.settle(rule_kind(action.type))
            timing["settle_s"] = time.monotonic() - t
        png = getattr(self, "_pending_png", None)
        self._pending_png = None
        if png:                     # the helper returned the settled frame: no extra round trip
            w, h = png_size(png)
            obs = Obs(png, w, h)
            self.last = {**timing, "obs_s": 0.0}
            return obs
        obs = self.observe()
        self.last = {**timing, **self.last}
        return obs


class E2BEnv(Env):
    def __init__(self, sb, blink_off: bool = True, fixed_wait_s: float | None = None):
        self.sb = sb
        self.helper = False
        try:
            if blink_off:   # a blinking caret makes every frame differ: no settle could ever finish
                sb.commands.run("xfconf-query -c xsettings -p /Net/CursorBlink -n -t bool -s false || true; "
                                "gsettings set org.gnome.desktop.interface cursor-blink false 2>/dev/null || true",
                                timeout=20)
            sb.files.write(HELPER_PATH, HELPER)
            r = sb.commands.run(f"python3 {HELPER_PATH} " + shlex.quote(json.dumps(
                {"cmds": [], "rule": {**LOCAL_RULES["default"], "cap": 0.2, "floor": 0, "quiet": 0}})), timeout=20)
            self.helper = '"settle_s"' in (r.stdout or "")
        except Exception:  # noqa: BLE001 - fall back to the SDK's calls
            self.helper = False
        super().__init__(self._screenshot, self._sdk_execute, self._helper_step if self.helper else None,
                         fixed_wait_s=fixed_wait_s)

    def _screenshot(self) -> bytes:
        b = self.sb.screenshot()
        return bytes(b) if not isinstance(b, (bytes, bytearray)) else bytes(b)

    def _helper_step(self, a: Action) -> dict:
        cmds, extra = xdotool_cmds(a)
        rule = LOCAL_RULES.get(rule_kind(a.type)) or LOCAL_RULES["default"]
        t = time.monotonic()
        r = self.sb.commands.run(f"python3 {HELPER_PATH} " + shlex.quote(json.dumps(
            {"cmds": cmds, "sleep": extra, "rule": rule, "shot": True})), timeout=30)
        rtt = time.monotonic() - t
        try:
            out = json.loads((r.stdout or "").strip().splitlines()[-1])
        except (ValueError, IndexError):
            out = {}
        # wall time of the call is what the agent waits; split it as the helper measured
        settle = float(out.get("settle_s", 0.0))
        self._pending_png = base64.b64decode(out["png"]) if out.get("png") else None
        return {"act_s": rtt - settle, "settle_s": settle, "capped": out.get("capped"), "helper": True}

    def _sdk_execute(self, a: Action) -> None:
        sb, t = self.sb, a.type
        if t in ("click", "double_click", "right_click"):
            sb.commands.run(f"xdotool mousemove {a.x} {a.y}")   # no --sync: never stalls on an unmoved pointer
            {"click": sb.left_click, "double_click": sb.double_click, "right_click": sb.right_click}[t]()
        elif t == "type":   # Tab/Return as separate keys with a pause, as in type_cmds: apps drop keys during a focus move
            buf = ""
            for ch in (a.text or "") + "\0":
                if ch in CONTROL_KEYS or ch == "\0":
                    if buf:
                        sb.write(buf)
                        buf = ""
                    if ch in CONTROL_KEYS:
                        sb.commands.run(f"xdotool key {CONTROL_KEYS[ch]}")
                        time.sleep(float(CONTROL_PAUSE_S))
                else:
                    buf += ch
        elif t == "key":
            sb.commands.run("xdotool key " + " ".join(shlex.quote(k) for k in (a.keys or "").split()))
        elif t == "scroll":
            if a.x is not None:
                sb.commands.run(f"xdotool mousemove {a.x} {a.y}")
            sb.scroll("up" if a.direction == "up" else "down", a.amount or 3)
        elif t == "drag":
            sb.drag((a.x, a.y), (a.x2, a.y2))
        elif t == "wait":
            time.sleep(1.0)


class ShellEnv(Env):
    """Any Linux sandbox with an X display where `run(cmd) -> stdout` runs a shell command: Legato installs its helper
    there, and each action is then one call that acts, waits for the screen and returns the screenshot."""

    PRE = "export DISPLAY=${DISPLAY:-:0}; "

    def __init__(self, run: Callable[[str], Any], blink_off: bool = True, fixed_wait_s: float | None = None):
        self.run = run
        if blink_off:
            run(self.PRE + "xfconf-query -c xsettings -p /Net/CursorBlink -n -t bool -s false 2>/dev/null || true; "
                "gsettings set org.gnome.desktop.interface cursor-blink false 2>/dev/null || true")
        run(f"echo {base64.b64encode(HELPER.encode()).decode()} | base64 -d > {HELPER_PATH}")
        super().__init__(self._screenshot, self._execute_only, self._helper_step, fixed_wait_s=fixed_wait_s)

    def _call(self, spec: dict) -> dict:
        out = self.run(self.PRE + f"python3 {HELPER_PATH} " + shlex.quote(json.dumps(spec)))
        out = out.decode() if isinstance(out, (bytes, bytearray)) else str(getattr(out, "stdout", out) or "")
        try:
            return json.loads(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise RuntimeError("Legato's helper did not answer from the sandbox; it needs an X display, python3, "
                               f"xdotool and scrot. Output: {out[-300:]!r}") from None

    def _screenshot(self) -> bytes:
        out = self._call({"cmds": [], "rule": {**LOCAL_RULES["default"], "cap": 0}, "shot": True})
        return base64.b64decode(out["png"])

    def _execute_only(self, a: Action) -> None:          # for fixed_wait_s: act, no wait
        cmds, extra = xdotool_cmds(a)
        self._call({"cmds": cmds, "sleep": extra, "rule": {**LOCAL_RULES["default"], "cap": 0}})

    def _helper_step(self, a: Action) -> dict:
        cmds, extra = xdotool_cmds(a)
        rule = LOCAL_RULES.get(rule_kind(a.type)) or LOCAL_RULES["default"]
        t = time.monotonic()
        out = self._call({"cmds": cmds, "sleep": extra, "rule": rule, "shot": True})
        rtt = time.monotonic() - t
        settle = float(out.get("settle_s", 0.0))
        self._pending_png = base64.b64decode(out["png"]) if out.get("png") else None
        return {"act_s": rtt - settle, "settle_s": settle, "capped": out.get("capped"), "helper": True}


def attach(sandbox=None, *, shell: Callable[[str], Any] | None = None, screenshot: Callable[[], bytes] | None = None,
           execute: Callable[[Action], Any] | None = None, blink_off: bool = True,
           fixed_wait_s: float | None = None) -> Env:
    """Attach to a sandbox:
    - `attach(e2b_sandbox)`: an E2B Desktop.
    - `attach(shell=run)`: any Linux sandbox with an X display, where `run(cmd)` runs a shell command and returns
      stdout (Docker, SSH, Daytona, Modal, your own VMs). It needs python3, xdotool and scrot.
    - `attach(screenshot=fn, execute=fn)`: anything else.
    `fixed_wait_s` replaces the wait for the screen with a fixed sleep after each action (Anthropic's reference loop
    sleeps 2.0 s). For this computer's own screen, use `legato.local()`."""
    if sandbox is not None and hasattr(sandbox, "commands") and hasattr(sandbox, "screenshot"):
        return E2BEnv(sandbox, blink_off=blink_off, fixed_wait_s=fixed_wait_s)
    if shell is not None:
        return ShellEnv(shell, blink_off=blink_off, fixed_wait_s=fixed_wait_s)
    if screenshot is None or execute is None:
        raise TypeError("attach(e2b_sandbox), attach(shell=run) or attach(screenshot=fn, execute=fn)")
    return Env(screenshot, execute, fixed_wait_s=fixed_wait_s)
