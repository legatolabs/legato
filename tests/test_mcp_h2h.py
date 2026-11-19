"""The MCP-H2H benchmark's pieces that run without a screen: the transcript reader, the task site and the checks."""
import json
import sys
import threading
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmarks" / "mcp_h2h"))
import run  # noqa: E402
import tasks  # noqa: E402
import taskweb  # noqa: E402


def entry(kind, t, content, mid=None):
    return json.dumps({"type": kind, "timestamp": f"2026-11-18T10:00:{t:06.3f}Z",
                       "message": {"id": mid, "model": "claude-sonnet-5-5" if kind == "assistant" else None,
                                   "content": content}})


def test_read_transcript(tmp_path):
    use = lambda name: [{"type": "tool_use", "name": name, "input": {}}]  # noqa: E731
    say = lambda text: [{"type": "text", "text": text}]  # noqa: E731
    f = tmp_path / "s.jsonl"
    f.write_text("\n".join([
        entry("user", 1.0, "warm-up"),
        entry("user", 2.0, "Rename it.\n\n" + run.RULES),
        entry("assistant", 4.0, [{"type": "thinking", "thinking": "..."}], "m1"),
        entry("assistant", 4.1, use("mcp__legato__desktop_open"), "m1"),
        entry("assistant", 6.0, say("DONE"), "m2"),                         # too early: it keeps going
        entry("assistant", 7.0, use("mcp__legato__desktop_act"), "m3"),
        entry("assistant", 7.1, use("mcp__legato__desktop_act"), "m3"),     # same reply, second block
        entry("assistant", 9.5, say("Renamed it.\nDONE"), "m4"),
    ]))
    r = run.read_transcript(f)
    assert (r["t1"] - r["t0"], r["turns"], r["verdict"]) == (pytest.approx(7.5), 2, "DONE")
    assert r["tools"] == {"mcp__legato__desktop_open": 1, "mcp__legato__desktop_act": 2}
    assert run.read_transcript(tmp_path / "missing.jsonl")["t0"] is None


def test_transcript_path_matches_claude_codes_folder_names():
    p = run.transcript_path(Path("/Users/me/legato-bench-arms/legato-slow"), "abc")
    assert p.parts[-2:] == ("-Users-me-legato-bench-arms-legato-slow", "abc.jsonl")


def test_arms_differ_only_in_the_computer():
    a, b = run.claude_cmd("legato", "s", "sonnet"), run.claude_cmd("builtin", "s", "sonnet")
    assert a == b and "Bash" in a and "--strict-mcp-config" in a
    assert run.ARMS["legato-slow"]["legato"]["args"][-4:] == ["--fixed-wait", "2", "--image-format", "png"]


def test_site_and_form_checks(tmp_path, monkeypatch):
    monkeypatch.setattr(taskweb, "STATE", tmp_path / "site")
    monkeypatch.setattr(tasks, "ROOT", tmp_path)
    monkeypatch.setattr(taskweb, "PORT", 0)
    srv = taskweb.ThreadingHTTPServer(("127.0.0.1", 0), taskweb.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        assert b"Create account" in urllib.request.urlopen(base + "/signup").read()
        urllib.request.urlopen(base + "/signup", data=b"name=Ada+Lovelace&email=ada%40example.com&plan=pro").read()
        for item in (b"milk", b"eggs", b"bread"):
            urllib.request.urlopen(urllib.request.Request(base + "/todo/items", data=item, method="POST")).read()
    finally:
        srv.shutdown()
    assert tasks.BY_ID["form-signup"].check() and tasks.BY_ID["form-todo"].check()
    tasks.BY_ID["form-todo"].setup()
    assert not tasks.BY_ID["form-todo"].check()


def test_file_task_checks(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks, "ROOT", tmp_path)
    for tid in ("textedit-new", "finder-rename", "textedit-edit"):
        tasks.BY_ID[tid].setup()
        assert not tasks.BY_ID[tid].check(), tid
    (tmp_path / "note.rtf").write_text("{\\rtf1 legato bench}")
    (tmp_path / "inbox" / "report-draft.txt").rename(tmp_path / "inbox" / "report-final.txt")
    (tmp_path / "letter.txt").write_text((tmp_path / "letter.txt").read_text().replace("Bob", "Alice"))
    assert all(tasks.BY_ID[t].check() for t in ("textedit-new", "finder-rename", "textedit-edit"))
