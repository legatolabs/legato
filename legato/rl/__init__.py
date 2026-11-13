"""legato.rl: graded desktop rollouts for computer-use RL.

The trainer keeps the weights and the model server; the sandboxes stay wherever they run. legato.rl runs the step
loop between them (screenshot, policy call, action, wait until the screen is ready), resets desktops between tasks,
grades each attempt and writes trajectories the trainer can consume (token ids, logprobs, rewards, group
advantages, policy version). See docs/rl.md for the format and the CLI.
"""
from legato.rl.collect import DesktopConfig, HarnessConfig, collect, plan_jobs
from legato.rl.policy import PolicyClient, PolicyConfig, PolicyError
from legato.rl.profile import report, summarize
from legato.rl.trajectory import export_steps, group_advantages, load_messages, load_request, read_rollouts

__all__ = ["DesktopConfig", "HarnessConfig", "PolicyClient", "PolicyConfig", "PolicyError", "collect",
           "export_steps", "group_advantages", "load_messages", "load_request", "plan_jobs", "read_rollouts", "report", "summarize"]
