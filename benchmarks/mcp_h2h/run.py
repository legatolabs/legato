"""MCP-H2H: Claude Code with Legato's MCP against Claude Code's built-in computer use, on this Mac (issue #21).

Every arm runs the same way: an interactive `claude` in tmux (built-in computer use is not available with -p), the
same model, prompt and tool limits, from its own folder under ~/legato-bench-arms/<arm>. Only the computer differs:

    builtin      Claude Code's computer-use server (enable it once there: `claude`, then /mcp → computer-use → Enable)
    legato       legato mcp --sandbox local
    legato-app   the same, with the wait watching only the front app's windows (--wait-scope app)
    legato-slow  legato mcp --sandbox local --fixed-wait 2 --image-format png (the control: no ready-wait, no JPEG)

Time, turns and tool calls come from the session transcript (~/.claude/projects/...), success from each task's check.
The agent's own Bash, file and web tools are off, so every task goes through the screen.

    python benchmarks/mcp_h2h/run.py --smoke legato textedit-new      # one run, to check the setup
    python benchmarks/mcp_h2h/run.py --reps 3                         # the benchmark, arms interleaved
    python benchmarks/mcp_h2h/run.py --summary                        # the table from runs.jsonl
"""
from __future__ import annotations

import argparse
import json
import random
import re
import shlex
import shutil
import statistics
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import taskweb  # noqa: E402
import tasks as T  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
LEGATO = str(REPO / ".venv" / "bin" / "legato") if (REPO / ".venv" / "bin" / "legato").exists() else "legato"
ARMS_DIR = Path.home() / "legato-bench-arms"
OUT = REPO / "benchmarks" / "results" / "mcp-h2h-1"
ARMS = {
    "builtin": {},
    "legato": {"legato": {"command": LEGATO, "args": ["mcp", "--sandbox", "local"]}},
    "legato-app": {"legato": {"command": LEGATO, "args": ["mcp", "--sandbox", "local", "--wait-scope", "app"]}},
    "legato-slow": {"legato": {"command": LEGATO, "args": ["mcp", "--sandbox", "local", "--fixed-wait", "2",
                                                           "--image-format", "png"]}},
}
NO_SHORTCUTS = ["Bash", "Read", "Write", "Edit", "MultiEdit", "Glob", "Grep", "NotebookEdit", "WebFetch", "WebSearch",
                "Agent", "Task", "mcp__legato__desktop_shell", "mcp__legato__desktop_run_task"]
ALLOW = ["mcp__legato__desktop_open", "mcp__legato__desktop_act", "mcp__legato__desktop_screenshot",
         "mcp__legato__desktop_close", "mcp__computer-use"]
RULES = ("Do the task on this Mac's screen with your computer tools; your shell and file tools are off. When it is "
         "done and you have checked it on the screen, reply with DONE on its own line. If you cannot do it, reply FAILED.")
ANSWER = ("Allow for this session", "Do you want to proceed")      # approval prompts the driver says yes to
TIMEOUT_S = 300.0


# ------------------------------------------------------------ transcript
def transcript_path(cwd: Path, session: str) -> Path:
    return Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9-]", "-", str(cwd)) / f"{session}.jsonl"


def _ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def read_transcript(path: Path) -> dict:
    """Start (the task message), end (the reply that says DONE or FAILED), turns, tool calls and the model."""
    out: dict = {"t0": None, "t1": None, "turns": 0, "tools": {}, "model": None, "verdict": None}
    turn_ids: set = set()                     # one assistant reply is split into an entry per block
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        msg = e.get("message") or {}
        content = msg.get("content")
        if e.get("type") == "user" and out["t0"] is None and isinstance(content, str) and RULES[:30] in content:
            out["t0"] = _ts(e["timestamp"])
        if e.get("type") != "assistant" or out["t0"] is None:
            continue
        out["model"] = msg.get("model") or out["model"]
        blocks = content if isinstance(content, list) else []
        uses = [b.get("name") for b in blocks if b.get("type") == "tool_use"]
        if uses:
            out["t1"] = out["verdict"] = None     # it kept going after saying DONE
            turn_ids.add(msg.get("id") or e.get("uuid"))
            out["turns"] = len(turn_ids)
            for u in uses:
                out["tools"][u] = out["tools"].get(u, 0) + 1
        text = " ".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        if not uses and re.search(r"(?m)^\W*(DONE|FAILED)\W*$", text):
            out["t1"], out["verdict"] = _ts(e["timestamp"]), re.search(r"DONE|FAILED", text).group()
    return out


# ------------------------------------------------------------ tmux
def tmux(*args: str) -> str:
    return subprocess.run(["tmux", *args], capture_output=True, text=True).stdout


def pane(name: str) -> str:
    return tmux("capture-pane", "-p", "-t", name)


def arm_dir(arm: str) -> Path:
    d = ARMS_DIR / arm
    d.mkdir(parents=True, exist_ok=True)
    (d / "mcp.json").write_text(json.dumps({"mcpServers": ARMS[arm]}, indent=2))
    return d


def claude_cmd(arm: str, session: str, model: str) -> list[str]:
    return ["claude", "--session-id", session, "--model", model, "--strict-mcp-config", "--mcp-config", "mcp.json",
            "--append-system-prompt", RULES, "--disallowedTools", *NO_SHORTCUTS, "--allowedTools", *ALLOW]


def run_one(arm: str, task: T.Task, model: str, rep: int) -> dict:
    cwd, session, name = arm_dir(arm), str(uuid.uuid4()), f"lb-{uuid.uuid4().hex[:6]}"
    T.tidy_windows()
    task.setup()
    row: dict = {"arm": arm, "task": task.id, "rep": rep, "session": session, "approvals": 0, "error": None}
    tmux("new-session", "-d", "-s", name, "-x", "200", "-y", "50", "-c", str(cwd),
         shlex.join(claude_cmd(arm, session, model)))
    try:
        t = time.monotonic()
        while time.monotonic() - t < 60:                         # wait for the input box
            p = pane(name)
            if "trust this folder" in p:                         # first run in a folder: "No, exit" is the default
                tmux("send-keys", "-t", name, "Down", "Enter")
                time.sleep(2.0)
            elif re.search(r"(?m)^\s*❯", p):                     # the input box (a non-breaking space follows ❯)
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("claude never showed its input box: " + pane(name)[-500:])
        time.sleep(1.0)
        tmux("send-keys", "-t", name, "-l", f"{task.prompt}\n\n{RULES}")
        tmux("send-keys", "-t", name, "Enter")
        path, t, started = transcript_path(cwd, session), time.monotonic(), time.time()
        while time.monotonic() - t < TIMEOUT_S:
            if not path.exists():                                # fall back to the newest transcript in this folder
                newer = [q for q in path.parent.glob("*.jsonl") if q.stat().st_mtime >= started - 5]
                path = max(newer, key=lambda q: q.stat().st_mtime) if newer else path
            p = pane(name)
            if any(a in p for a in ANSWER):                  # an approval prompt: yes, and count it
                row["approvals"] += 1
                tmux("send-keys", "-t", name, "Enter")
                time.sleep(1.0)
            if read_transcript(path)["t1"]:
                break
            time.sleep(0.5)
        else:
            row["error"] = "timeout"
        r = read_transcript(path)
        row.update(seconds=round(r["t1"] - r["t0"], 2) if r["t0"] and r["t1"] else None, turns=r["turns"],
                   tools=r["tools"], model=r["model"], verdict=r["verdict"])
        if r["t0"] is None and not row["error"]:
            row["error"] = "the task message never reached the transcript"
    finally:
        tmux("kill-session", "-t", name)
    time.sleep(1.0)
    row["ok"] = bool(task.check())
    T.tidy_windows()
    return row


# ------------------------------------------------------------ the run and the table
def start_site() -> None:
    srv = taskweb.serve()
    threading.Thread(target=srv.serve_forever, daemon=True).start()


def summary(out: Path) -> str:
    rows = [json.loads(x) for x in (out / "runs.jsonl").read_text().splitlines() if x.strip()]
    arms = list(dict.fromkeys(r["arm"] for r in rows))
    tasks = list(dict.fromkeys(r["task"] for r in rows))

    def mean_s(arm, task):
        xs = [r["seconds"] for r in rows if r["arm"] == arm and r["task"] == task and r["seconds"] is not None]
        return statistics.mean(xs) if xs else None

    lines = ["| arm | success | mean s/task | turns/task | approvals |", "|---|---|---|---|---|"]
    for a in arms:
        rs = [r for r in rows if r["arm"] == a]
        ms = [m for m in (mean_s(a, t) for t in tasks) if m is not None]
        lines.append(f"| {a} | {sum(r['ok'] for r in rs)}/{len(rs)} | {statistics.mean(ms):.1f} | "
                     f"{statistics.mean(r['turns'] for r in rs):.1f} | {sum(r['approvals'] for r in rs)} |")
    if "builtin" in arms:
        rng = random.Random(0)
        for a in (x for x in arms if x != "builtin"):
            pairs = [(mean_s(a, t), mean_s("builtin", t)) for t in tasks]
            pairs = [p for p in pairs if None not in p]
            if not pairs:
                continue
            ratio = sum(p[0] for p in pairs) / sum(p[1] for p in pairs)
            boots = sorted(sum(p[0] for p in s) / sum(p[1] for p in s)
                           for s in ([rng.choice(pairs) for _ in pairs] for _ in range(2000)))
            lines.append(f"\n{a} / builtin time: {ratio:.3f} [{boots[50]:.3f}, {boots[1949]:.3f}] "
                         f"({len(pairs)} tasks, 95% task bootstrap)")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--arms", default="builtin,legato,legato-app,legato-slow")
    ap.add_argument("--tasks", default="all")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--smoke", nargs=2, metavar=("ARM", "TASK"))
    ap.add_argument("--summary", action="store_true")
    a = ap.parse_args(argv)
    if a.summary:
        print(summary(a.out))
        return 0
    if not shutil.which("tmux"):
        sys.exit("needs tmux: brew install tmux")
    start_site()
    if a.smoke:
        print(json.dumps(run_one(a.smoke[0], T.BY_ID[a.smoke[1]], a.model, 0), indent=2))
        return 0
    arms = a.arms.split(",")
    tasks = T.TASKS if a.tasks == "all" else [T.BY_ID[t] for t in a.tasks.split(",")]
    a.out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(21)
    for rep in range(1, a.reps + 1):
        for task in tasks:
            for arm in rng.sample(arms, len(arms)):            # interleaved, in a shuffled order each time
                row = run_one(arm, task, a.model, rep)
                with open(a.out / "runs.jsonl", "a") as f:
                    f.write(json.dumps(row) + "\n")
                print(f"rep {rep} {task.id:14} {arm:12} ok={row['ok']} s={row['seconds']} err={row['error']}",
                      flush=True)
    print(summary(a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
