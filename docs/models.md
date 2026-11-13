# Which models work with Legato, and how to set them up

Any model behind an OpenAI-compatible chat API that accepts images: OpenRouter, Together, Fireworks, or your own vLLM
or SGLang server, including one on the same machine. Two settings depend on the model: coordinates and, for thinking models, the token budget.

| model | through | with `legato.Harness` | notes |
|---|---|---|---|
| Claude Sonnet 5.5 | OpenRouter | yes: 48/48 office tasks, 1.48x faster than Anthropic's reference loop | pixel coordinates, 1366x768 screenshots, prompt caching on |
| Qwen3.8-27B | OpenRouter | yes: 71/96 vs 53/96 for OSWorld's Qwen agent over two runs, same speed | 0–1000 coordinates; a thinking model |
| Qwen3.5-9B | vLLM, 1 H100 | runtime only (OSWorld's harness on Legato's runtime) | see [benchmarks](benchmarks.md) |
| DeepSeek-V4-Flash-Vision | vLLM, 8 H100 | runtime only | not yet tested with `legato.Harness` |

Other models are untested; please open an issue with what you find.

## On your own GPU

Serve an open vision model with vLLM (or SGLang) and point Legato at it. A local server needs no API key.

```bash
vllm serve Qwen/Qwen3.5-9B --port 8000
legato run "Rename the screenshots on the desktop by date" --sandbox local \
    --model Qwen/Qwen3.5-9B --base-url http://localhost:8000/v1
```

## Settings

- **Coordinates.** `coords="norm1000"` (default for non-Claude models) is `[x, y]` on a 0–1000 grid, the Qwen-VL
  convention. `coords="pixels"` (default for Claude) sends screenshots at Anthropic's recommended size (1024x768,
  1280x800 or 1366x768) and reads clicks in that image's pixels. For other models, check where "click the File menu"
  lands (`result.actions`) and set `coords=` (and `model_size=(w, h)` for a fixed screenshot size).
- **Thinking models.** Replies get 4,096 output tokens (`max_tokens`). If one hits the limit before acting, the next
  turn asks for a short reply. Raise `max_tokens` for models that reason at length.
- **Tool calls.** `native_tools=True` (default) sends a `computer` function tool with Claude-style action names;
  `native_tools=False` asks for JSON in the reply, for servers without tool-call parsing. Both accept common
  open-model shapes such as `coordinate` lists and `"(x, y)"` text.
- **Prompt caching.** Claude gets a top-level `cache_control`, read by OpenRouter and Anthropic-compatible APIs.
  Elsewhere, dropping screenshots 4 at a time keeps the automatic prefix cache hitting.

```python
legato.Harness(model, base_url, api_key,
               coords=None,          # "norm1000" | "pixels"; default by model
               image_max=8, fold=4,  # screenshots kept, dropped in chunks
               max_batch=3,          # actions per turn, one screenshot after them
               step_timeout_s=60.0, retries=1,
               max_tokens=4096, temperature=0.0,
               native_tools=True,
               model_size="auto",    # screenshot size sent to the model
               extra_body=None)      # merged into every request (e.g. provider routing)
```
