# Benchmarks

Same model, same desktops, all arms run at the same time.

| Model | Legato vs | Time | Success |
|---|---|---|---|
| Claude Sonnet 5.5 | Anthropic's reference loop | 0.678 [0.604, 0.757], 1.48x faster | 48/48 vs 48/48 |
| Qwen3.8-27B | OSWorld's Qwen agent | 0.953 [0.798, 1.134], same | 71/96 vs 53/96 (two runs) |

## Method

- **Tasks:** the Legato Office Bench, 24 Excel-to-PowerPoint tasks in LibreOffice (`legato bench office generate`). A
  python-pptx grader checks the saved deck.
- **Desktops:** OSWorld's Docker VMs, 1920x1080, one AWS host. All models are called through OpenRouter.
- **Metric:** agent time, from the first screenshot to the last model turn. Ratios are summed per-task means, with a
  95% task-bootstrap interval. The bars were fixed before each run.
- **Reference loop:** `legato.Harness.reference`, our re-implementation of Anthropic's computer-use demo loop: 2 s
  before each screenshot, a screenshot per action, all screenshots kept.
- **OSWorld's Qwen agent:** `mm_agents/qwen` at commit 8903ebe, unmodified, with 0 s wait.
- **Caveats:**
  - The Claude run used Legato's earlier 1,024-token budget.
  - Every arm called models through Legato's client.
  - All arms ran with a 25-step cap; OSWorld's runner default is 50.

## H2H-1: Claude and Qwen (24 tasks, 2 attempts each, 0 errors)

| arm | success | agent s | calls/task | model s/call | wait s/action | $/task |
|---|---|---|---|---|---|---|
| Claude, reference loop | 48/48 | 96.9 | 10.6 | 4.17 | 1.91 | 0.137 |
| Claude, Legato | 48/48 | 65.7 | 14.0 | 3.02 | 0.71 | 0.126 |
| Qwen, OSWorld agent | 26/48 | 212.5 | 23.2 | 7.90 | 0.00 | 0.058 |
| Qwen, Legato | 36/48 | 90.0 | 18.0 | 4.28 | 0.67 | 0.034 |

- **Claude:** the saving is waiting. Per task, Legato waited 14.0 s for the screen against 44.5 s, with about the same
  model time.
- **The replay on legato.computer** is `office-11-paste_chart`, attempt 1: 151.7 s of agent time for the reference
  loop, 78.8 s for Legato, both passed.
- **Qwen:** this run's speed gap came from four runaway 32,768-token replies in OSWorld's agent. It did not replicate
  (see H2H-2).

## H2H-2: Qwen again, with the fix, on fresh task data (24 tasks, 2 attempts each, 0 errors)

| arm | success | agent s | calls/task | model s/call | turns cut off at the token limit |
|---|---|---|---|---|---|
| Qwen, OSWorld agent | 27/48 | 121.2 | 23.0 | 4.76 | 0.0% |
| Qwen, Legato | 35/48 | 115.5 | 19.7 | 5.06 | 0.1% (was 22.5%) |

## OSWorld tasks on OSWorld's own harness (runtime only)

120 OSWorld tasks with Qwen3.5-9B and OSWorld's harness unchanged. Only the waits and the reset differ. This is not an
official OSWorld-Verified submission.

| Legato vs | task time | success difference |
|---|---|---|
| fixed 5 s per action | 0.521 [0.425, 0.648] | +1.2 [-3.3, +6.2] pts |
| fixed 3 s | 0.542 [0.451, 0.649] | +4.2 [-1.2, +9.6] pts |
| no wait | 0.674 [0.523, 0.892] | +7.9 [+2.5, +13.8] pts |

With fixed sleeps, 40–53% of rollout time was idle.

## Legato Cloud: what the managed version adds

The open-source loop runs anywhere. Legato Cloud also runs the model and the desktop, side by side, so it can save time
the loop alone cannot. These are measurements of each part, not end-to-end task times. Raw data:
[benchmarks/results/cloud](../benchmarks/results/cloud).

**Live in the beta**

| what | measured |
|---|---|
| A warm prefix cache: each run stays on one engine, and its next request is prefilled while the screen settles | a new screenshot with the history already cached prefills in 70 ms, vs 158 ms for the whole conversation (UI-TARS-1.5-7B and Qwen2.5-VL-7B); 255 ms vs 676 ms (Qwen2.5-VL-32B); 133 real desktop steps |
| Speculative decoding (MTP-4) on the hosted Qwen3.8-27B | median task 1.46 s, vs 1.85 s without it (59 web tasks; 54 vs 55 succeeded) |
| Pause, resume and fork a running desktop, memory included | see the table below |
| Desktops from a warm pool | booting is off the critical path |

**In development**

| what | measured so far |
|---|---|
| Memory-snapshot forks | a running desktop restored in 0.7–0.9 s, vs 3.9–7.4 s to boot one and replay to the same point; 36 of 36 restores had the same screen and files, and 15 of 16 forks finished their task |
| Forking a whole run, not only its desktop | the fork keeps the model's cached history, so it continues without prefilling it again (not yet measured) |
| Denser desktops | 16 desktops per container: 0.67 container-seconds per episode, vs 10.46 with one per container (scripted replays; 29 of 30 vs 30 of 30 passed) |

### Pause, resume and fork (live check)

| step | E2B SDK | through the API |
|---|---|---|
| pause | 0.63 s | 0.87 s |
| resume | 0.19–0.40 s | 0.48 s |
| fork | 1.71 s | 2.04 s |

A running process survives all three, and a fork is isolated from its parent. These are single checks, not a
benchmark.

## A coding agent on a desktop container (single check)

Claude Code with `legato mcp --sandbox docker` checked a signup form served from the host: it opened Firefox from the
desktop's terminal, tried an invalid and a valid email, and reported both messages correctly. The MCP server's
`--record` log has every action and its wait.

| run | actions | wait for the screen per action | Claude Code, end to end |
|---|---|---|---|
| signup form, invalid then valid email | 9 | median 0.71 s (0.39–1.04 s) | 35.1 s, 14 turns |

One run, not a benchmark. Raw data: [benchmarks/results/mcp/claude-code-signup](../benchmarks/results/mcp/claude-code-signup).

## What these results do not show

- Only office tasks.
- Only two models are measured end to end.
- The Claude result is one run.
- There is no head-to-head yet against Agent S3 or UI-TARS-desktop.
- The Legato Cloud numbers measure parts (prefill, decoding, forks, density), not whole tasks.

## Raw data

- Rollouts, task lists and reports are in [benchmarks/results](../benchmarks/results).
- To reproduce, you need a Linux host with KVM and an [OSWorld](https://github.com/xlang-ai/OSWorld) checkout:

```bash
pip install "legatolabs[office,video]"
legato bench office generate --out bench --repeat 2
legato rl collect --tasks bench/tasks.json --harness legato --policy-url https://openrouter.ai/api/v1 \
    --model qwen/qwen3.8-27b --api-key-env OPENROUTER_API_KEY --eval-only --osworld ~/OSWorld \
    --version legato --out runs/legato --group 2 --max-steps 25
legato rl collect ... --harness qwen --no-ready-signal --fixed-wait 0 --version qwen-osworld --out runs/qwen-osworld
legato bench h2h --tasks bench/tasks.json --arm qwen-osworld=runs/qwen-osworld --arm legato=runs/legato \
    --ref qwen-osworld --out report
```
