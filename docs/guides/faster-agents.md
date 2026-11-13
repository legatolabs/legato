# Why computer-use agents are slow, and how to make them faster

A computer-use step is: take a screenshot, call the model, act, wait for the screen. Three things make it slow.

- **Fixed sleeps.** Many loops sleep after every action. Anthropic's reference loop sleeps 2 s before each screenshot.
  In our OSWorld runs, 40–53% of rollout time was idle under fixed sleeps ([benchmarks](../benchmarks.md)).
- **Model calls.** Planning, reflection and judging calls are most of an agent's latency, and later steps can take 3x
  longer than early ones as the history grows. Even the best agents take 2.7–4.3x more steps than a person
  ([OSWorld-Human, arXiv 2506.16042](https://arxiv.org/abs/2506.16042)).
- **A cold prompt cache.** Dropping the oldest screenshot each turn changes the prompt's prefix, so the cache misses
  and the whole history is prefilled again.

What Legato does about each:

| problem | Legato |
|---|---|
| fixed sleeps | waits until the screen stops changing, inside the sandbox when it can, never longer than the 2 s it replaces |
| one action per call | up to 3 actions per turn, with one screenshot after them |
| cache misses | keeps 8 screenshots and drops 4 at a time, so the prefix stays the same between drops |
| stuck calls | a wall-clock timeout and a token budget on every model call |

The result, with the same Claude model on the same 24 office tasks: 1.48x faster than the reference loop, 48/48 tasks
in both. Per task, Legato waited 14.0 s for the screen against 44.5 s, and each model call took 3.02 s against 4.17 s.

To measure your own loop, run the same tasks through `legato.Harness` and `legato.Harness.reference` and compare
`result.report()`, which splits each run into model time, action time and waiting
([example](https://github.com/legatolabs/legato/blob/main/examples/compare_with_reference.py)).
