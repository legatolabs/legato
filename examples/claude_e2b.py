"""Claude through OpenRouter on an E2B desktop. Legato switches to pixel coordinates, Anthropic's recommended screenshot
size and prompt caching on its own.

    export OPENROUTER_API_KEY=... E2B_API_KEY=...
    python examples/claude_e2b.py "Create a folder named reports on the desktop"
"""
import os
import sys

from e2b_desktop import Sandbox

import legato

task = sys.argv[1] if len(sys.argv) > 1 else "Create a folder named reports on the desktop"
agent = legato.Harness(model="anthropic/claude-sonnet-5.5", base_url="https://openrouter.ai/api/v1",
                       api_key=os.environ["OPENROUTER_API_KEY"])
desk = Sandbox.create()
try:
    print(agent.run(task, legato.attach(desk), max_steps=30).report())
finally:
    desk.kill()
