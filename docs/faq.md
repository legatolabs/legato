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
No. Bring any model: Qwen through OpenRouter, Claude, or your own vLLM server.

**Which desktops?**
Full desktops: E2B Desktop, any sandbox through `legato.attach(screenshot=..., execute=...)`, and OSWorld's Docker VMs
([sandboxes](sandboxes.md)). For browser-only work, a Playwright-based tool such as Browser Use or Stagehand usually
fits better.

**RL training?**
`legato rl collect` runs graded rollouts with token logprobs and GRPO groups ([rl.md](rl.md)).

**How do I check it on my own tasks?**
Compare `result.report()` from `legato.Harness` and `legato.Harness.reference` on them
(`examples/compare_with_reference.py`).

**Is there a hosted version?**
Optional: [legato.computer](https://legato.computer) runs desktops with pause, resume and fork.
