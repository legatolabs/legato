---
name: use-legato
description: Run a computer-use task on a desktop with Legato (any OpenAI-compatible vision model, E2B or any sandbox). Use when an agent must click, type and read a GUI.
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

- **Other sandboxes:** `legato.attach(screenshot=fn_returning_png_bytes, execute=fn_taking_legato_Action)`.
- **Coordinates:** Claude models use pixels automatically. Other models use a 0–1000 grid. Pass `coords=` for a model you have not checked.
- **Cut-off turns:** if `result.report()` shows them, raise `max_tokens`.
- **MCP:** to let a coding agent drive a desktop itself, run `legato mcp` (`pip install "legatolabs[e2b,mcp]"`).
- **Docs:** https://legato.computer/llms.txt
