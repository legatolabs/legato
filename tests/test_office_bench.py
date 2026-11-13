"""legato.rl.bench.office: generated tasks are well-formed, the grader passes a solved deck and fails an untouched
one, and the side-by-side replay renders from real rollout files (fake desktop, stub policy)."""
from __future__ import annotations

import io
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
pptx = pytest.importorskip("pptx")
pytest.importorskip("openpyxl")

from legato.rl import DesktopConfig, HarnessConfig, PolicyConfig, collect  # noqa: E402
from legato.rl.bench import office  # noqa: E402


@pytest.fixture(scope="module")
def bench(tmp_path_factory):
    out = tmp_path_factory.mktemp("bench")
    return out, office.generate(out)


def solve(deck_bytes: bytes, spec: dict) -> bytes:
    """Apply the right answer to an untouched deck, the way a correct agent would."""
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.util import Inches
    p = Presentation(io.BytesIO(deck_bytes))
    s = list(p.slides)
    c0 = spec["checks"][0]
    k = spec["kind"]
    if k == "copy_total":
        s[0].placeholders[1].text = f"{c0['number']:,}"
    elif k == "copy_title":
        s[0].shapes.title.text = c0["title"]
    elif k == "top_region":
        s[1].shapes.title.text = c0["title"]
    elif k == "top3_list":
        s[1].placeholders[1].text = "\n".join(c0["lines_in_order"])
    elif k == "compute_total":
        s[1].placeholders[1].text = f"Total: {spec['checks'][1]['number']}"
    elif k == "region_table":
        rows = c0["table_rows"]
        t = s[1].shapes.add_table(len(rows) + 1, 2, Inches(1), Inches(1.5), Inches(6), Inches(3)).table
        t.cell(0, 0).text, t.cell(0, 1).text = "Region", "Total"
        for i, (r, v) in enumerate(rows, 1):
            t.cell(i, 0).text, t.cell(i, 1).text = r, f"{v:,}"
    elif k == "next_steps":
        ns = p.slides.add_slide(p.slide_layouts[1])
        ns.shapes.title.text = "Next Steps"
        ns.placeholders[1].text = f"Grow {spec['checks'][1]['text']}"
    elif k == "paste_chart":
        cd = CategoryChartData()
        cd.categories = office.MONTHS
        cd.add_series("Revenue", [1, 2, 3, 4, 5, 6])
        s[2].shapes.add_chart(XL_CHART_TYPE.LINE, Inches(1), Inches(1.5), Inches(6), Inches(4), cd)
    buf = io.BytesIO()
    p.save(buf)
    return buf.getvalue()


def test_generated_tasks_are_wellformed(bench):
    out, tasks = bench
    assert len(tasks) == 12 and len({t["task_id"] for t in tasks}) == 12
    assert {t["config"]["legato_grader"]["kind"] for t in tasks} == set(office.KINDS)
    for t in tasks:
        cfg = t["config"]
        assert t["domain"] == "office" and cfg["id"] == t["task_id"] and "~/Desktop/" in cfg["instruction"]
        for f in cfg["config"][0]["parameters"]["files"]:
            assert Path(f["local_path"]).exists() and f["path"].startswith("/home/user/Desktop/")
    assert json.loads((out / "tasks.json").read_text()) == tasks


@pytest.mark.parametrize("k", range(12))
def test_grader_passes_solved_and_fails_untouched(bench, k):
    out, tasks = bench
    spec = tasks[k]["config"]["legato_grader"]
    deck = (out / "files" / Path(spec["files"]["deck"]).name).read_bytes()
    score, details = office.grade(spec, {"deck": solve(deck, spec)})
    assert score == 1.0, details
    untouched, _ = office.grade(spec, {"deck": deck})
    assert untouched < 1.0
    assert office.grade(spec, {"deck": None})[0] == 0.0


def test_number_matching_accepts_common_formats():
    for s in ("48213", "48,213", "Total: 48 213", "48213.00", "$48,213"):
        assert any(abs(n - 48213) < 0.5 for n in office._numbers(s)), s


def test_demo_video_renders(tmp_path):
    pytest.importorskip("imageio_ffmpeg")

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            reply = "DONE" if len(body["messages"]) >= 6 else "click"
            out = json.dumps({"choices": [{"message": {"content": reply}, "logprobs": {"content": [{"token": reply, "logprob": -0.1}]}}],
                              "usage": {"prompt_tokens": 10, "completion_tokens": 1}}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/v1"
    tasks = [{"task_id": "office-00-copy_total", "domain": "office", "difficulty": "easy", "reward": 1.0,
              "config": {"instruction": "Copy the total into the deck."}}]
    for arm, act_s in (("baseline", 0.3), ("legato", 0.05)):
        collect(tasks, PolicyConfig(url, "m", version=arm), tmp_path / arm, desktops=1, group=1,
                desktop=DesktopConfig(backend="fake", fake={"act_s": act_s, "reset_s": act_s}),
                harness=HarnessConfig(name="fake"), start_stagger_s=0.0)
    srv.shutdown()
    res = office.compare(tmp_path / "baseline", tmp_path / "legato", tasks)
    assert res["overall"]["legato"]["passed"] == 1 and res["time_ratio_legato_over_baseline"] < 1
    info = office.demo(tmp_path / "baseline", tmp_path / "legato", tasks, tmp_path / "demo.mp4", speed=1.0)
    assert info["task_id"] == "office-00-copy_total" and (tmp_path / "demo.mp4").stat().st_size > 1000
    md = office.report_md(res, {"model": "m", "attempts": 1, "baseline": "b", "legato": "l"})
    assert "Time ratio" in md and "office-00-copy_total" in md
