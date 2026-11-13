# Benchmarks: is Legato faster or more reliable than the reference computer-use loops?

**Short answer.** On the same model and the same desktops:
- **Claude Sonnet 5.5.** Legato finished office tasks 1.48x faster than a re-implementation of Anthropic's
  computer-use reference loop. Success was 48/48 for both (one run).
- **Qwen3.8-27B.** Legato matched the speed of OSWorld's Qwen agent scaffold and solved more tasks: 74% vs 55% over
  two runs on independent task sets.

All the numbers, the method and the raw rollouts are below, including where Legato did not win.

## Method

- **Tasks: the Legato Office Bench.** Excel-to-PowerPoint workflows on a real Ubuntu desktop with LibreOffice Calc and
  Impress, generated from seeded data (`legato bench office generate`). There are 12 task kinds:
  - copy a total;
  - copy a title;
  - find the top region;
  - list the top three;
  - compute a total;
  - build a region table;
  - add a "Next Steps" slide;
  - paste a chart.

  The grader opens the saved .pptx with python-pptx and checks the content. A rollout passes only if every check
  passes.
- **Desktops.** OSWorld's Docker VMs (Ubuntu, 1920x1080) on one AWS m7i.metal-24xl. Every arm runs at the same time
  with its own 4–6 desktops and the same fast reset.
- **Models.** Every model is called through OpenRouter: `anthropic/claude-sonnet-5.5` and `qwen/qwen3.8-27b`.
- **Metric.** Agent time runs from the first screenshot to the model's last turn. Reset and grading are excluded. The
  ratio is the sum of per-task mean agent times, arm over reference, with a 95% task-bootstrap interval.
- **Bars, fixed before each run.**
  - "Faster" only if the interval's upper bound is below 1.
  - "Substantially faster" only at 0.67 or below.
  - Success may drop at most 8 points.
- **Arms.**
  - `reference`: `legato.Harness.reference`, a re-implementation of Anthropic's computer-use reference loop
    (anthropics/claude-quickstarts, `computer-use-demo`). Every returned tool call runs, it sleeps 2 s before each
    screenshot and sends a screenshot after every action, all screenshots stay in the history, and replies are
    free-form. Two differences from the shipped demo: the history is text rather than tool_result blocks, and the
    first screenshot is given rather than requested (the second favours the reference).
  - `qwen-osworld`: OSWorld's Qwen agent scaffold (`mm_agents/qwen` at commit 8903ebe) unmodified, with OSWorld's
    runner default of 0 s wait.
  - `legato`: `legato.Harness` with the defaults of the time.
    - In H2H-1 that meant a 1,024-token output budget and no cut-off note.
    - H2H-2 used today's defaults: 4,096 tokens and the cut-off note.
    - The Claude result has not been re-measured with today's defaults.
- **Shared plumbing.** Every arm, OSWorld's agent included, called the model through Legato's client (`legato.rl`),
  with a 90 s per-attempt timeout and one retry. OSWorld's agent therefore did not use its own client's settings.
  - At the time, that timeout bounded only the wait between bytes. OpenRouter keeps a connection busy while it
    generates, so a 32,768-token reply could run for 818 s.
  - The timeout is now a wall-clock deadline per attempt.
- **Step cap.** 25 steps per rollout in every arm. OSWorld's runner default is 50.

## H2H-1: Claude and Qwen (24 tasks, 2 attempts each, 0 errors)

| arm | success | agent s (mean) | calls/task | actions/call | model s/call | output tokens/call | input tokens/call | wait s/action | $/task |
|---|---|---|---|---|---|---|---|---|---|
| Claude, reference loop | 48/48 | 96.9 | 10.6 | 2.20 | 4.17 | 339 | 18,667 | 1.91 | 0.137 |
| Claude, Legato | 48/48 | 65.7 | 14.0 | 1.40 | 3.02 | 225 | 8,603 | 0.71 | 0.126 |
| Qwen, OSWorld agent | 26/48 | 212.5 | 23.2 | 3.07 | 7.90 | 549 | 25,852 | 0.00 | 0.058 |
| Qwen, Legato | 36/48 | 90.0 | 18.0 | 0.74 | 4.28 | 343 | 12,267 | 0.67 | 0.034 |

- **Claude, Legato vs the reference loop.** **0.678 [0.604, 0.757]**, that is 1.48x faster.
  - It passed the "faster" bar but missed the pre-set "substantially faster" bar of 0.67, by 0.008.
  - Per task, model time was 42.4 s vs 44.1 s and waiting was 14.0 s vs 44.5 s. Legato made more calls, but each call
    was cheaper.
- **Qwen.** 0.424 [0.299, 0.622], with +21 points of success. **This speed number does not hold up:** see H2H-2.
- **What we found and fixed.** 22.5% of Legato's Qwen turns hit the old 1,024-token limit mid-reasoning and returned
  no action.

## H2H-2: Qwen again, with the fix, on fresh task data (24 tasks, 2 attempts each, 0 errors)

| arm | success | agent s (mean) | calls/task | model s/call | output tokens/call | input tokens/call | turns cut off at the token limit |
|---|---|---|---|---|---|---|---|
| Qwen, OSWorld agent | 27/48 | 121.2 | 23.0 | 4.76 | 169 | 24,039 | 0.0% |
| Qwen, Legato | 35/48 | 115.5 | 19.7 | 5.06 | 257 | 12,695 | 0.1% (was 22.5%) |

- **Time.** **0.953 [0.798, 1.134]**: not faster. **Success** +17 points.
- **Why H2H-1's 2.36x did not replicate.** OSWorld's agent allows replies up to 32,768 tokens. In H2H-1, four of its
  rollouts ran to that limit and spent 12–16 minutes each in the model. In H2H-2 its longest reply was 2,367 tokens.
- **Why Legato is not faster per call here.**
  - It sends half the input per call, but on a hosted API output tokens dominate.
  - It waits about 0.6 s per action for the screen, where OSWorld's agent waits 0 s. That is part of why it acts on
    fresh screens and succeeds more.
- **What the two runs support for open models.** About the same speed as OSWorld's Qwen agent, 19 points more success
  (71/96 vs 53/96 pooled), and bounded output per call.

## OSWorld tasks on OSWorld's own harness (runtime only)

These 120 OSWorld tasks with Qwen3.5-9B on one H100, 1,560 rollouts, used OSWorld's harness unchanged. Only the
runtime underneath changed: Legato's ready-wait and fast reset (`legato.rl`), against OSWorld's fixed waits.

| comparison | task time [95% CI] | success difference [95% CI] |
|---|---|---|
| Legato vs fixed 5 s per action | 0.521 [0.425, 0.648] | +1.2 [-3.3, +6.2] points |
| Legato vs fixed 3 s | 0.542 [0.451, 0.649] | +4.2 [-1.2, +9.6] |
| Legato vs no wait (0 s) | 0.674 [0.523, 0.892] | +7.9 [+2.5, +13.8] |

- Task time includes a 30 s penalty per failure, paired by task.
- With fixed waits, 40–53% of rollout time was waiting the screen did not need.
- Dropping one screenshot per step cut the prefix-cache hit rate from 85% to 25%. Dropping them in chunks kept 58%.
- This is not an official OSWorld-Verified submission.

## Managed desktops: pause, resume and fork (live check)

The managed service keeps desktops on E2B. It can pause one with its memory, resume it, and fork it into a copy that
runs independently. Checked on 12 November 2026 against real sandboxes, both directly and through the deployed public
API.

| step | direct (E2B SDK) | through the API | checked |
|---|---|---|---|
| pause | 0.63–0.64 s | 0.87 s | E2B reports the sandbox `paused`, still paused 20 s later |
| resume | 0.19–0.40 s | 0.48 s | a background process and a file both survive |
| fork | 1.71 s | 2.04 s | the copy has the parent's files and running process; a file written in the copy does not appear in the parent |

Times through the API include the round trip from the test machine to the gateway. These are single checks, not a
benchmark.

## What these results do not show

- **Tasks.** Office tasks only. Browser-heavy, terminal-heavy or long-horizon tasks may behave differently.
- **Models.** Only two models are measured end to end through `legato.Harness`. DeepSeek, UI-TARS and others are
  untested.
- **Run count.** The Claude result is a single run.
- **Other harnesses.** No head-to-head yet against Agent S3 or UI-TARS-desktop. See the comparison pages for how they
  differ.

## Raw data

`benchmarks/results/h2h-1/` and `benchmarks/results/h2h-2/` hold:
- the generated task list;
- every rollout (`*.rollouts.jsonl.gz`: per step, the model reply, token usage, cost and per-action timing);
- the reports;
- a side-by-side video for one task (`demo-claude.mp4`).

Re-run the analysis with `legato bench h2h`.
