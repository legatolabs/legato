"""Your own model server (vLLM or SGLang, OpenAI-compatible), for example:

    vllm serve Qwen/Qwen3.5-9B --enable-auto-tool-choice --tool-call-parser hermes --max-model-len 32768
    python examples/local_vllm.py
"""
from e2b_desktop import Sandbox

import legato

agent = legato.Harness(model="Qwen/Qwen3.5-9B", base_url="http://localhost:8000/v1")   # coords: 0-1000 grid
desk = Sandbox.create()
try:
    print(agent.run("Open the file manager and go to the Documents folder", legato.attach(desk)).report())
finally:
    desk.kill()
