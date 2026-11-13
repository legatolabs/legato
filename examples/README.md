# Examples

| file | what it shows | needs |
|---|---|---|
| [docker_desktop.py](docker_desktop.py) | a desktop container on this machine, any OpenAI-compatible model | Docker, `OPENROUTER_API_KEY` |
| [local_vllm.py](local_vllm.py) | your own model server (vLLM or SGLang) | a GPU, `E2B_API_KEY` |
| [openrouter_qwen_e2b.py](openrouter_qwen_e2b.py) | an open model through OpenRouter on an E2B desktop | `OPENROUTER_API_KEY`, `E2B_API_KEY` |
| [claude_e2b.py](claude_e2b.py) | Claude on an E2B desktop: pixel coordinates and prompt caching | `OPENROUTER_API_KEY`, `E2B_API_KEY` |
| [custom_sandbox.py](custom_sandbox.py) | any desktop you can screenshot and send input to | an X display |
| [compare_with_reference.py](compare_with_reference.py) | Legato against Anthropic's reference loop on your own task | as above |

For coding agents (Claude Code, Codex, Cursor), see the [MCP server](https://legato.computer/docs/mcp) instead.
