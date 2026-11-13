# Run a computer-use agent with a local model

Serve an open vision model on your own GPU, point Legato at it, and run it on your own screen or in a desktop container.
No API key, and no screenshot leaves your machine.

```bash
pip install "legatolabs[local]"
vllm serve Qwen/Qwen3.5-9B --enable-auto-tool-choice --tool-call-parser hermes --max-model-len 32768

legato run "Create a folder named reports on the desktop" --sandbox docker \
    --model Qwen/Qwen3.5-9B --base-url http://localhost:8000/v1
```

`--sandbox docker` runs the agent in a container, so it can't touch your own screen. `--sandbox local` drives your
real screen, mouse and keyboard ([setup and safety](../sandboxes.md#this-computer)). From Python:

```python
import legato

agent = legato.Harness(model="Qwen/Qwen3.5-9B", base_url="http://localhost:8000/v1")   # 0-1000 coordinates
print(agent.run("Create a folder named reports on the desktop", legato.local()).report())
```

- **Which model.** Any vision model behind an OpenAI-compatible API that accepts images. Our OSWorld runtime benchmark
  served Qwen3.5-9B with vLLM on one H100; local models are not yet benchmarked end to end with `legato.Harness`, and
  other servers (SGLang, LM Studio, Ollama, llama.cpp) are untested. See [models](../models.md) for coordinates and
  token budgets.
- **No tool-call parser?** Pass `native_tools=False` and the model answers in JSON instead.
- **Speed.** With a local model, the wait for the screen and the model call are most of each step. Legato replaces
  fixed sleeps with a wait for the screen to settle and runs up to 3 actions per turn.
