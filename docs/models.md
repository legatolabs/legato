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

## Will my model work?

```bash
legato doctor --model                          # your saved model
legato doctor --model mistralai/pixtral-large-2502     # another one, at the saved provider (--base-url for elsewhere)
```

It sends what a run sends (the agent's prompt, a test screenshot and the `computer` tool) and reports four things,
each with its fix: the model answers, accepts images, makes tool calls, and where its click lands on the test image
(pixels or the 0–1000 grid). `legato setup` runs the same check and saves what the model needs, so `legato run`,
`legato mcp` and `legato.run()` use it:

- **No tool calls** (the provider says "No endpoints found that support tool use", or a local server has no tool-call
  parser): Legato asks for the actions as JSON in the reply. Saved as `"tool_calls": "json"`. A run does the same by
  itself the first time a server refuses tool calls.
- **Clicks in the other convention:** saved as `"coords"`. Set it by hand with `legato setup --coords pixels`.
- **No images:** refused. Legato needs a vision model.

## On your own GPU

Serve an open vision model with vLLM and point Legato at it. A local server needs no API key, and nothing leaves the
machine.

```bash
vllm serve Qwen/Qwen3.5-9B --enable-auto-tool-choice --tool-call-parser hermes --max-model-len 32768
legato run "On Wikipedia, look up the Eiffel Tower and tell me how tall it is" --model Qwen/Qwen3.5-9B --base-url http://localhost:8000/v1
```

vLLM is the server we test with. Ollama (`http://localhost:11434/v1`) and LM Studio (`http://localhost:1234/v1`) serve
the same API and `legato setup` finds them, but we haven't tested them end to end yet.

## Settings

- **OpenRouter attribution.** Requests to openrouter.ai carry `HTTP-Referer: https://legato.computer` and
  `X-Title: Legato`, so OpenRouter counts them toward Legato on its app rankings. Nothing about you or your task is
  sent. `LEGATO_NO_ATTRIBUTION=1` turns it off.

- **Coordinates.** `coords="norm1000"` (default for non-Claude models) is `[x, y]` on a 0–1000 grid, the Qwen-VL
  convention. `coords="pixels"` (default for Claude) sends screenshots at Anthropic's recommended size (1024x768,
  1280x800 or 1366x768) and reads clicks in that image's pixels. For other models, check where "click the File menu"
  lands (`legato doctor --model NAME` tries it) and set `coords=` (and `model_size=(w, h)` for a fixed screenshot
  size).
- **Thinking models.** Replies get 4,096 output tokens (`max_tokens`). If one hits the limit before acting, the next
  turn asks for a short reply. Raise `max_tokens` for models that reason at length.
- **Tool calls.** `native_tools=True` (default) sends a `computer` function tool with Claude-style action names;
  `native_tools=False` asks for JSON in the reply, for servers without tool-call parsing; Legato switches to it by
  itself when a server refuses tool calls. Both accept common open-model shapes such as `coordinate` lists and
  `"(x, y)"` text.
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
