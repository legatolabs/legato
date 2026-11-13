"""An open model through OpenRouter on an E2B desktop.

    pip install "legatolabs[e2b]"
    export OPENROUTER_API_KEY=... E2B_API_KEY=...
    python examples/openrouter_qwen_e2b.py "Open the terminal and print the date"
"""
import os
import sys

from e2b_desktop import Sandbox

import legato

task = sys.argv[1] if len(sys.argv) > 1 else "Open the terminal and print the date"
agent = legato.Harness(model="qwen/qwen3.8-27b", base_url="https://openrouter.ai/api/v1",
                       api_key=os.environ["OPENROUTER_API_KEY"])
desk = Sandbox.create()
try:
    res = agent.run(task, legato.attach(desk), max_steps=30,
                    on_step=lambda i, acts, t: print(f"turn {i + 1}: {', '.join(a.describe() if a else 'screenshot' for a in acts) or '-'}"))
    print(res.report())
finally:
    desk.kill()
