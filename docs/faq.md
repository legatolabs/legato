# Legato FAQ

Legato is a fast, open-source (Apache-2.0) computer-use runtime: it sends screenshots to the vision model you choose,
runs the actions it picks on a desktop, and decides when the screen is ready for the next screenshot.

**How do I start?**
Tell Claude Code, Codex or Cursor "Set up Legato for me: https://legato.computer/setup", or run
`pip install legatolabs` and then `legato setup`, or download the Mac app ([setup](setup.md)).

**What are the Mac app, the terminal, the MCP server and the Python package?**
Four ways into the same agent. The Mac app is for people (hold Fn, say a task); `legato run` is for one task from a
terminal or a script; the MCP server gives Claude Code, Codex or Cursor a computer; the Python package is for building
your own agent or running RL. `legato setup` saves one model and desktop for the last three.

**Can I keep using my Mac while it works?**
Yes: on a Mac it works in one app's window in the background by default, without your mouse or keyboard
([how](sandboxes.md#in-the-background-while-you-keep-working-macos)). A task that moves between apps needs the whole
screen (`legato run --screen`).

**How is it different from Anthropic's computer-use demo?**
It runs any OpenAI-compatible model and waits for the screen instead of sleeping 2 s before every screenshot. On the
same Claude model it was 1.48x faster on office tasks, at the same success rate
([comparison](compare/anthropic-computer-use-demo.md)).

**Is it faster than OSWorld's Qwen agent?**
About the same speed, with more success: 71/96 vs 53/96 over two runs ([benchmarks](benchmarks.md)).

**cua-agent and Bytebot are unmaintained. What now?**
Legato covers the agent loop for both: [cua-agent](migrate/from-cua-agent.md), [Bytebot](migrate/from-bytebot.md).

**Does it include a model, or need a GPU?**
No. Bring any model: Qwen through OpenRouter, Claude, or your own vLLM server, which can run on the same machine.

**Can Claude Code, Cursor or Codex use it?**
Yes, through the MCP server: `legato setup` adds it to them, and they get a computer to look at and click
([coding agents](mcp.md)).

**Claude Code's `computer-use` server isn't in `/mcp`. What can I use?**
The built-in server needs a Mac, a Pro, Max or Ultra plan and an interactive session. Legato's MCP server works on
Linux and Windows, on Team and Enterprise plans, with Bedrock or Vertex AI, in `claude -p`, and in several sessions at
once ([guide](guides/claude-code-computer-use-anywhere.md)).

**Does it work on macOS and Windows?**
Yes, on your own computer: `legato.local()` drives the screen of a Mac, a Windows PC or a Linux desktop. Cloud
desktops are Linux today; managed Windows and macOS desktops are planned for Legato Cloud.

**Is it safe to let an agent use my computer?**
Use a desktop container (`--sandbox docker`) or a cloud desktop when you can: the agent then never touches your own
screen or files. On your own screen, move the mouse into a corner to stop it. Risky keys and typed commands (Cmd+Q,
Alt+F4, `rm`, `sudo`, `Remove-Item`...) wait for your yes, and the Mac app also asks before clicking Send, Delete or Pay.

**Where does my data go?**
Screenshots and the task go to the model you chose, and nowhere else. With a model on your own machine, nothing
leaves it. Legato has no server of its own and sends no telemetry; the Mac app checks GitHub for updates. The apps
Legato drives keep their own habits: TextEdit keeps each new document, the ones Legato's background mode opens too,
in iCloud Drive (`Untitled 3.rtf` in TextEdit's folder) until you close it without keeping it.

**Should my coding agent test my app with Playwright MCP or Legato?**
Playwright MCP, when the app is a web page and the accessibility tree describes what you are checking. Legato, when
the check is visual (a canvas, a layout), the app is not a web page, or the flow leaves the browser
([which to use](compare/playwright-mcp.md)).

**How does it compare with other computer-use MCP servers?**
Legato can give the agent a desktop container, any sandbox or a cloud desktop instead of your own screen, and each
action returns once the screen has settled. Several others act through the accessibility tree on your own screen
([comparison](compare/computer-use-mcp-servers.md)).

**Which desktops?**
Your own computer (macOS, Windows, Linux), any Linux sandbox you can run a command in (Docker, SSH, Daytona, your own
VMs), E2B, anything you can screenshot and send input to, and OSWorld's VMs ([sandboxes](sandboxes.md)). Managed
Windows and macOS cloud desktops are planned for Legato Cloud. For browser-only work, a Playwright-based tool such as Browser Use or Stagehand usually
fits better.

**RL training?**
RL rollouts at scale are part of Legato Cloud, in beta: the model's state and the desktop forked together, so rollouts
branch mid-task. Join the waitlist on [legato.computer](https://legato.computer) ([more](rl.md)).

**How do I check it on my own tasks?**
Compare `result.report()` from `legato.Harness` and `legato.Harness.reference` on them
(`examples/compare_with_reference.py`).

**Where is my key kept, and how do I remove it?**
A key in an environment variable (`OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`...) is read from there and never
written down: setup saves only the variable's name. A key pasted into `legato setup` (or given with `--api-key -`)
is saved in plain text in `~/.config/legato/config.json` (`C:\Users\you\.config\legato\config.json` on Windows),
readable by your user only. To remove it, delete that file, or run `legato setup --api-key-env OPENROUTER_API_KEY`
to go back to the variable. `LEGATO_MODEL`, `LEGATO_BASE_URL` and `LEGATO_API_KEY` override the saved choice for one
shell; a flag on the command wins over both. A saved key is only ever sent to the server it was saved for: with
`LEGATO_BASE_URL` pointing somewhere else, set `LEGATO_API_KEY` for that server.

**Can I use Claude, GPT and open models side by side?**
Yes. Save a key for each provider (`legato setup --add-key anthropic`, `--add-key openai`) or export its usual
variable (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`). Then name the model: `legato run --model claude-sonnet-5-5 "..."`,
`legato.run(..., model="gpt-6.2-sol")`, or `desktop_run_task(model=...)` over MCP. The name says where it runs
(`claude-...` on Anthropic's API, `gpt-...` on OpenAI's, `vendor/model` on OpenRouter), and each key goes only to its
own provider. `legato doctor --model claude-sonnet-5-5` tries one before a run.

**Is there a hosted version?**
Not publicly. Legato runs where you run it, with the model you bring. Legato Cloud, which also runs the model and the
desktop, is in a private beta: its measured parts are on the [benchmarks page](benchmarks.md), and the waitlist is on
[legato.computer](https://legato.computer).
