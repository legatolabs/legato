# MCP-H2H: Claude Code + Legato MCP vs Claude Code's built-in computer use

Issue #21. Same agent (Claude Code), same model, same Mac. Only the computer layer changes.

| arm | the computer |
|---|---|
| `builtin` | Claude Code's built-in computer use |
| `legato` | `legato mcp --sandbox local` |
| `legato-app` | the same, waiting on the front app's windows only (`--wait-scope app`) |
| `legato-slow` | control: `--fixed-wait 2 --image-format png` (no ready-wait, no JPEG) |

**Tasks** (`tasks.py`): five short GUI tasks with end states a script can check:
- `textedit-new`
- `finder-rename`
- `textedit-edit`
- `form-signup`
- `form-todo`

The web pages are served by `taskweb.py` on localhost:8765. The agent's Bash, file and web tools are off, so every task goes through the screen.

**How it runs** (`run.py`):
- Each run starts an interactive `claude` in tmux, because built-in computer use is not available with `-p`.
- The driver sends the task, then reads the time, turns and tool calls from the session transcript.
- Arms run interleaved, in a shuffled order each time.

## Before you run it

1. `brew install tmux`
2. Turn on **Accessibility** and **Screen Recording** for the terminal you run it from (System Settings → Privacy & Security). Then run it from that terminal, not from inside another app.
3. Enable built-in computer use in its folder, once: `cd ~/legato-bench-arms/builtin && claude`, then `/mcp` → `computer-use` → Enable.
4. Close or hide personal apps (Messages, mail). The agent sees the whole screen, and other apps animating slow the `legato` arm's waits.
5. Leave the Mac alone while it runs. Every arm moves the real mouse.

```bash
python benchmarks/mcp_h2h/run.py --smoke legato textedit-new   # one run, to check the setup
python benchmarks/mcp_h2h/run.py --reps 3                      # 4 arms x 5 tasks x 3 = 60 runs
python benchmarks/mcp_h2h/run.py --summary                     # the table
```

## Bars, fixed before the run

- **Primary:** `legato` / `builtin` time, the sum of per-task mean seconds, with a 95% task bootstrap interval. "Faster" means the upper bound is under 1.
- **Success:** a faster arm counts only if it passes at least as many runs as `builtin`, minus one.
- Results go to `benchmarks/results/mcp-h2h-1/runs.jsonl`. Numbers go public only through `docs/benchmarks.md`.
- Runs are on the Claude Code plan, not API spend.
