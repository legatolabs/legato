"""Legato Office Bench: multi-app workflows from an Excel workbook to a PowerPoint deck, on a real desktop.

Each task gives the agent two files on the desktop, `sales_<k>.xlsx` (open in LibreOffice Calc) and `deck_<k>.pptx`
(open in LibreOffice Impress), and asks it to move or compute something from the workbook into the deck: copy a
number, use a title, find the top region, list the top three, compute a total, build a table, add a slide, paste a
chart. The inputs are generated from a seed, so every task has one right answer, and the grader opens the saved deck
with python-pptx and checks it. The reward is the share of checks passed; success means all of them.

    python -m legato.rl.bench.office generate --out ~/bench          # files + tasks.json for legato.rl collect
    python -m legato.rl collect --tasks ~/bench/tasks.json ...        # run it (OSWorld backend)

The files are real .xlsx and .pptx; the apps are LibreOffice Calc and Impress on OSWorld's Ubuntu desktop.
"""
from __future__ import annotations

import argparse
import io
import json
import random
import re
from pathlib import Path

REGIONS = ["North", "South", "East", "West", "Central", "Pacific", "Mountain", "Atlantic", "Midwest", "Coastal"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun"]
COMPANIES = ["Northwind", "Contoso", "Fabrikam", "Tailspin", "Wingtip", "Litware"]
DESK = "/home/user/Desktop"

# kind, difficulty, instruction (format fields: x, p, extra)
KINDS = {
    "copy_total": ("easy", "In {x}, cell B2 of the Summary sheet holds the total revenue. Replace the subtitle text on "
                            "slide 1 of {p} with that number, then save {p}."),
    "copy_title": ("easy", "Use the text in cell A1 of the Summary sheet of {x} as the title of slide 1 of {p}, then "
                            "save {p}."),
    "top_region": ("medium", "Find the region with the highest total revenue in the Data sheet of {x} (add up its "
                              "months). Make that region's name the title of slide 2 of {p}, then save {p}."),
    "top3_list": ("medium", "In the Data sheet of {x}, find the three regions with the highest total revenue. Replace "
                             "the body text of slide 2 of {p} with their names, highest first, one per line, then save "
                             "{p}."),
    "compute_total": ("medium", "Add up the Revenue column of the Data sheet of {x}. Replace the body text of slide 2 "
                                 "of {p} with 'Total: ' followed by that number, then save {p}."),
    "region_table": ("hard", "On slide 2 of {p}, add a table with two columns, Region and Total, with one row per "
                              "region from the Data sheet of {x} and each region's total revenue. Save {p}."),
    "next_steps": ("hard", "Add a new slide at the end of {p} titled 'Next Steps' whose body text says 'Grow ' "
                            "followed by the name of the region with the lowest total revenue in {x}. Save {p}."),
    "paste_chart": ("hard", "In {x}, make a chart of revenue by month for the {extra} region and put it on slide 3 "
                             "of {p}. Save {p}."),
}
PLAN = ["copy_total", "copy_total", "copy_title", "copy_title", "top_region", "top_region", "top3_list",
        "compute_total", "compute_total", "region_table", "next_steps", "paste_chart"]


# ---------------------------------------------------------------- inputs

def _data(rng: random.Random) -> dict:
    regions = rng.sample(REGIONS, 5)
    rows = [(r, m, rng.randrange(1000, 20000)) for r in regions for m in MONTHS]
    totals = {r: sum(v for rr, _, v in rows if rr == r) for r in regions}
    if len(set(totals.values())) < len(totals):            # unique totals: one right answer per question
        return _data(rng)
    return {"regions": regions, "rows": rows, "totals": totals, "total": sum(totals.values()),
            "company": rng.choice(COMPANIES), "quarter": f"Q{rng.randrange(1, 5)} {rng.randrange(2025, 2028)}"}


def write_xlsx(d: dict, path: Path, summary_total: bool) -> None:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    ws.append(["Region", "Month", "Revenue"])
    for r in d["rows"]:
        ws.append(list(r))
    s = wb.create_sheet("Summary")
    s["A1"] = f"{d['company']} {d['quarter']} Revenue Review"
    if summary_total:
        s["A2"], s["B2"] = "Total revenue", d["total"]
    wb.save(path)


def write_pptx(path: Path) -> None:
    from pptx import Presentation
    p = Presentation()
    s1 = p.slides.add_slide(p.slide_layouts[0])
    s1.shapes.title.text = "Quarterly Review"
    s1.placeholders[1].text = "TBD"
    s2 = p.slides.add_slide(p.slide_layouts[1])
    s2.shapes.title.text = "Highlights"
    s2.placeholders[1].text = "TBD"
    s3 = p.slides.add_slide(p.slide_layouts[5])
    s3.shapes.title.text = "Appendix"
    p.save(path)


def generate(out: str | Path, seed: int = 20261112, repeat: int = 1) -> list[dict]:
    """Write the input files and tasks.json (legato.rl task list, inline OSWorld configs) under `out`. `repeat` > 1
    adds further rounds of the same task kinds with fresh data (12 tasks per round)."""
    out = Path(out).expanduser().resolve()
    files = out / "files"
    files.mkdir(parents=True, exist_ok=True)
    tasks = []
    for k, kind in enumerate(PLAN * max(1, repeat)):
        rng = random.Random(seed * 100 + k)
        d = _data(rng)
        x, p = f"sales_{k}.xlsx", f"deck_{k}.pptx"
        write_xlsx(d, files / x, summary_total=(kind == "copy_total"))
        write_pptx(files / p)
        extra = rng.choice(d["regions"]) if kind == "paste_chart" else ""
        diff, text = KINDS[kind]
        instruction = text.format(x=f"~/Desktop/{x} (open in LibreOffice Calc)",
                                  p=f"~/Desktop/{p} (open in LibreOffice Impress)", extra=extra)
        instruction = instruction.replace(f"save ~/Desktop/{p} (open in LibreOffice Impress)", f"save {p}")
        instruction = instruction.replace(f"Save ~/Desktop/{p} (open in LibreOffice Impress)", f"Save {p}")
        tid = f"office-{k:02d}-{kind}"
        cfg = {
            "id": tid, "instruction": instruction, "snapshot": "libreoffice_impress",
            "config": [
                {"type": "upload_file", "parameters": {"files": [
                    {"local_path": str(files / x), "path": f"{DESK}/{x}"},
                    {"local_path": str(files / p), "path": f"{DESK}/{p}"}]}},
                {"type": "open", "parameters": {"path": f"{DESK}/{x}"}},
                {"type": "open", "parameters": {"path": f"{DESK}/{p}"}},
            ],
            "evaluator": {   # OSWorld needs a parseable evaluator at reset; legato_grader does the grading
                "postconfig": [
                    {"type": "activate_window", "parameters": {"window_name": f"{p} - LibreOffice Impress", "strict": False}},
                    {"type": "sleep", "parameters": {"seconds": 0.5}},
                    {"type": "execute", "parameters": {"command": ["python", "-c",
                        "import pyautogui, time; pyautogui.hotkey('ctrl', 's'); time.sleep(1.0)"]}},
                    {"type": "sleep", "parameters": {"seconds": 1.0}}],
                "func": "compare_pptx_files",
                "result": {"type": "vm_file", "path": f"{DESK}/{p}", "dest": p},
            },
            "legato_grader": {"kind": kind, "files": {"deck": f"{DESK}/{p}"}, "checks": checks_for(kind, d, extra)},
        }
        tasks.append({"task_id": tid, "domain": "office", "difficulty": diff, "config": cfg})
    (out / "tasks.json").write_text(json.dumps(tasks, indent=1))
    return tasks


def checks_for(kind: str, d: dict, extra: str) -> list[dict]:
    t = d["totals"]
    ranked = sorted(t, key=t.get, reverse=True)
    return {
        "copy_total": [{"slide": 1, "number": d["total"]}],
        "copy_title": [{"slide": 1, "title": f"{d['company']} {d['quarter']} Revenue Review"}],
        "top_region": [{"slide": 2, "title": ranked[0]}],
        "top3_list": [{"slide": 2, "lines_in_order": ranked[:3]}],
        "compute_total": [{"slide": 2, "text": "Total"}, {"slide": 2, "number": d["total"]}],
        "region_table": [{"slide": 2, "table_rows": [[r, t[r]] for r in d["regions"]]}],
        "next_steps": [{"slide": "last", "title": "Next Steps"}, {"slide": "last", "text": ranked[-1]},
                       {"slide_count": 4}],
        "paste_chart": [{"slide": 3, "chart_or_picture": True}],
    }[kind]


# ---------------------------------------------------------------- grading

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).casefold()


def _numbers(s: str) -> list[float]:
    out = []
    for m in re.finditer(r"-?\d[\d,  .]*", s or ""):
        raw = m.group(0).strip().replace(",", "").replace(" ", "").replace(" ", "")
        try:
            out.append(float(raw.rstrip(".")))
        except ValueError:
            pass
    return out


def _slide_parts(slide) -> dict:
    from pptx.enum.shapes import MSO_SHAPE_TYPE
    title = ""
    if slide.shapes.title is not None:
        title = slide.shapes.title.text_frame.text if slide.shapes.title.has_text_frame else ""
    body, tables, visual = [], [], False
    for sh in slide.shapes:
        if slide.shapes.title is not None and sh.shape_id == slide.shapes.title.shape_id:
            continue
        if sh.has_text_frame:
            body.append(sh.text_frame.text)
        if getattr(sh, "has_table", False) and sh.has_table:
            tables.append([[c.text for c in row.cells] for row in sh.table.rows])
        if getattr(sh, "has_chart", False) and sh.has_chart:
            visual = True
        if sh.shape_type in (MSO_SHAPE_TYPE.PICTURE, MSO_SHAPE_TYPE.EMBEDDED_OLE_OBJECT, MSO_SHAPE_TYPE.CHART,
                             MSO_SHAPE_TYPE.LINKED_PICTURE):
            visual = True
    return {"title": title, "body": "\n".join(body), "tables": tables, "visual": visual}


def grade(spec: dict, files: dict) -> tuple[float, dict]:
    """Score the saved deck against the task's checks. Returns (share of checks passed, details)."""
    from pptx import Presentation
    raw = files.get("deck")
    if not raw:
        return 0.0, {"error": "deck not found on the desktop"}
    try:
        prs = Presentation(io.BytesIO(raw))
    except Exception as e:  # noqa: BLE001
        return 0.0, {"error": f"deck unreadable: {type(e).__name__}: {e}"[:200]}
    slides = list(prs.slides)
    results = []
    for c in spec["checks"]:
        ok, why = False, ""
        if "slide_count" in c:
            ok, why = len(slides) == c["slide_count"], f"{len(slides)} slides"
            results.append({"check": c, "ok": ok, "seen": why})
            continue
        idx = len(slides) - 1 if c["slide"] == "last" else c["slide"] - 1
        if not 0 <= idx < len(slides):
            results.append({"check": c, "ok": False, "seen": f"no slide {c['slide']}"})
            continue
        part = _slide_parts(slides[idx])
        everything = part["title"] + "\n" + part["body"] + "\n" + "\n".join(" ".join(r) for t in part["tables"] for r in t)
        if "title" in c:
            ok, why = _norm(part["title"]) == _norm(c["title"]), part["title"][:120]
        elif "number" in c:
            ok, why = any(abs(n - c["number"]) < 0.5 for n in _numbers(everything)), everything[:120]
        elif "text" in c:
            ok, why = _norm(c["text"]) in _norm(everything), everything[:120]
        elif "lines_in_order" in c:
            lines = [_norm(x) for x in part["body"].splitlines() if x.strip()]
            want = [_norm(x) for x in c["lines_in_order"]]
            ok, why = lines[:len(want)] == want or [ln for ln in lines if ln in want] == want, " | ".join(lines)[:120]
        elif "table_rows" in c:
            rows = [r for t in part["tables"] for r in t]
            def has(region, total):
                return any(_norm(region) in _norm(" ".join(r)) and any(abs(n - total) < 0.5 for n in _numbers(" ".join(r)))
                           for r in rows)
            ok = bool(rows) and all(has(r, v) for r, v in c["table_rows"])
            why = f"{len(rows)} table rows"
        elif "chart_or_picture" in c:
            ok, why = part["visual"], "chart/picture present" if part["visual"] else "none"
        results.append({"check": c, "ok": ok, "seen": why})
    score = sum(r["ok"] for r in results) / len(results) if results else 0.0
    return score, {"checks": results, "slides": len(slides)}


# ---------------------------------------------------------------- comparing two harnesses

def compare(base_dir: str | Path, legato_dir: str | Path, tasks: list[dict]) -> dict:
    """Per task and overall: success and time for a baseline run and a Legato run of the same tasks."""
    import statistics as st
    from legato.rl.trajectory import read_rollouts
    diff = {t["task_id"]: t.get("difficulty", "") for t in tasks}
    runs = {"baseline": read_rollouts(base_dir), "legato": read_rollouts(legato_dir)}
    done = {k: [r for r in v if r.get("status") in ("ok", "truncated")] for k, v in runs.items()}
    per = {}
    for tid in diff:
        row = {"difficulty": diff[tid]}
        for k, rs in done.items():
            xs = [r for r in rs if r["task_id"] == tid]
            row[k] = {"n": len(xs), "passed": sum((r.get("reward") or 0) >= 1 for r in xs),
                      "score": st.mean(r.get("reward") or 0 for r in xs) if xs else None,
                      "mean_s": st.mean(r["timing"]["total_s"] for r in xs) if xs else None}
        per[tid] = row
    both = [t for t, r in per.items() if r["baseline"]["n"] and r["legato"]["n"]]
    ratio = (sum(per[t]["legato"]["mean_s"] for t in both) / sum(per[t]["baseline"]["mean_s"] for t in both)) if both else None
    overall = {k: {"rollouts": len(rs), "passed": sum((r.get("reward") or 0) >= 1 for r in rs),
                   "success_rate": (sum((r.get("reward") or 0) >= 1 for r in rs) / len(rs)) if rs else None,
                   "mean_score": st.mean(r.get("reward") or 0 for r in rs) if rs else None,
                   "mean_s": st.mean(r["timing"]["total_s"] for r in rs) if rs else None,
                   "median_s": st.median(r["timing"]["total_s"] for r in rs) if rs else None,
                   "errors": sum(1 for r in runs[k] if r.get("status") == "error")} for k, rs in done.items()}
    return {"overall": overall, "time_ratio_legato_over_baseline": ratio, "tasks_compared": len(both), "per_task": per}


def report_md(res: dict, meta: dict) -> str:
    o, f = res["overall"], (lambda v, n=1: "-" if v is None else f"{v:.{n}f}")
    L = [f"# Legato Office Bench: {meta.get('model', '')}", "",
         "Excel-to-PowerPoint workflows on a real Ubuntu desktop (LibreOffice Calc and Impress, .xlsx and .pptx files), "
         f"{meta.get('attempts', '?')} attempts per task per harness, same model and desktop image for both.", "",
         f"- **Baseline**: {meta.get('baseline', '')}", f"- **Legato**: {meta.get('legato', '')}", "",
         "| harness | rollouts | passed | success | mean score | mean s | median s | errors |", "|---|---|---|---|---|---|---|---|"]
    for k in ("baseline", "legato"):
        x = o[k]
        L.append(f"| {k} | {x['rollouts']} | {x['passed']} | {f(100 * x['success_rate'], 0) if x['success_rate'] is not None else '-'}% | "
                 f"{f(x['mean_score'], 2)} | {f(x['mean_s'])} | {f(x['median_s'])} | {x['errors']} |")
    r = res["time_ratio_legato_over_baseline"]
    L += ["", f"Time ratio, Legato over baseline (sum of per-task means, {res['tasks_compared']} tasks): **{f(r, 2)}**"
          + (f" ({1 / r:.1f}x faster)" if r else ""), "",
          "| task | difficulty | baseline passed | baseline mean s | legato passed | legato mean s |", "|---|---|---|---|---|---|"]
    for tid, row in res["per_task"].items():
        b, g = row["baseline"], row["legato"]
        L.append(f"| {tid} | {row['difficulty']} | {b['passed']}/{b['n']} | {f(b['mean_s'])} | {g['passed']}/{g['n']} | {f(g['mean_s'])} |")
    return "\n".join(L) + "\n"


def demo(base_dir: str | Path, legato_dir: str | Path, tasks: list[dict], out: str | Path, speed: float = 4.0,
         task_id: str | None = None) -> dict:
    """Render the side-by-side replay for one task: by default the one where both harnesses passed with the largest
    time gap (else where Legato passed, else any task both ran)."""
    from legato.rl.bench.replay import pick, render, timeline
    base_dir, legato_dir = Path(base_dir), Path(legato_dir)
    by_id = {t["task_id"]: t for t in tasks}
    cands = []
    for tid in ([task_id] if task_id else by_id):
        b, g = pick(base_dir, tid), pick(legato_dir, tid)
        if b and g:
            both_ok = (b.get("reward") or 0) >= 1 and (g.get("reward") or 0) >= 1
            cands.append((2 if both_ok else 1 if (g.get("reward") or 0) >= 1 else 0,
                          b["timing"]["total_s"] - g["timing"]["total_s"], tid, b, g))
    if not cands:
        return {"error": "no task with a completed rollout in both runs"}
    cands.sort(key=lambda c: (c[0], c[1]), reverse=True)
    _, _, tid, b, g = cands[0]
    info = render(timeline(b, base_dir), timeline(g, legato_dir), out,
                  titles=("Standard harness", "Legato"),
                  subtitles=("fixed 2 s wait after every action · full reset · fixed 60 s / 20 s waits",
                             "waits until the screen is ready · fast reset"),
                  task_text=by_id[tid]["config"]["instruction"], speed=speed)
    info.update(task_id=tid, baseline_reward=b.get("reward"), legato_reward=g.get("reward"),
                baseline_rollout=b["rollout_id"], legato_rollout=g["rollout_id"])
    return info


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m legato.rl.bench.office")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--out", required=True)
    g.add_argument("--seed", type=int, default=20261112)
    g.add_argument("--repeat", type=int, default=1, help="rounds of the 12 task kinds (fresh data each round)")
    r = sub.add_parser("report", help="compare a baseline run and a Legato run; optionally render the demo video")
    r.add_argument("--tasks", required=True)
    r.add_argument("--baseline", required=True)
    r.add_argument("--legato", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--model", default="")
    r.add_argument("--video", action="store_true")
    r.add_argument("--speed", type=float, default=4.0)
    a = ap.parse_args(argv)
    if a.cmd == "generate":
        tasks = generate(a.out, a.seed, a.repeat)
        print(json.dumps({"tasks": len(tasks), "out": str(Path(a.out).expanduser()),
                          "kinds": sorted({t["config"]["legato_grader"]["kind"] for t in tasks})}))
        return 0
    tasks = json.loads(Path(a.tasks).read_text())
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    res = compare(a.baseline, a.legato, tasks)
    meta = {"model": a.model, "attempts": max((x["n"] for row in res["per_task"].values() for x in (row["baseline"], row["legato"])), default=0),
            "baseline": "OSWorld's runner with a fixed 2 s wait after every action (the delay in Anthropic's reference "
                        "computer-use harness), a full snapshot reset, and OSWorld's fixed 60 s / 20 s waits",
            "legato": "the same runner with Legato's ready signal (wait until the screen is ready) and fast reset"}
    if a.video:
        res["demo"] = demo(a.baseline, a.legato, tasks, out / "demo.mp4", speed=a.speed)
    (out / "results.json").write_text(json.dumps(res, indent=1))
    md = report_md(res, meta)
    if res.get("demo") and "error" not in res["demo"]:
        d = res["demo"]
        md += (f"\nDemo video `demo.mp4`: task {d['task_id']}, baseline {d['left_s']} s (reward {d['baseline_reward']}), "
               f"Legato {d['right_s']} s (reward {d['legato_reward']}), played at {d['speed']:g}x.\n")
    (out / "RESULTS.md").write_text(md)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
