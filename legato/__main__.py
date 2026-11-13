"""legato command line.

  legato run "Add a Total row under the table and save" \\
      --model qwen/qwen3-vl-235b-a22b-instruct --base-url https://openrouter.ai/api/v1 [--api-key-env OPENROUTER_API_KEY]
      [--max-steps 30] [--coords norm1000|pixels] [--sandbox e2b]

Runs the open harness on a fresh E2B Desktop sandbox (needs `pip install "legato[e2b]"` and E2B_API_KEY) and prints
where the time went.

  legato bench office generate|report ...   the Legato Office Bench (Excel to PowerPoint tasks)
  legato bench h2h --tasks ... --arm a=DIR --arm b=DIR --ref a --out DIR   compare harness runs
  legato rl probe|collect|export|profile ... graded rollouts for RL (see docs/rl.md)
  legato mcp                                 an MCP server: cloud desktops for any agent (see docs/mcp.md)
"""
from __future__ import annotations

import argparse
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
    r.add_argument("--sandbox", default="e2b", choices=["e2b"])
    a = ap.parse_args(argv)
    from legato import Harness, attach
    try:
        from e2b_desktop import Sandbox
    except ImportError:
        print('E2B Desktop is not installed: pip install "legato[e2b]"', file=sys.stderr)
        return 2
    agent = Harness(model=a.model, base_url=a.base_url, api_key=os.environ.get(a.api_key_env, ""), coords=a.coords)
    desk = Sandbox.create()
    try:
        res = agent.run(a.task, attach(desk), max_steps=a.max_steps,
                        on_step=lambda i, acts, t: print(f"turn {i + 1}: {', '.join(x.describe() for x in acts) or '-'} "
                                                         f"(model {t['model_s']:.1f} s, waiting {t['settle_s']:.1f} s)"))
    finally:
        desk.kill()
    print(res.report())
    return 0 if res.done else 1


if __name__ == "__main__":
    sys.exit(main())
