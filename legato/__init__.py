"""legato: an open computer-use harness for any OpenAI-compatible model. https://legato.computer

    agent = legato.Harness(model, base_url, api_key)
    result = agent.run(task, legato.attach(sandbox))      # a cloud sandbox
    result = agent.run(task, legato.local())              # or this computer

The optional hosted API (legato.computer) lives in `legato.hosted`.
"""
from legato import hosted, providers
from legato.actions import Action, parse
from legato.env import Env, Obs, attach
from legato.harness import Harness, RunResult, computer_tool
from legato.local import local
from legato.settle import Waiter, wrap

__all__ = ["Action", "Env", "Harness", "Obs", "RunResult", "Waiter", "attach", "computer_tool", "hosted", "local",
           "parse", "providers", "wrap"]
__version__ = "0.1.3"
