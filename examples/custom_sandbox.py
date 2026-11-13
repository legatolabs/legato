"""Drive any desktop you can screenshot and send input to: here a local X display through xdotool and ImageMagick.
Swap the two functions for your VM, VNC, or cloud sandbox. If the sandbox can run a shell command and has python3,
xdotool and scrot, `legato.attach(shell=run)` is simpler and faster: one call per action (docs/sandboxes.md).

    DISPLAY=:1 OPENROUTER_API_KEY=... python examples/custom_sandbox.py
"""
import os
import subprocess

import legato
from legato.env import xdotool_cmds


def grab_png() -> bytes:
    return subprocess.run(["import", "-window", "root", "png:-"], capture_output=True, check=True).stdout


def run_action(action: legato.Action) -> None:
    cmds, extra_sleep = xdotool_cmds(action)
    for c in cmds:
        subprocess.run(c, check=False)


agent = legato.Harness(model="qwen/qwen3.8-27b", base_url="https://openrouter.ai/api/v1",
                       api_key=os.environ["OPENROUTER_API_KEY"])
env = legato.attach(screenshot=grab_png, execute=run_action)
print(agent.run("Open a terminal", env, max_steps=15).report())
