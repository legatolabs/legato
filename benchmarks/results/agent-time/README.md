# Where agent time goes (public traces)

`tracelab_decomposition_v2.json` is the output of `analyze_tracelab2.py` over the TraceLab release v0.0.1 (UW SyFI):
665,453 model rounds in 8,058 Claude Code and Codex sessions from 43 developers, with a timestamp for every event.

A step is one model invocation plus the tool calls it issues. Generation time runs from the step's first input event
to its last assistant event (queueing, prefill, decode and network). Tool time is the longest of the step's calls.
Waits on a person (questions, plan approval) and on subagents are kept apart from machine tools.

| | Claude Code | Codex |
|---|---|---|
| steps with at least one machine tool | 261,835 | 173,077 |
| mean generation per step | 16.5 s | 10.3 s |
| mean machine tool time per step | 18.7 s | 8.3 s |
| machine share of step wall-clock, time-weighted | 53% | 45% |
| the same if generation were 5x faster | 85% | 80% |
| machine share at the median step | 3% | 3% |

The share is time-weighted: most tool calls are short, and the long ones (builds, tests, polling a running process)
hold most of the time. Run 3 Sep 2026 on public traces; no GPU.
