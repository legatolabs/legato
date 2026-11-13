# Legato

**Legato is an open-source computer-use harness for any OpenAI-compatible model** (Qwen, DeepSeek, Claude, or your
own vLLM or SGLang server, directly or through OpenRouter). It drives a desktop sandbox (E2B, or any sandbox you can
screenshot and send input to) with a Claude-style `computer` tool. It waits until the screen is ready instead of
sleeping a fixed time, and it bounds every model call. Apache-2.0.

```bash
pip install "legatolabs[e2b]"      # the package is legatolabs on PyPI; you import it as `legato`
```

## Results

Same model, same desktops, same tasks, all arms run at the same time. The tasks are 24 Excel-to-PowerPoint office
workflows on real Ubuntu desktops (LibreOffice). Times are agent time: first screenshot to last model turn. Ranges are
95% task-bootstrap intervals. Full method, raw data and losses: [docs/benchmarks.md](docs/benchmarks.md).

| Model | Legato compared with | Time | Success |
|---|---|---|---|
| Claude Sonnet 5.5 | Anthropic's computer-use reference loop (re-implemented) | **1.48x faster** (0.678 [0.604, 0.757]) | 48/48 vs 48/48 |
| Qwen3.8-27B | OSWorld's Qwen agent scaffold (`mm_agents/qwen`) | about the same (0.953 [0.798, 1.134]) | **71/96 vs 53/96** (74% vs 55%, two runs) |

- **The Claude result** is one run. The saving is almost all fixed waiting: 14 s vs 45 s per task. Model time is the
  same.
- **The Qwen success gain** held on two independent task sets: +21 and +17 points.
- **The Qwen speed result.** In the first run, OSWorld's agent let four rollouts generate up to 32,768 tokens in one
  reply, so Legato looked 2.4x faster. On fresh tasks, without those runaways, the two are tied. We report the tie.
- On OSWorld tasks with OSWorld's own harness (120 tasks, Qwen3.5-9B), Legato's ready-wait and fast reset cut task
  time to 0.52 [0.43, 0.65] of the 5 s fixed-wait setting, at equal success. That is not an official OSWorld-Verified
  submission.

## Quickstart

An open model through OpenRouter, on an E2B desktop:

```python
import os
import legato
from e2b_desktop import Sandbox

agent = legato.Harness(model="qwen/qwen3.8-27b", base_url="https://openrouter.ai/api/v1",
                       api_key=os.environ["OPENROUTER_API_KEY"])
desk = Sandbox.create()                      # needs E2B_API_KEY
try:
    result = agent.run("Open the terminal and print the date", legato.attach(desk), max_steps=30)
    print(result.report())                   # where the time went: model, actions, waiting for the screen
finally:
    desk.kill()
```

The same harness works with Claude: `legato.Harness(model="anthropic/claude-sonnet-5.5", ...)`. It switches to pixel
coordinates and Anthropic's recommended screenshot size. It also works with your own server:
`base_url="http://localhost:8000/v1"`. From the command line:

```bash
legato run "Open the terminal and print the date" --model qwen/qwen3.8-27b --base-url https://openrouter.ai/api/v1
```

Any sandbox, without E2B: pass a screenshot function and an action executor.

```python
env = legato.attach(screenshot=lambda: png_bytes(), execute=lambda action: send_input(action))
```

`action` is a `legato.Action` (`type` click / double_click / right_click / type / key / scroll / drag / wait, with
pixel `x`, `y`, `text`, `keys`). More in [docs/sandboxes.md](docs/sandboxes.md) and [docs/models.md](docs/models.md).

## What it does differently

| | Legato | Typical reference loop |
|---|---|---|
| After each action | waits until the screen stops changing; on E2B the check runs inside the sandbox in one call | fixed sleep (Anthropic's demo: 2 s; OSWorld runners: 0–5 s) |
| Actions per turn | up to 3, one screenshot after them | one per call, or all returned with a screenshot after each |
| Screenshot history | old screenshots dropped 4 at a time past 8, so the provider's prompt cache keeps hitting | all kept, or one dropped per step |
| Model calls | timeout, retry, 4,096-token budget; a reply cut off before acting gets a note on the next turn | often unbounded |
| Timing | `result.report()` splits each run into model, action, waiting and screenshot time | none |

`legato.Harness.reference(...)` is a re-implementation of Anthropic's reference loop on the same plumbing, so you can
measure the difference on your own tasks.

## Reproduce the benchmarks

```bash
pip install "legatolabs[office,video]"
legato bench office generate --out bench --repeat 2          # 24 tasks: .xlsx data, .pptx decks, grader specs
legato rl collect --tasks bench/tasks.json --harness legato --policy-url https://openrouter.ai/api/v1 \
    --model qwen/qwen3.8-27b --api-key-env OPENROUTER_API_KEY --eval-only --osworld ~/OSWorld \
    --version legato --out runs/legato --desktops 6 --group 2 --max-steps 25
legato rl collect ... --harness qwen --no-ready-signal --fixed-wait 0 --version qwen-osworld --out runs/qwen-osworld
legato bench h2h --tasks bench/tasks.json --arm qwen-osworld=runs/qwen-osworld --arm legato=runs/legato \
    --ref qwen-osworld --out report
```

The desktops are OSWorld's Docker VMs, so you need a Linux host with KVM and an
[OSWorld](https://github.com/xlang-ai/OSWorld) checkout. See [docs/benchmarks.md](docs/benchmarks.md).

## Comparisons and migration

- [Legato vs Anthropic's computer-use demo](docs/compare/anthropic-computer-use-demo.md)
- [Legato vs OSWorld's Qwen agent](docs/compare/osworld-qwen-agent.md)
- [Legato vs Agent S3](docs/compare/agent-s3.md)
- [Legato vs UI-TARS-desktop](docs/compare/ui-tars-desktop.md)
- [Migrating from cua-agent](docs/migrate/from-cua-agent.md) (cua-agent is no longer maintained)
- [Migrating from Bytebot](docs/migrate/from-bytebot.md) (Bytebot is archived)

## MCP server

`legato mcp` gives Claude Code, Cursor or any MCP client a cloud desktop. Each action returns once the screen has
settled, with the new screenshot. See [docs/mcp.md](docs/mcp.md).

```bash
pip install "legatolabs[e2b,mcp]"
claude mcp add legato -e E2B_API_KEY=your_key -- legato mcp
```

## RL rollouts

`legato rl` collects graded desktop rollouts for computer-use RL. It records token logprobs and token ids, runs
GRPO-style groups with oversampling and straggler cancel, caps in-flight requests, and profiles each batch. You bring
the model server and the trainer. See [docs/rl.md](docs/rl.md).

## FAQ

- **Why `pip install legatolabs` but `import legato`?** The PyPI name `legato` was taken, so the package is published as
  `legatolabs`. The module, the command line tool and the docs all use `legato`.
- **Is Legato an alternative to cua-agent or Bytebot?** Yes, for the agent loop. Both are no longer maintained; see
  the migration guides above.
- **Which models work?** Any model behind an OpenAI-compatible chat API that accepts images. Native tool calls are
  used when the server supports them; otherwise JSON in the reply text is parsed. Measured so far: Claude Sonnet 5.5
  and Qwen3.8-27B. See [docs/models.md](docs/models.md).
- **Does it need a GPU?** No. Point it at a hosted API or your own server.
- **Is there a hosted version?** Optional: [legato.computer](https://legato.computer) runs desktops with pause,
  resume and fork. Nothing in this package requires it.

More in [docs/faq.md](docs/faq.md). License: Apache-2.0.
