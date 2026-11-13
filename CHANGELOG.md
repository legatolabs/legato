# Changelog

## 0.1.0

- `legato.Harness`: a computer-use loop for any OpenAI-compatible model. It has a ready-wait, chunked screenshot
  history, up to 3 actions per turn, bounded calls, a 4,096-token budget with a cut-off note, and per-phase timing.
- `legato.Harness.reference`: a re-implementation of Anthropic's computer-use reference loop, for comparison.
- `legato.attach`: E2B Desktop with a single-call in-sandbox wait, or any sandbox through screenshot and execute
  functions.
- `legato rl`: graded rollouts for computer-use RL on OSWorld VMs, with logprobs, GRPO groups, oversampling and
  per-batch profiles.
- `legato mcp`: an MCP server with cloud desktops (E2B) for any agent; each action returns the settled screenshot.
- `legato bench`: the Legato Office Bench (Excel-to-PowerPoint tasks with a python-pptx grader) and multi-arm reports.
