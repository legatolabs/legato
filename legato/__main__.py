"""legato command line.

  legato setup                    choose your model and where it works; adds Legato to your coding agents
  legato run "task"               do one task with that model and desktop, and print where the time went
      [--app NAME | --screen | --sandbox docker|e2b|path/to/file.py:function]   where (default: the saved one; on
                                  a Mac, one app's window in the background, so you keep using the Mac)
      [--model M --base-url URL --api-key-env VAR]   another model for this run
      [--max-steps 40] [--timeout S] [--json] [--yes]
  legato mcp                      an MCP server: a computer for Claude Code, Codex, Cursor (see docs/mcp.md)
  legato doctor [--json]          what is installed and set up, and how to fix what is missing
      [--model [NAME]]            also try the saved model (or NAME): images, tool calls, where its clicks land
  legato init                     write agent.py, a working agent to copy into your own code
  legato                          what to do first

  legato bench office|h2h ...     benchmarks (docs/benchmarks.md)
  legato rl probe|collect|export|profile ...   graded rollouts for RL (docs/rl.md)
  legato app-engine               the engine the Legato Mac app starts (JSON lines on stdin/stdout)
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def _safe_output() -> None:
    """A character the terminal's encoding lacks (an arrow on a Windows pipe in cp1252) is replaced, not a crash."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    _safe_output()
    if not argv:
        from legato.start import welcome
        print(welcome())
        return 0
    if argv[:1] == ["init"]:
        from legato.start import init_main
        return init_main(argv[1:])
    if argv[:1] == ["rl"]:
        from legato.rl.__main__ import main as rl_main
        return rl_main(argv[1:])
    if argv[:1] == ["mcp"]:
        try:
            from legato.mcp_server import main as mcp_main
        except ImportError as e:
            return _fail(str(e))
        return mcp_main(argv[1:])
    if argv[:1] == ["app-engine"]:
        from legato.app_engine import main as engine_main
        return engine_main(argv[1:])
    if argv[:1] == ["doctor"]:
        from legato.doctor import main as doctor_main
        return doctor_main(argv[1:])
    if argv[:1] == ["bench"]:
        which = argv[1] if len(argv) > 1 else ""
        if which == "office":
            from legato.rl.bench.office import main as office_main
            return office_main(argv[2:])
        if which == "h2h":
            from legato.rl.bench.h2h import main as h2h_main
            return h2h_main(argv[2:])
        usage = ("usage: legato bench COMMAND ...\n\n"
                 "  legato bench office generate --out DIR   Legato Office Bench: Excel-to-PowerPoint tasks, as files\n"
                 "                                           and a tasks.json for `legato rl collect` (OSWorld backend)\n"
                 "  legato bench h2h --tasks tasks.json --arm NAME=RUN_DIR ... --ref NAME --out DIR\n"
                 "                                           compare harnesses run on the same tasks\n\n"
                 "`legato bench office --help` and `legato bench h2h --help` list each one's options. Results and "
                 "how they were measured: docs/benchmarks.md (legato.computer/docs/benchmarks).")
        if which in ("", "-h", "--help", "help"):
            print(usage)
            return 0
        print(f"legato bench: no command {which!r}\n{usage}", file=sys.stderr)
        return 2
    if argv[:1] == ["setup"]:
        from legato.setup import main as setup_main
        return setup_main(argv[1:])
    if argv[:1] in (["-h"], ["--help"], ["help"]):
        print(__doc__.strip())
        return 0
    if argv[:1] in (["-V"], ["--version"]):
        from legato import __version__
        print(f"legato {__version__}")
        return 0
    if argv[:1] == ["run"]:
        return run_main(argv[1:])
    print(f"legato: unknown command {argv[0]!r}. Run legato --help", file=sys.stderr)
    return 2


def run_main(argv) -> int:
    from legato import config
    ap = argparse.ArgumentParser(prog="legato run", description="Do one task with your model on your desktop "
                                 "(legato setup chooses both).")
    ap.add_argument("task")
    where = ap.add_mutually_exclusive_group()
    where.add_argument("--app", "--background", dest="app", metavar="APP",
                       help="macOS: work in this app's window in the background (e.g. Safari, Notes), so you keep "
                            "using the Mac")
    where.add_argument("--screen", action="store_true", help="use this whole screen (moves your real mouse)")
    where.add_argument("--sandbox", help="docker (a desktop container), e2b (a cloud desktop), local (= --screen), or "
                                         "your own: path/to/file.py:function or module:function (docs/sandboxes.md)")
    ap.add_argument("--model", help="default: the one legato setup saved")
    ap.add_argument("--base-url", help="OpenAI-compatible root, e.g. https://openrouter.ai/api/v1")
    ap.add_argument("--api-key-env", help="the environment variable holding the key for --base-url")
    ap.add_argument("--max-steps", type=int, default=40)
    ap.add_argument("--timeout", type=float, metavar="S", help="end the run after S seconds (status timeout)")
    ap.add_argument("--coords", default=None, choices=["norm1000", "pixels"],
                    help="default: pixels for Claude models, norm1000 (0-1000 grid) otherwise")
    ap.add_argument("--yes", action="store_true",
                    help="on this computer: do not ask before risky actions (cmd+Delete, a typed rm or sudo...)")
    ap.add_argument("--json", action="store_true", help="print one JSON object (for scripts and agents) instead of text")
    a = ap.parse_args(argv)
    fail = lambda msg: _fail(msg, a.json)                # with --json, the error as JSON too
    cfg = config.load()
    if a.model or a.base_url:
        same = (a.model or cfg.model) == cfg.model and (a.base_url or cfg.base_url) == cfg.base_url
        cfg = config.Config(coords=cfg.coords if same else "", tool_calls=cfg.tool_calls if same else "",
                            model=a.model or cfg.model, base_url=a.base_url or cfg.base_url,
                            api_key_env=a.api_key_env or ("" if a.base_url and a.base_url != cfg.base_url else cfg.api_key_env),
                            api_key="" if a.base_url and a.base_url != cfg.base_url else cfg.api_key,
                            desktop=cfg.desktop, app=cfg.app)
    elif a.api_key_env:
        cfg.api_key, cfg.api_key_env = "", a.api_key_env
    if cfg.missing():
        return fail(cfg.missing())
    desktop = "background" if a.app else "screen" if a.screen else a.sandbox
    if (desktop or cfg.desktop) == "e2b":
        try:
            import e2b_desktop  # noqa: F401
        except ImportError:
            return fail('E2B Desktop is not installed. Run: pip install "legatolabs[e2b]"')
        if not os.environ.get("E2B_API_KEY"):
            return fail("E2B_API_KEY is not set. Get a key at https://e2b.dev and export it, or use --screen.")
    label = (f"--sandbox {a.sandbox}" if a.sandbox else f"{a.app}'s window" if a.app else "this screen" if a.screen
             else "the desktop")
    from legato.agent import agent_for, open_desktop
    import time
    t_start = time.monotonic()                           # --timeout covers opening the desktop too
    try:
        from legato import safety
        # a person at the terminal is asked before a risky action; with nobody to ask (output piped, run by a coding
        # agent or a script) it is refused and said, never run unseen. --yes allows them all.
        # --json is for scripts and agents: nobody reads a question there, so it is treated as nobody to ask
        confirm = (None if a.yes else safety.deny_and_say if a.json
                   else safety.default_confirm(unattended=safety.deny_and_say))
        env, close, what = open_desktop(desktop, task=a.task, app=a.app, confirm=confirm, cfg=cfg,
                                        timeout_s=a.timeout)
    except ImportError as e:
        return fail(f"cannot start {label}: a package is missing ({e}). Run: pip install -U legatolabs")
    except (RuntimeError, OSError, ValueError, AttributeError) as e:     # Docker stopped, no screen permission, ...
        return fail(f"cannot start {label}: {e}")
    say = (lambda *_: None) if a.json else print
    t0 = time.monotonic()
    say(f"{cfg.model}{config.dot()}{what}" + (" (your mouse and keyboard stay yours)" if "background" in what else ""))
    if "background" not in what and (desktop or cfg.desktop or "") in ("", "screen", "local"):
        say("  it uses this computer's mouse and keyboard: Ctrl+C here stops it, and so does moving the mouse into a corner")

    def show(i, acts, t):
        say(f"  {time.monotonic() - t0:5.1f} s  {', '.join(x.describe() if x else 'looking' for x in acts) or 'looking'}")
    try:
        agent = agent_for(env, cfg=cfg, **({"coords": a.coords} if a.coords else {}))
        from legato.agent import ANSWER_NOTE
        # a desktop that fails mid-run comes back as status "error", with the usage so far
        left = None if a.timeout is None else max(0.0, a.timeout - (time.monotonic() - t_start))
        res = agent.run(a.task + ANSWER_NOTE, env, max_steps=a.max_steps, on_step=show, timeout_s=left,
                        raise_desktop_errors=False)
        if res.status == "timeout" and a.timeout is not None:
            res.error = f"the run's time limit ({a.timeout:g} s) ran out"
    except ValueError as e:
        return fail(str(e))
    finally:
        close()
    if a.json:
        print(json.dumps({"status": res.status, "done": res.done, "failed": res.failed, "answer": res.answer,
                          "error": res.error, "stopped": res.stopped, "steps": res.steps, "actions": res.actions,
                          "wall_s": round(res.wall_s, 2), "usage": res.usage, "desktop": what, "model": cfg.model}))
    else:
        if res.answer:
            print(f"\n{res.answer}\n")
        print(res.report())
    return 0 if res.done else 2 if res.status == "error" else 1


def _fail(msg: str, as_json: bool = False) -> int:
    print(f"legato: {msg}", file=sys.stderr)
    if as_json:
        print(json.dumps({"status": "error", "done": False, "failed": True, "answer": "", "error": msg}))
    return 2


if __name__ == "__main__":
    sys.exit(main())
