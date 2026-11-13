# Legato vs Anthropic's computer-use demo: which loop should you use?

Use Anthropic's demo for Claude with bash and file-editing tools, Anthropic's own computer tool versions, or Bedrock
and Vertex. Use Legato for the same screenshot-and-click loop with less fixed waiting, on a sandbox you already run, or
with a non-Claude model.

On 24 office tasks with Claude Sonnet 5.5, Legato took 0.678 of the reference loop's agent time (95% CI 0.604 to 0.757;
1.48x faster), with 48/48 successes in both arms. Almost all of the saving is fixed waiting: 14.0 s vs 44.5 s per
task. One run, against our re-implementation of the demo's loop ([benchmarks](../benchmarks.md)).

| | Anthropic computer-use demo | Legato (`legato.Harness`) |
|---|---|---|
| Models | Claude only: Anthropic API, Bedrock or Vertex | Any OpenAI-compatible chat API that accepts images |
| Desktop | Ubuntu in Docker; the loop runs inside the container it controls, one session at a time | E2B Desktop, or any sandbox given a screenshot function and an execute function |
| Wait after action | 2.0 s sleep before every screenshot | Until the screen stops changing, capped at 2 s per action (longer for `wait`) |
| Screenshots kept | All on the Anthropic API; last 3 by default on Bedrock and Vertex | Last 8, dropped 4 at a time |
| Actions per turn | Every tool call in the reply, with a screenshot after each | Up to 3, with one screenshot after them |
| Call bounds | 4,096 output tokens; 4 SDK retries on the Anthropic API; no explicit timeout; no step limit | 4,096 output tokens with a cut-off note; 60 s timeout; 1 retry; 30 turns by default |
| License | MIT | Apache-2.0 |

## When to use Anthropic's demo instead

- **You need a shell or file editing.** The demo gives Claude bash and `str_replace_based_edit_tool`; Legato offers
  only the `computer` tool.
- **You want Anthropic's computer tool itself, or Claude on Bedrock or Vertex.** Legato sends its own `computer`
  function in OpenAI format and needs an OpenAI-compatible endpoint.
- **You want a reference to read and modify,** with a Streamlit chat and a VNC view of the desktop.

## Code

```python
# pip install "legatolabs[e2b]"
import os
import legato
from e2b_desktop import Sandbox

agent = legato.Harness(model="anthropic/claude-sonnet-5.5", base_url="https://openrouter.ai/api/v1",
                       api_key=os.environ["OPENROUTER_API_KEY"])
# legato.Harness.reference(...) takes the same arguments and runs the reference loop
desk = Sandbox.create()                     # needs E2B_API_KEY
try:
    result = agent.run("Add a Total row under the table and save", legato.attach(desk))
    print(result.report())                  # model, action, wait and screenshot time
finally:
    desk.kill()
```

## Sources

- [anthropics/claude-quickstarts](https://github.com/anthropics/claude-quickstarts), commit 4a4224b
- [`loop.py#L101`](https://github.com/anthropics/claude-quickstarts/blob/4a4224bdf0e9cfb3332167b69c90457403813625/computer-use-demo/computer_use_demo/loop.py#L101),
  [`#L124-L130`](https://github.com/anthropics/claude-quickstarts/blob/4a4224bdf0e9cfb3332167b69c90457403813625/computer-use-demo/computer_use_demo/loop.py#L124-L130),
  [`#L125`](https://github.com/anthropics/claude-quickstarts/blob/4a4224bdf0e9cfb3332167b69c90457403813625/computer-use-demo/computer_use_demo/loop.py#L125),
  [`#L132-L139`](https://github.com/anthropics/claude-quickstarts/blob/4a4224bdf0e9cfb3332167b69c90457403813625/computer-use-demo/computer_use_demo/loop.py#L132-L139),
  [`#L202-L239`](https://github.com/anthropics/claude-quickstarts/blob/4a4224bdf0e9cfb3332167b69c90457403813625/computer-use-demo/computer_use_demo/loop.py#L202-L239)
- [`tools/computer.py#L99`](https://github.com/anthropics/claude-quickstarts/blob/4a4224bdf0e9cfb3332167b69c90457403813625/computer-use-demo/computer_use_demo/tools/computer.py#L99),
  [`#L273-L274`](https://github.com/anthropics/claude-quickstarts/blob/4a4224bdf0e9cfb3332167b69c90457403813625/computer-use-demo/computer_use_demo/tools/computer.py#L273-L274)
- [`tools/groups.py#L36-L63`](https://github.com/anthropics/claude-quickstarts/blob/4a4224bdf0e9cfb3332167b69c90457403813625/computer-use-demo/computer_use_demo/tools/groups.py#L36-L63)
- [`streamlit.py#L208-L209`](https://github.com/anthropics/claude-quickstarts/blob/4a4224bdf0e9cfb3332167b69c90457403813625/computer-use-demo/computer_use_demo/streamlit.py#L208-L209)
- [`README.md#L34`](https://github.com/anthropics/claude-quickstarts/blob/4a4224bdf0e9cfb3332167b69c90457403813625/computer-use-demo/README.md#L34)
- [`LICENSE`](https://github.com/anthropics/claude-quickstarts/blob/4a4224bdf0e9cfb3332167b69c90457403813625/computer-use-demo/LICENSE)
- Legato: [`harness.py`](../../legato/harness.py), [`env.py`](../../legato/env.py),
  [`settle.py`](../../legato/settle.py), [`rl/policy.py`](../../legato/rl/policy.py)
- [Benchmarks](../benchmarks.md)
