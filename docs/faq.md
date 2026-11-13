# Legato FAQ

**What is Legato?**
An open-source (Apache-2.0) computer-use harness. It is the loop that sends screenshots to a vision-language model,
runs the actions the model chooses on a desktop, and decides when the screen is ready for the next screenshot. It
works with any OpenAI-compatible model.

**How is it different from Anthropic's computer-use demo?**
- It runs any model, not only Claude.
- It waits until the screen is ready instead of sleeping 2 s before every screenshot.
- It sends one screenshot per batch of actions, and bounds every call.
- On the same Claude model it finished office tasks 1.48x faster, at the same success rate.

See [compare/anthropic-computer-use-demo.md](compare/anthropic-computer-use-demo.md).

**Is it faster than OSWorld's Qwen agent?**
About the same speed on fresh tasks, with 19 points more success over two runs (74% vs 55%). See
[benchmarks.md](benchmarks.md).

**What should I use now that cua-agent and Bytebot are unmaintained?**
Legato covers the agent loop for both. See [migrate/from-cua-agent.md](migrate/from-cua-agent.md) and
[migrate/from-bytebot.md](migrate/from-bytebot.md).

**Does Legato include a model?**
No. Bring any model: Qwen through OpenRouter, Claude, or your own vLLM server.

**Does it need a GPU?**
No.

**Which sandboxes?**
- E2B Desktop out of the box.
- Any sandbox through `legato.attach(screenshot=..., execute=...)`.
- OSWorld's Docker VMs for benchmarks and RL.

See [sandboxes.md](sandboxes.md).

**Browsers only, or full desktops?**
Full desktops: office apps, file managers, terminals, and browsers inside the desktop. For browser-only automation, a
browser library (Playwright-based tools such as Browser Use or Stagehand) is usually the better fit.

**Can I use it for RL training?**
Yes. `legato rl collect` runs graded rollouts with token logprobs, GRPO groups and per-batch profiles, against your own
model server. See [rl.md](rl.md).

**How do I check it on my own tasks?**
Run `legato.Harness` and `legato.Harness.reference` on the same tasks and compare `result.report()`. See
`examples/compare_with_reference.py`.

**Is there a hosted version?**
Optional: [legato.computer](https://legato.computer) runs desktops with pause, resume and fork. The open-source package
never requires it.
