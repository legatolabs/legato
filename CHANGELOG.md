# Changelog

## Unreleased

- MCP: screenshots go to the agent as JPEG (quality 85), like the harness since #17; `legato mcp --image-format png`
  keeps PNG. Recordings (`--record`) stay PNG.
- Plans read labels (`click_text`, `wait_for`, labelled clicks) on Linux too: where macOS Vision is not available,
  `legato.plan` uses RapidOCR if it is installed (`pip install rapidocr-onnxruntime`, CPU). A label also matches a
  line the reader ran together ("Document saved" in "Documentsaved").
- `Harness(plan=True)`: one `plan` tool call carries the steps of a turn (screen actions, `click_text` by visible
  label, shell, AppleScript, `wait_for` a label, done), run in order until one cannot go on; the model then sees what
  ran and why it stopped. Labels are read with macOS Vision. `end_fix=True`: a reply that says the task is complete
  without an action gets a note, and a second one in a row ends the task as done.
- `legato.tools.applescript(env)`: AppleScript that is compiled against the app's dictionary first and read back with
  a `check` script; an error returns the app's real terms.
- `Harness(image_format="jpeg")`: screenshots go to the model as JPEG, the same pixels in about a fifth of the bytes.
  The upload is most of a hosted call's wait for its first token on a home uplink; on replayed Mac turns through
  OpenRouter it halved a call with the next action unchanged (legato-research EXP-0052), and on 27 live Mac tasks it
  cut a median task from 23.7 to 18.5 s with passes held (EXP-0053). JPEG is the default; `image_format="png"` sends
  the screenshots lossless.
- `legato.local()` is safe to run on your own Mac: move the mouse into a corner or hold Escape to stop it, and risky
  keys and typed shell commands (`legato/safety.py`) wait for a yes (`confirm=`, `legato run --yes`). `legato doctor`
  checks the macOS permissions. `wait_scope="app"` waits on the front app's windows only; `wait="ax|cells"` ends a
  wait on the front app's Accessibility events.
- Off by default, for experiments: `change_log` (one screenshot and a text log of what changed on the screen, with
  `change_log_lines` and `change_log_turns`), `progress_note`, `history_scale`, `hedge_after_s`, `ocr_cache`,
  `ocr_near`, `plan_trust_first_click`, `strict_done`.

- MCP: `desktop_open(url=..., app=...)` starts a page or app first and returns once its window has drawn, so the agent
  skips typing a launch command and waiting for it.
- MCP: `desktop_shell(desktop_id, command, timeout_s=30)` runs a shell command on the desktop's computer and returns
  its output and exit code, so an agent can start apps, read files and check results without the screen.
- A native in-sandbox helper (Rust, `native/legato-step`) for Linux sandboxes and E2B, on x86_64 and arm64. Each
  action is faster: it reads the screen through shared memory instead of running `xwd`, and encodes the screenshot
  while the wait sits out its quiet time. The stop rule and the JSON it answers are unchanged. Sandboxes it can't run
  in keep the Python helper; `LEGATO_HELPER=python` chooses it.
- Requests to OpenRouter name Legato as the app (`HTTP-Referer`, `X-Title`); `LEGATO_NO_ATTRIBUTION=1` turns it off.
- `Harness(tools=[...])`: tools beside `computer`. `legato.tools.bash(env)` runs commands in the desktop's own shell
  (E2B, `attach(shell=...)`, `--sandbox docker`, `legato.local()`); `legato.Tool` wraps your own function. Calls run
  in the order the model sent them, each result goes back as text, and a tool that raises is reported to the model.
  Models without native tool calls are told the JSON form. Without `tools`, requests are unchanged.
  `result.report()` shows tool time.

## 0.1.8

- `triple_click` (selects a line or a whole field), on every desktop: Linux sandboxes, E2B, your own screen and
  OSWorld. Models trained on Claude's computer-use actions send it, and it used to be rejected.
- A busy or starting model server is waited out: a 429 or 503 is retried after its Retry-After, for up to
  `busy_wait_s` (120 s) in all, without using up `retries`. A cold GPU no longer ends a run.

## 0.1.7

- `LEGATO_DOCKER_ARGS`: extra `docker run` arguments for `--sandbox docker`, such as a volume with your project.

## 0.1.6

- `legato mcp --record DIR`: every screenshot the agent got, with a log of its actions.
- `--sandbox docker` hands over the desktop once its first window is up, so the first screenshot is not black.

## 0.1.5

- The MCP server tells the agent what kind of desktop it has (for docker: a terminal is open, and the user's apps are
  at `host.docker.internal`; for local: these are the user's real windows).
- `--sandbox docker` pulls or builds the image as the server starts, and says plainly when Docker is missing or not
  running. The image is published for arm64 too, and opens Firefox without first-run tabs.
- A Claude Desktop extension, `legato.mcpb`, attached to each release (Python 3.11 or later).
- A second skill, `desktop`, on using the desktop tools well.

## 0.1.4

- `legato doctor [--json]`: which sandboxes, extras and model keys are ready, with the fix for each.
- A Claude Code plugin in the repo: `/plugin install legato --marketplace legatolabs/legato` adds the MCP server
  (with `--sandbox docker`) and the use-legato skill. The repo also carries Agent Plugins files (`plugin.json`,
  `mcp.json`) for other editors.
- The MCP Registry entry now starts the server with `--sandbox docker`, so it needs no key.

## 0.1.3

- `--sandbox docker`: a fresh desktop container on this machine (`ghcr.io/legatolabs/desktop`: a terminal and
  Firefox), with apps on the host reachable at `http://host.docker.internal:PORT`. `--sandbox docker:IMAGE` for your
  own image. Also `legato.providers.docker(width, height)` from Python.
- `legato.providers` is importable from `legato`.

## 0.1.2

- Bring your own computer. `legato.local()` drives this computer's screen on macOS, Windows or Linux (X11), with
  `pip install "legatolabs[local]"`.
- `legato.attach(shell=run)` drives any Linux sandbox you can run a command in (Docker, SSH, Daytona, your own VMs),
  with one call per action.
- `legato run` and `legato mcp` take `--sandbox e2b|local|path/to/file.py:function`, and `legato mcp --local`.
- `legato run` on E2B now opens a 1280x800 desktop with a one-hour limit, and closes it at the end.

## 0.1.1

- `legato run --json` prints one JSON result, for scripts and coding agents.
- Clear errors when the E2B extra, `E2B_API_KEY` or the model key is missing.
- A `legatolabs` command alias, so `uvx legatolabs mcp` works.
- Ships `py.typed`.
- Listed on the MCP Registry as `io.github.legatolabs/legato`.

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
