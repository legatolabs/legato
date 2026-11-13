"""legato.local() on a real screen (run by .github/workflows/local-desktop.yml on macOS and Windows runners).

Captures the screen, clicks, and on Windows types into Notepad; saves screenshots to ./local-shots for the artifact.
"""
import os
import subprocess
import sys
import time

import legato
from legato.actions import Action

os.makedirs("local-shots", exist_ok=True)
env = legato.local()
obs = env.observe()
print(f"screen {env.size}, screenshot {obs.width}x{obs.height}")
assert (obs.width, obs.height) == tuple(env.size), "screenshot is not at the logical screen size"
open("local-shots/0-start.png", "wb").write(obs.screenshot)

x, y = env.size[0] // 2, env.size[1] // 2
obs = env.act(Action("click", x=x, y=y))
pos = tuple(env.gui.position())
print(f"clicked ({x}, {y}); pointer at {pos}; waited {env.last.get('settle_s', 0):.2f} s")
assert abs(pos[0] - x) <= 2 and abs(pos[1] - y) <= 2, "pointer is not where we clicked"

if sys.platform == "win32":
    subprocess.Popen(["notepad.exe"])
    time.sleep(3)
    env.act(Action("type", text="typed by legato.local()"))
    obs = env.act(Action("key", keys="ctrl+a"))
    open("local-shots/1-notepad.png", "wb").write(obs.screenshot)
elif sys.platform == "darwin":
    subprocess.Popen(["open", "-a", "TextEdit"])
    time.sleep(4)
    obs = env.act(Action("key", keys="ctrl+n"))          # sent as Cmd+N on macOS
    obs = env.act(Action("type", text="typed by legato.local()"))
    open("local-shots/1-textedit.png", "wb").write(obs.screenshot)
print("ok")
