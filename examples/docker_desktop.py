"""A desktop container on this machine (needs only Docker), driven by any OpenAI-compatible model.

    OPENROUTER_API_KEY=... python examples/docker_desktop.py

The container (ghcr.io/legatolabs/desktop) has a terminal and Firefox; apps on your machine are reachable from inside
at http://host.docker.internal:PORT.
"""
import os

import legato

stop, env = legato.providers.docker(1280, 800)
try:
    agent = legato.Harness(model="qwen/qwen3.8-27b", base_url="https://openrouter.ai/api/v1",
                           api_key=os.environ["OPENROUTER_API_KEY"])
    print(agent.run("Open Firefox and go to example.com", env, max_steps=20).report())
finally:
    legato.providers.close(stop)
