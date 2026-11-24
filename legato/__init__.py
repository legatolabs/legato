"""legato: a fast computer-use agent for any OpenAI-compatible model. https://legato.computer

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
from legato.env import Env, Obs, attach
from legato.delegate import Planner
from legato.harness import Harness, RunResult, computer_tool
from legato.local import local
from legato.settle import Waiter, wrap
from legato.tools import Tool

__all__ = ["Action", "Env", "Harness", "Obs", "Planner", "RunResult", "Tool", "Waiter", "agent_for", "attach",
           "computer_tool", "config", "local", "parse", "providers", "run", "tools", "wrap"]
__version__ = "0.3.0"
