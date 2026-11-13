"""legato command line.

  legato run "Add a Total row under the table and save" \\
      --model qwen/qwen3-vl-235b-a22b-instruct --base-url https://openrouter.ai/api/v1 [--api-key-env OPENROUTER_API_KEY]
      [--max-steps 30] [--coords norm1000|pixels] [--sandbox e2b|local]

Runs the open harness on a fresh E2B Desktop sandbox (needs `pip install "legatolabs[e2b]"` and E2B_API_KEY), or with
`--sandbox local` on this computer's own screen (`pip install "legatolabs[local]"`), and prints where the time went.
A local model server (`--base-url http://localhost:8000/v1`) needs no key.

  legato bench office generate|report ...   the Legato Office Bench (Excel to PowerPoint tasks)
  legato bench h2h --tasks ... --arm a=DIR --arm b=DIR --ref a --out DIR   compare harness runs
  legato rl probe|collect|export|profile ... graded rollouts for RL (see docs/rl.md)
  legato mcp [--local]                       an MCP server: a desktop for any agent (see docs/mcp.md)
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["rl"]:
        from legato.rl.__main__ import main as rl_main
        return rl_main(argv[1:])
    if argv[:1] == ["mcp"]:
        from legato.mcp_server import main as mcp_main
        return mcp_main(argv[1:])
    if argv[:1] == ["bench"]:
        which = argv[1] if len(argv) > 1 else ""
        if which == "office":
            from legato.rl.bench.office import main as office_main
            return office_main(argv[2:])
        if which == "h2h":
            from legato.rl.bench.h2h import main as h2h_main
            return h2h_main(argv[2:])
        print("usage: legato bench office|h2h ...", file=sys.stderr)
        return 2
    ap = argparse.ArgumentParser(prog="legato", epilog="also: legato bench office|h2h ... (benchmarks), "
                                 "legato rl probe|collect|export|profile ... (RL rollouts), legato mcp (MCP server)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run one task with the open harness on a fresh sandbox")
    r.add_argument("task")
    r.add_argument("--model", required=True)
    r.add_argument("--base-url", required=True, help="OpenAI-compatible root, e.g. https://openrouter.ai/api/v1")
    r.add_argument("--api-key-env", default="OPENROUTER_API_KEY")
    r.add_argument("--max-steps", type=int, default=30)
    r.add_argument("--coords", default=None, choices=["norm1000", "pixels"],
                   help="default: pixels for Claude models, norm1000 (0-1000 grid) otherwise")
    r.add_argument("--sandbox", default="e2b",
                   help="e2b: a fresh cloud desktop (default); local: this computer (moves your real mouse); or your "
                        "own: path/to/file.py:function or module:function returning a legato Env (docs/sandboxes.md)")
    r.add_argument("--json", action="store_true", help="print one JSON object (for scripts and agents) instead of text")
    a = ap.parse_args(argv)
    from legato import Harness
    from legato import providers
    if a.sandbox == "local":
        try:
            from legato.local import local
            env, close = local(), (lambda: None)
        except ImportError:
            return _fail('Driving this computer needs extra packages. Run: pip install "legatolabs[local]"')
        except Exception as e:                    # no display, or no screen-recording permission
            return _fail(f"cannot use this computer's screen: {e}")
    elif a.sandbox == "e2b":
        try:
            import e2b_desktop  # noqa: F401
        except ImportError:
            return _fail('E2B Desktop is not installed. Run: pip install "legatolabs[e2b]"')
        if not os.environ.get("E2B_API_KEY"):
            return _fail("E2B_API_KEY is not set. Get a key at https://e2b.dev and export it, or use --sandbox local.")
        env = close = None
    else:
        try:
            make = providers.load(a.sandbox)
        except (ValueError, ImportError, AttributeError, OSError) as e:
            return _fail(f"cannot load --sandbox {a.sandbox}: {e}")
        env = close = None
    key = os.environ.get(a.api_key_env, "")
    if not key and not any(h in a.base_url for h in ("localhost", "127.0.0.1", "0.0.0.0")):
        return _fail(f"{a.api_key_env} is not set. Export your model provider's key, or pass --api-key-env NAME.")
    agent = Harness(model=a.model, base_url=a.base_url, api_key=key, coords=a.coords)
    say = (lambda *_: None) if a.json else print
    if env is None:
        handle, env = (providers.e2b if a.sandbox == "e2b" else make)(1280, 800)
        close = lambda: providers.close(handle)  # noqa: E731
    try:
        res = agent.run(a.task, env, max_steps=a.max_steps,
                        on_step=lambda i, acts, t: say(f"turn {i + 1}: {', '.join(x.describe() if x else 'screenshot' for x in acts) or '-'}"
                                                       f" (model {t['model_s']:.1f} s, waiting {t['settle_s']:.1f} s)"))
    finally:
        close()
    if a.json:
        print(json.dumps({"done": res.done, "failed": res.failed, "error": res.error, "steps": res.steps,
                          "actions": res.actions, "wall_s": round(res.wall_s, 2), "usage": res.usage}))
    else:
        print(res.report())
    return 0 if res.done else 1


def _fail(msg: str) -> int:
    print(f"legato: {msg}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
