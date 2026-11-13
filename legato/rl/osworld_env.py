"""OSWorld desktops for legato.rl: OSWorld's DesktopEnv (Docker/QEMU provider) with legato's two environment changes.

1. Ready signal. A persistent watcher inside the VM records when the screen last changed; after each action one small
   reader call returns as soon as the screen is ready under the SDK's settle rules (legato.settle), instead of a fixed
   sleep. The same signal replaces OSWorld's fixed 60 s wait after reset and 20 s wait before evaluation.
2. Fast reset. The VM stays up between rollouts: task apps are killed, their absence is checked (nothing from the
   previous rollout survives), the clean /home/user baseline is restored, then OSWorld's own task setup runs. Domains
   whose tasks change state outside /home/user (os, multi_apps) fall back to OSWorld's snapshot revert.

Measured in our OSWorld study (120 OSWorld tasks, Qwen3.5-9B): fast reset 2.2 s vs 20.4 s revert; per-action ready median
0.5 s. Requires an OSWorld checkout on the Python path (`desktop_env`), a Linux host with KVM and Docker.
The `step` body follows OSWorld's DesktopEnv.step (Apache-2.0) with the fixed pause replaced by the ready signal.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from desktop_env.desktop_env import DesktopEnv  # OSWorld

from legato.settle import LOCAL_RULES, REMOTE_RULES

APP_PROCS = ["soffice.bin", "soffice", "chrome", "google-chrome", "chromium", "gimp", "thunderbird", "vlc", "code",
             "nautilus", "gedit", "gnome-text-editor", "evince", "eog", "totem"]
FAST_DOMAINS = {"office",   # legato.rl.bench.office: files under ~/Desktop, LibreOffice only
                "chrome", "gimp", "libreoffice_calc", "libreoffice_impress", "libreoffice_writer", "thunderbird",
                "vlc", "vs_code"}
BASE = "/var/tmp/legato-base-home"


def load_rules() -> dict:
    """The SDK's own settle rules, injected into the VM-side reader so the two cannot drift."""
    return {"local": LOCAL_RULES, "remote": REMOTE_RULES}


def action_kind(action) -> str:
    """The settle rule kind for an OSWorld action (pyautogui code string or special token)."""
    if isinstance(action, dict):
        action = action.get("action_type", "")
    a = str(action)
    if a in ("WAIT",):
        return "wait"
    if re.search(r"\b(typewrite|write)\(", a):
        return "type"
    if re.search(r"\b(hotkey|press|keyDown|keyUp)\(", a):
        return "key"
    if re.search(r"\bscroll\(|hscroll\(|vscroll\(", a):
        return "scroll"
    if re.search(r"\b(click|doubleClick|rightClick|tripleClick|mouseDown|mouseUp|dragTo|moveTo)\(", a):
        return "click"
    return "default"


DETECTOR = r'''
import json, time, hashlib
R = __RULE__
def grab():
    try:
        from PIL import ImageGrab
        im = ImageGrab.grab(xdisplay=":0")
    except Exception:
        import pyautogui
        im = pyautogui.screenshot()
    return hashlib.blake2b(im.reduce(4).tobytes(), digest_size=8).hexdigest()
t0 = time.monotonic(); last = grab(); t_change = t0; same = 1; n = 1; ready = None
cap = R["cap"]
while time.monotonic() - t0 < cap:
    time.sleep(R.get("poll", 0.03))
    d = grab(); n += 1; now = time.monotonic()
    if d != last:
        last, t_change, same = d, now, 1
    else:
        same += 1
    if now - t0 >= R["floor"] and now - t_change >= R["quiet"] and same >= R["min_same"]:
        ready = now - t0
        break
print(json.dumps({"ready_s": ready, "grabs": n, "elapsed_s": time.monotonic() - t0}))
'''


WATCHER = r'''
import time, json, hashlib, os
def mk():
    try:
        from PIL import ImageGrab
        ImageGrab.grab(xdisplay=":0")
        return "pil", lambda: ImageGrab.grab(xdisplay=":0")
    except Exception:
        import pyautogui
        return "pyautogui", pyautogui.screenshot
method, g = mk()
from PIL import ImageChops
COARSE_PX = 64   # in the 4x-reduced frame: a change whose bounding box covers < 64 px (~32x32 real px) is noise
last, prev, last_change, last_coarse, same, n = None, None, time.time(), time.time(), 0, 0
while True:
    t = time.time()
    try:
        im = g().reduce(4).convert("L")
        d = hashlib.blake2b(im.tobytes(), digest_size=8).hexdigest()
    except Exception:
        time.sleep(0.2)
        continue
    now = time.time(); n += 1
    if d != last:
        if prev is not None:
            bb = ImageChops.difference(im, prev).getbbox()
            if bb and (bb[2] - bb[0]) * (bb[3] - bb[1]) >= COARSE_PX:
                last_coarse = now
        last, last_change, same = d, now, 1
    else:
        same += 1
    prev = im
    with open("/tmp/.legato_screen.tmp", "w") as f:
        json.dump({"last_change": last_change, "last_coarse": last_coarse, "last_grab": now, "same": same, "n": n,
                   "grab_s": now - t, "method": method}, f)
    os.replace("/tmp/.legato_screen.tmp", "/tmp/legato_screen.json")
    time.sleep(max(0.0, __POLL__ - (time.time() - t)))
'''

READER = r'''
import json, time
R = __RULE__
t0 = time.time()
def st():
    try:
        return json.load(open("/tmp/legato_screen.json"))
    except Exception:
        return None
s0 = st(); n0 = s0["n"] if s0 else 0; ready = None; stale = False; s = s0
while time.time() - t0 < R["cap"]:
    s = st(); now = time.time()
    if s is None or now - s["last_grab"] > 1.0:
        stale = True
        break
    eff_change = max(s.get("last_coarse", s["last_change"]) if R.get("coarse") else s["last_change"], t0)
    same = (s["n"] - n0) if R.get("coarse") else min(s["same"], s["n"] - n0)
    if now - t0 >= R["floor"] and now - eff_change >= R["quiet"] and same >= R["min_same"]:
        ready = now - t0
        break
    time.sleep(R.get("poll", 0.03))
load = quiet = None
if ready is not None:
    load = eff_change - t0          # actual load time: from the call to the last screen change it saw (0 = none)
    quiet = ready - load            # the quiet window spent confirming the screen had stopped changing
print(json.dumps({"ready_s": ready, "load_s": load, "quiet_s": quiet, "stale": stale,
                  "grab_s": (s or {}).get("grab_s"), "method": (s or {}).get("method"), "elapsed_s": time.time() - t0}))
'''

GUEST_START = r'''
import subprocess, os, json
open("/tmp/legato_job.sh", "w").write(__SCRIPT__)
for f in ("/tmp/legato_job.rc", "/tmp/legato_job.out"):
    try:
        os.remove(f)
    except FileNotFoundError:
        pass
subprocess.Popen(["setsid", "bash", "-c", "bash /tmp/legato_job.sh > /tmp/legato_job.out 2>&1; echo $? > /tmp/legato_job.rc"],
                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
print(json.dumps({"started": True}))
'''

GUEST_POLL = r'''
import os, json
rc = open("/tmp/legato_job.rc").read().strip() if os.path.exists("/tmp/legato_job.rc") else None
out = open("/tmp/legato_job.out", errors="replace").read()[-2000:] if os.path.exists("/tmp/legato_job.out") else ""
print(json.dumps({"rc": rc, "out": out}))
'''


class LegatoDesktopEnv(DesktopEnv):
    def __init__(self, *a, wait_mode: str = "fixed", reset_mode: str = "revert", log_path: str | None = None, **kw):
        assert wait_mode in ("fixed", "ready") and reset_mode in ("revert", "fast")
        self.wait_mode, self.reset_mode = wait_mode, reset_mode
        self.rules = load_rules()
        self.log_path = log_path
        self._fast_now = False
        self._base_taken = False
        self.timing: dict = {}
        super().__init__(*a, **kw)

    # -------------------------------------------------------------- the in-VM ready detector
    CHUNK_S = 25.0   # the OSWorld server kills a /run_python script after 30 s
    POLL_S = 0.03

    def _py(self, code: str, timeout: float = 40) -> dict:
        """POST a script to the VM server's /run_python directly (the controller's helper injects a pyautogui patch
        and retries; neither is wanted for a timing probe). Returns the parsed last line of stdout, or {error}."""
        import requests
        try:
            r = requests.post(self.controller.http_server + "/run_python", json={"code": code}, timeout=timeout)
            out = (r.json() or {}).get("output", "") if r.status_code == 200 else ""
            return json.loads(out.strip().splitlines()[-1]) if out.strip() else {"error": f"http {r.status_code}: {r.text[:200]}"}
        except Exception as e:  # noqa: BLE001
            return {"error": f"{type(e).__name__}: {e}"[:300]}

    def start_watcher(self) -> dict:
        code = GUEST_START.replace("__SCRIPT__", json.dumps(
            "cat > /tmp/legato_watcher.py <<'PYEOF'\n" + WATCHER.replace("__POLL__", str(self.POLL_S)) + "\nPYEOF\n"
            "pkill -f legato_watcher.py; nohup python3 /tmp/legato_watcher.py > /tmp/legato_watcher.log 2>&1 &\n"))
        t = time.perf_counter()
        self._py(code)
        time.sleep(1.0)
        st = self._py("import json; print(open('/tmp/legato_screen.json').read())")
        return {"watcher_s": time.perf_counter() - t, "watcher": {k: st.get(k) for k in ("method", "grab_s")} if "error" not in st else st}

    def watcher_alive(self) -> bool:
        st = self._py("import json, time; s = json.load(open('/tmp/legato_screen.json')); "
                      "print(json.dumps({'age': time.time() - s['last_grab']}))")
        return "error" not in st and st.get("age", 99) < 2.0

    def detect(self, rule: dict, cap: float) -> dict:
        """At most `cap` s, in chunks of at most CHUNK_S. The reader asks the watcher; if the watcher is stale, the
        inline detector grabs itself. ready_s is measured from the start of the call."""
        t, out, chunks = time.perf_counter(), {"ready_s": None}, 0
        while True:
            left = cap - (time.perf_counter() - t)
            if left <= 0.05:
                break
            r = dict(rule, cap=min(left, self.CHUNK_S))
            t_c = time.perf_counter()
            got = self._py(READER.replace("__RULE__", repr(r)))
            chunks += 1
            if got.get("elapsed_s") is not None:              # signal message cost: round trip minus in-VM time
                out["msg_s"] = out.get("msg_s", 0.0) + max(0.0, (time.perf_counter() - t_c) - got["elapsed_s"])
            if got.get("stale") or "error" in got:
                out["fallback"] = True
                got = self._py(DETECTOR.replace("__RULE__", repr(dict(r, cap=max(0.05, left - (time.perf_counter() - t_c))))))
            if "error" in got:
                out["detect_error"] = got["error"]
                break
            for k in ("grab_s", "method"):
                if got.get(k) is not None:
                    out[k] = got[k]
            if got.get("ready_s") is not None:
                out["ready_s"] = (t_c - t) + got["ready_s"]
                if got.get("load_s") is not None:
                    out["load_s"] = (t_c - t) + got["load_s"]
                    out["quiet_s"] = got["quiet_s"]
                break
        out.update(detect_call_s=time.perf_counter() - t, chunks=chunks)
        return out

    def wait_after(self, kind: str, fixed_s: float) -> dict:
        rule = self.rules["local"].get(kind) or self.rules["local"]["default"]
        t = time.perf_counter()
        if self.wait_mode == "ready":
            info = self.detect(rule, cap=rule.get("cap", 3.0))
            if info.get("detect_error"):                          # never shorter than the rule's cap
                rest = rule.get("cap", 3.0) - (time.perf_counter() - t)
                if rest > 0:
                    time.sleep(rest)
                info["fell_back"] = True
        else:
            info = self.detect(rule, cap=fixed_s) if fixed_s > 0 else {"ready_s": None, "skipped": True}
            rest = fixed_s - (time.perf_counter() - t)
            if rest > 0:
                time.sleep(rest)
        info["wait_s"] = time.perf_counter() - t
        return info

    def wait_phase(self, fixed_s: float) -> dict:
        """OSWorld's 60 s after reset and 20 s before evaluation: kept in fixed arms, replaced in ready arms."""
        t = time.perf_counter()
        # the SDK's reset rule on the coarse signal: a blinking caret must not hold a 3 s quiet window open
        info = self.detect(dict(self.rules["remote"]["reset"], poll=0.1, coarse=True), cap=fixed_s)   # silent in fixed arms
        if self.wait_mode != "ready" or info.get("detect_error"):   # a failed detection waits OSWorld's own time
            rest = fixed_s - (time.perf_counter() - t)
            if rest > 0:
                time.sleep(rest)
            if self.wait_mode == "ready":
                info["fell_back"] = True
        info["wait_s"] = time.perf_counter() - t
        return info

    # -------------------------------------------------------------- step
    def step(self, action, pause=2):
        """OSWorld's DesktopEnv.step at commit 8903ebe (Apache-2.0), copied with one change: the final
        `time.sleep(pause)` before the observation is the arm's wait (fixed: the same sleep, with the silent ready
        detector inside it; ready: the detector alone). The special WAIT action keeps OSWorld's own sleep."""
        self._step_no += 1
        self.action_history.append(action)
        self.is_environment_used = True
        reward, done, info = 0, False, {}
        special = action in ['WAIT', 'FAIL', 'DONE'] or (type(action) == dict and action['action_type'] in ['WAIT', 'FAIL', 'DONE'])
        if special:
            if action == 'WAIT' or (type(action) == dict and action.get('action_type') == 'WAIT'):
                time.sleep(pause)
            elif action == 'FAIL' or (type(action) == dict and action.get('action_type') == 'FAIL'):
                done, info = True, {"fail": True}
            elif action == 'DONE' or (type(action) == dict and action.get('action_type') == 'DONE'):
                done, info = True, {"done": True}
        t0 = time.perf_counter()
        if self.action_space == "computer_13":
            self.controller.execute_action(action)
        elif self.action_space in ["pyautogui", "claude_computer_use", "gemini_computer_use"]:
            if special:
                self.controller.execute_action(action)
            elif type(action) == str:
                self.controller.execute_python_command(action)
            elif type(action) == dict:
                self.controller.execute_python_command(action['command'])
        t1 = time.perf_counter()
        w = self.wait_after(action_kind(action), float(pause or 0)) if not done else {"wait_s": 0.0}
        t2 = time.perf_counter()
        observation = self._get_obs()
        t3 = time.perf_counter()
        info = dict(info, legato={"act_s": t1 - t0, "wait": w, "obs_s": t3 - t2, "kind": action_kind(action),
                                  "parking": False, "kv_restore_s": None, "kv_restore_before_settle": None})
        return observation, reward, done, info

    # -------------------------------------------------------------- fast reset
    def _guest(self, script: str, timeout: int = 300) -> dict:
        """Run a bash script in the VM as a detached job, polled through /run_python (the image's /run_bash_script
        fails with "name '_append_event' is not defined"; /run_python also kills scripts after 30 s)."""
        st = self._py(GUEST_START.replace("__SCRIPT__", json.dumps(script)))
        if "error" in st:
            return {"returncode": -1, "output": st["error"]}
        t = time.perf_counter()
        while time.perf_counter() - t < timeout:
            p = self._py(GUEST_POLL)
            if p.get("rc") is not None:
                return {"returncode": int(p["rc"]), "output": p.get("out", "")}
            time.sleep(0.5)
        return {"returncode": -2, "output": "timeout"}

    def _sudo(self) -> str:
        pw = getattr(self, "client_password", None) or "password"
        return f"echo '{pw}' | sudo -S -p ''"

    def take_base(self):
        """Clean copy of /home/user (rsync if the image has it, else cp -a; an rsync-only
        version failed)."""
        su = self._sudo()
        r = self._guest(
            "set -e\n"
            f"if command -v rsync >/dev/null; then {su} mkdir -p {BASE}; {su} rsync -a --delete /home/user/ {BASE}/; "
            f"else {su} rm -rf {BASE}; {su} cp -a /home/user {BASE}; fi\n", timeout=300)
        self._base_taken = r.get("returncode") == 0
        if not self._base_taken:
            self.timing["base_error"] = str(r.get("output", ""))[-300:]
        return r

    def reset(self, task_config=None, seed=None, options=None):
        dom = (task_config or {}).get("_domain") or (task_config or {}).get("domain")
        self._fast_now = (self.reset_mode == "fast" and self._base_taken and self.is_environment_used
                          and dom in FAST_DOMAINS)
        if not self._base_taken and not self.is_environment_used:
            tb = time.perf_counter()
            r = self.take_base()                                    # clean VM, no baseline yet: take it now
            self.timing["base_s"] = self.timing.get("base_s", 0.0) + time.perf_counter() - tb
            self.timing["base_ok"] = r.get("returncode") == 0
        if not self.watcher_alive():
            self.timing.update(self.start_watcher())
        t = time.perf_counter()
        obs = super().reset(task_config=task_config, seed=seed, options=options)
        self.timing["reset_s"] = time.perf_counter() - t
        self.timing["reset_path"] = "fast" if self._fast_now else "revert"
        self._fast_now = False
        return obs

    def _revert_to_snapshot(self):
        if not self._fast_now:
            self._base_taken = False                                # a revert wipes the VM disk, baseline included
            return super()._revert_to_snapshot()
        su = self._sudo()
        t = time.perf_counter()
        procs = "|".join(APP_PROCS)
        r = self._guest(
            f"for p in {' '.join(APP_PROCS)}; do pkill -9 -x $p; done\n"
            f"for i in $(seq 50); do pgrep -x '{procs}' >/dev/null || break; sleep 0.1; done\n"   # killed apps exit
            f"if pgrep -x '{procs}' >/dev/null; then echo LEFTOVER; pgrep -a -x '{procs}' | head -5; exit 3; fi\n"
            "set -e\n"
            f"if command -v rsync >/dev/null; then {su} rsync -a --delete {BASE}/ /home/user/; "
            f"else {su} find /home/user -mindepth 1 -maxdepth 1 -exec rm -rf {{}} +; {su} cp -a {BASE}/. /home/user/; fi\n"
            f"{su} chown -R user:user /home/user\n", timeout=300)
        self.timing["fast_restore_s"] = time.perf_counter() - t
        if r.get("returncode") != 0:                                # never publish a half-restored state
            self.timing["fast_restore_error"] = str(r)[:300]
            self._fast_now = False
            return super()._revert_to_snapshot()

    def _start_emulator(self):
        if self._fast_now:
            return                                                  # the VM never stopped
        out = super()._start_emulator()
        if getattr(self, "controller", None) is not None:
            tb = time.perf_counter()
            r = self.take_base()                                    # freshly booted from the snapshot: clean (every arm)
            self.timing["base_s"] = self.timing.get("base_s", 0.0) + time.perf_counter() - tb
            self.timing["base_ok"] = r.get("returncode") == 0
            self.timing.update(self.start_watcher())                # every arm: fixed arms log readiness silently
        return out
