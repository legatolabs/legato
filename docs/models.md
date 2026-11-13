# Which models work with Legato, and how to set them up

**Short answer.** Any model behind an OpenAI-compatible chat API that accepts images. That includes OpenRouter,
Together, Fireworks, and your own vLLM or SGLang server. Legato uses native tool calls when the server supports them.
Otherwise it parses the action JSON from the reply text. Two settings depend on the model: the coordinate convention,
and the token budget for thinking models.

## Measured so far

| model | through | with `legato.Harness` | notes |
|---|---|---|---|
| Claude Sonnet 5.5 | OpenRouter | yes: 48/48 office tasks, 1.48x faster than Anthropic's reference loop | pixel coordinates, 1366x768 screenshots, prompt caching on |
| Qwen3.8-27B | OpenRouter | yes: 74% vs 55% for OSWorld's Qwen agent over two runs, same speed | 0–1000 coordinates; a thinking model |
| Qwen3.5-9B | vLLM, 1 H100 | runtime only (OSWorld's harness on Legato's runtime) | see [benchmarks](benchmarks.md) |
| DeepSeek-V4-Flash-Vision | vLLM, 8 H100 | runtime only | not yet tested with `legato.Harness` |

Other models should work but are untested. Please open an issue with what you find.

## Coordinates

- **`coords="norm1000"`** is the default for non-Claude models. Positions are `[x, y]` on a 0–1000 grid of the
  screenshot, the convention of the Qwen-VL family. Legato maps them onto the real screen.
- **`coords="pixels"`** is the default for Claude. Screenshots go out at Anthropic's recommended size for the screen's
  aspect ratio: 1024x768, 1280x800 or 1366x768. Coordinates come back in that image's pixels.
- **For any other model**, check its convention before a long run. A wrong guess sends every click to the wrong place:
  - Give it one task such as "click the File menu" and look at where the click lands (`result.actions`).
  - Set `coords=` explicitly.
  - Set `model_size=(w, h)` if the model expects a fixed screenshot size.

## Thinking models

Replies get 4,096 output tokens (`max_tokens`).

- **What goes wrong without it.** In one benchmark, a 1,024-token budget cut off 22% of a thinking model's turns
  mid-reasoning, with no action.
- **What Legato does.** If a reply still hits the limit before acting, the next turn says so and asks for a short
  reply.
- **For models that reason at length,** raise `max_tokens`.

## Tool calls and text mode

- `native_tools=True` (the default) sends a `computer` function tool with Claude-style action names.
- `native_tools=False` asks for JSON in the reply instead, for servers without tool-call parsing.
- Both paths accept the common shapes open models send:
  - numeric strings;
  - `coordinate` lists;
  - `"(x, y)"` text;
  - `{x, y}` objects;
  - aliases such as `left_click` and `hotkey`.

## Prompt caching

- **Claude.** Legato adds a top-level `cache_control` that OpenRouter and Anthropic-compatible APIs read.
- **Other providers and vLLM.** Prefix caching is automatic. Legato drops old screenshots 4 at a time past 8
  (`image_max`, `fold`), so the prefix stays identical between drops and the cache keeps hitting.

## Useful settings

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
