---
name: use-legato
description: Run a computer-use task on a desktop with Legato (any OpenAI-compatible vision model; this computer, any sandbox, or E2B). Use when an agent must click, type and read a GUI.
---

# Use Legato

1. Install the package: `pip install "legatolabs[e2b]"`. The PyPI name is `legatolabs`; the import is `import legato`.
2. Set the keys in the environment: the model key (for example `OPENROUTER_API_KEY`) and `E2B_API_KEY`.
3. Run a task:

```python
import os, legato
from e2b_desktop import Sandbox

agent = legato.Harness(model="qwen/qwen3.8-27b", base_url="https://openrouter.ai/api/v1",
                       api_key=os.environ["OPENROUTER_API_KEY"])
desk = Sandbox.create()
try:
    result = agent.run("Open the terminal and print the date", legato.attach(desk), max_steps=30)
    print(result.done, result.report())
finally:
    desk.kill()
```

Or from a shell: `legato run "TASK" --model MODEL --base-url URL --json`. It prints one JSON object.

## Notes

- **This computer:** `legato.local()` (`pip install "legatolabs[local]"`). It moves the real mouse; moving the mouse into a screen corner stops it.
- **Other sandboxes:** `legato.attach(shell=fn_running_a_command_returning_stdout)` for any Linux sandbox with X, python3, xdotool and scrot; otherwise `legato.attach(screenshot=fn_returning_png_bytes, execute=fn_taking_legato_Action)`.
- **Coordinates:** Claude models use pixels automatically. Other models use a 0–1000 grid. Pass `coords=` for a model you have not checked.
- **Cut-off turns:** if `result.report()` shows them, raise `max_tokens`.
- **MCP:** to let a coding agent drive a desktop itself: `legato mcp --sandbox docker` (a desktop container; needs only Docker; the safest choice), `legato mcp --local` (this computer), `legato mcp --sandbox file.py:function` (your sandbox), or `legato mcp` for E2B (`pip install "legatolabs[mcp,local]"` or `[mcp,e2b]`).
- **No cloud account?** `--sandbox docker` runs a Linux desktop container locally; apps on the host are at `http://host.docker.internal:PORT` inside it.
- **Docs:** https://legato.computer/llms.txt
