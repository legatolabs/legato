"""Measure Legato against Anthropic's reference loop on your own task: same model, same kind of desktop. The reference
loop sleeps 2 s before every screenshot, so its desktop is attached with `fixed_wait_s=2.0`.

    export OPENROUTER_API_KEY=... E2B_API_KEY=...
    python examples/compare_with_reference.py "Create a folder named reports on the desktop"
"""
import os
import sys

from e2b_desktop import Sandbox

import legato

task = sys.argv[1] if len(sys.argv) > 1 else "Create a folder named reports on the desktop"
model, url, key = "anthropic/claude-sonnet-5.5", "https://openrouter.ai/api/v1", os.environ["OPENROUTER_API_KEY"]


for name, agent, wait in (("legato", legato.Harness(model, url, key), None),
                          ("reference", legato.Harness.reference(model, url, key), 2.0)):
    desk = Sandbox.create()
    try:
        res = agent.run(task, legato.attach(desk, fixed_wait_s=wait), max_steps=30)
        print(f"--- {name}\n{res.report()}")
    finally:
        desk.kill()
