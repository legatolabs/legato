# Legato FAQ

Legato is an open-source (Apache-2.0) computer-use harness: it sends screenshots to a vision-language model, runs the
actions it chooses on a desktop, and decides when the screen is ready for the next screenshot.

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
Yes, through the MCP server: `claude mcp add legato -- legato mcp --sandbox docker` gives the agent a desktop container
to look at and click ([guide](guides/coding-agent-computer.md)).

**Does it work on macOS and Windows?**
Yes, on your own computer: `legato.local()` drives the screen of a Mac, a Windows PC or a Linux desktop. Cloud
desktops are Linux today; managed Windows and macOS desktops are planned for Legato Cloud.

**Is it safe to let an agent use my computer?**
Use a desktop container (`--sandbox docker`) or a cloud desktop when you can: the agent then never touches your own
screen or files. On your own screen, move the mouse into a corner to stop it.

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
`legato rl collect` runs graded rollouts with token logprobs and GRPO groups ([rl.md](rl.md)).

**How do I check it on my own tasks?**
Compare `result.report()` from `legato.Harness` and `legato.Harness.reference` on them
(`examples/compare_with_reference.py`).

**Is there a hosted version?**
Optional: [legato.computer](https://legato.computer) runs desktops with pause, resume and fork.
