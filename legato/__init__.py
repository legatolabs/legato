"""legato: a fast computer-use runtime for any OpenAI-compatible model. https://legato.computer

    result = legato.run("Find the weather in Tokyo tomorrow")       # the model and desktop `legato setup` saved
    print(result.answer, result.report())

    agent = legato.Harness(model, base_url, api_key)                 # or choose each part yourself
    result = agent.run(task, legato.local(background="Safari"))      # one app's window on a Mac, in the background
    result = agent.run(task, legato.local())                          # this whole screen
    result = agent.run(task, legato.attach(sandbox))                  # a cloud desktop or any sandbox
    agent = legato.Harness(model, base_url, api_key, tools=[legato.tools.bash(env)])   # a shell beside the screen
"""
from legato import config, providers, tools
from legato.actions import Action, parse
from legato.agent import agent_for, run
from legato.env import DesktopError, DesktopStartError, Env, Obs, attach
from legato.delegate import Planner
from legato.harness import Harness, RunResult, RunStatus, Usage, computer_tool
from legato.local import local
from legato.settle import Waiter, wrap
from legato.tools import Tool
from legato.verify import Receipt, VerifiedResult, VisibleResult, checked_step, verified_step

__all__ = ["Action", "DesktopError", "DesktopStartError", "Env", "Harness", "Obs", "Planner", "Receipt", "RunResult",
           "RunStatus", "Tool", "Usage", "VerifiedResult", "VisibleResult", "Waiter", "agent_for", "attach",
           "checked_step", "computer_tool", "config", "local", "parse", "providers", "run", "tools", "verified_step",
           "wrap"]
__version__ = "0.3.2"
