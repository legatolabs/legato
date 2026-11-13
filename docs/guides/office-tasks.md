# Automate spreadsheets and office apps with an agent

Some work only happens in a GUI: a spreadsheet with no API, a deck to update, a desktop app. A computer-use agent can
do it by looking at the screen and clicking, like a person.

```python
import os, legato
from e2b_desktop import Sandbox                       # pip install "legatolabs[e2b]"; needs E2B_API_KEY

desk = Sandbox.create()
desk.files.write("/home/user/sales.xlsx", open("sales.xlsx", "rb").read())
agent = legato.Harness(model="anthropic/claude-sonnet-5.5", base_url="https://openrouter.ai/api/v1",
                       api_key=os.environ["OPENROUTER_API_KEY"])
result = agent.run("Open ~/sales.xlsx in LibreOffice Calc, add a Total row under the table and save it", legato.attach(desk))
print(result.report())
open("sales.xlsx", "wb").write(desk.files.read("/home/user/sales.xlsx", format="bytes"))
desk.kill()
```

How well it works, on the Legato Office Bench (24 tasks that move numbers from a spreadsheet into a PowerPoint deck in
LibreOffice, graded by checking the saved file):

| model | tasks passed |
|---|---|
| Claude Sonnet 5.5 with Legato | 48/48 |
| Qwen3.8-27B with Legato | 71/96 over two runs (OSWorld's Qwen agent: 53/96) |

- **Your own Excel.** `legato.local()` drives the apps on your own Mac or Windows machine instead
  ([setup](../sandboxes.md#this-computer)). We have measured LibreOffice on Linux only.
- **Check the result.** Read the saved file back and verify it in code, as the benchmark's grader does, rather than
  trusting the agent's own report.
- **Try it on your tasks.** `legato bench office generate` builds the benchmark's tasks; see [benchmarks](../benchmarks.md).
