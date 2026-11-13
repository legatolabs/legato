"""legato: an open computer-use harness for any OpenAI-compatible model. https://legato.computer

    agent = legato.Harness(model, base_url, api_key)
    result = agent.run(task, legato.attach(sandbox))      # a cloud sandbox
    result = agent.run(task, legato.local())              # or this computer
    agent = legato.Harness(model, base_url, api_key, tools=[legato.tools.bash(env)])   # a shell beside the screen

The optional hosted API (legato.computer) lives in `legato.hosted`.
"""
from legato import hosted, providers, tools
from legato.actions import Action, parse
from legato.env import Env, Obs, attach
from legato.harness import Harness, RunResult, computer_tool
from legato.local import local
from legato.settle import Waiter, wrap
from legato.tools import Tool

__all__ = ["Action", "Env", "Harness", "Obs", "RunResult", "Tool", "Waiter", "attach", "computer_tool", "hosted", "local",
           "parse", "providers", "tools", "wrap"]
__version__ = "0.1.8"
