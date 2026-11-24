"""`legato doctor`'s live checks, with fakes: one window capture as background mode takes it (works, stalled, failed)
and whether a model server on this machine answers (a throwaway server on 127.0.0.1 stands in). No real capture is
taken."""
import http.server
import json
import socket
import sys
import threading
import time

import pytest

from legato import config, doctor


def test_a_capture_that_answers_works():
    c = doctor.capture_state(lambda: object(), lambda: pytest.fail("no fallback needed"), wait_s=1.0)
    assert c["ok"] is True and c["state"] == "works" and c["fix"] is None


def test_a_stalled_capture_says_legato_falls_back_to_screencapture():
    def stalls():
        time.sleep(0.5)
        return object()
    c = doctor.capture_state(stalls, lambda: b"png", wait_s=0.05)
    assert c["ok"] is True and c["state"] == "stalled"
    assert "did not answer within 0.05 s" in c["why"] and "screencapture" in c["fix"] and "restart" in c["fix"]


def test_a_capture_that_fails_both_ways_says_how_to_fix_it():
    c = doctor.capture_state(lambda: None, lambda: None, wait_s=1.0)
    assert c["ok"] is False and c["state"] == "failed"
    assert "returned nothing" in c["why"] and "screencapture failed too" in c["why"] and "Screen Recording" in c["fix"]


def test_the_capture_takes_the_saved_apps_window_and_nothing_while_screen_recording_is_off(monkeypatch):
    import legato.background as bg
    wins = [{"id": 1, "app": "Mail", "rect": (0, 0, 800, 600)}, {"id": 2, "app": "Chess", "rect": (5, 5, 300, 200)}]
    took = []
    monkeypatch.setattr(doctor, "_windows", lambda: wins)
    monkeypatch.setattr(doctor, "_api_capture", lambda w: took.append(w["id"]) or object())
    monkeypatch.setattr(bg, "_screencapture", lambda wid, size: pytest.fail("no fallback needed"))
    c = doctor.window_capture("Chess", {})
    assert took == [2] and c["state"] == "works" and c["window"] == "Chess's window"
    doctor.window_capture("", {})
    assert took == [2, 1]                                   # no app saved: the front window
    off = {"screen_recording": {"ok": False, "fix": "turn it on"}}
    c = doctor.window_capture("Chess", off)
    assert c == {"ok": False, "state": "failed", "why": "Screen Recording is off", "fix": "turn it on"}
    assert took == [2, 1]                                   # not tried: it would ask for the permission
    monkeypatch.setattr(doctor, "_windows", lambda: [])
    assert doctor.window_capture("Chess", {})["state"] == "not checked"


def test_doctor_prints_each_capture_state_and_a_failed_one_marks_local_not_ok(monkeypatch, capsys):
    from legato import safety
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(doctor, "_docker", lambda: {"ok": False, "fix": "x"})
    monkeypatch.setattr(doctor, "_has", lambda m: True)
    monkeypatch.setattr(safety, "permissions", lambda: {})
    for state, ok, shown in (("works", True, "ok  window capture: works"),
                             ("stalled", True, "!   window capture: stalled"),
                             ("failed", False, "no  window capture: failed")):
        monkeypatch.setattr(doctor, "window_capture", lambda app, perms: {
            "ok": ok, "state": state, "seconds": 0.1, "why": "why", "fix": "fix", "window": "Chess's window"})
        doctor.main([])
        out = capsys.readouterr().out
        assert shown in out and "Chess's window" in out
        assert ("ok  local" in out) == ok and ("window capture failed" in out) == (not ok)
    assert "window_capture" not in doctor.checks()          # only `legato doctor` itself takes a capture


class _Models(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"data": [{"id": "qwen3.5:9b"}, {"id": "other"}]}).encode()
        self.send_response(200 if self.path == "/v1/models" else 404)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def models_server():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Models)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/v1"
    srv.shutdown()


def _dead_url():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()                                               # nothing listens there now
    return f"http://127.0.0.1:{port}/v1"


def test_model_server_answers_or_not(models_server):
    up = doctor.model_server(models_server, "qwen3.5:9b")
    assert up["ok"] is True and up["models"] == ["qwen3.5:9b", "other"] and "note" not in up
    assert "does not list gpt-x" in doctor.model_server(models_server, "gpt-x")["note"]
    down = doctor.model_server(_dead_url(), "qwen3.5:9b", timeout_s=1.0)
    assert down["ok"] is False and down["error"]


def test_a_dead_local_model_server_is_not_reported_ready(models_server, monkeypatch, capsys):
    monkeypatch.setattr(doctor, "_docker", lambda: {"ok": False, "fix": "x"})
    monkeypatch.setattr(doctor, "window_capture", lambda app, perms: {"ok": None, "state": "not checked", "fix": None})
    cfg = config.Config(provider="local", model="qwen3.5:9b", base_url=_dead_url(), desktop="docker")
    config.save(cfg)
    st = doctor.checks()["setup"]
    assert st["ok"] is False and "no model server answers" in st["fix"] and "ollama serve" in st["fix"]
    doctor.main([])
    assert "no  setup: no model server answers" in capsys.readouterr().out
    cfg.base_url = models_server
    config.save(cfg)
    st = doctor.checks()["setup"]
    assert st["ok"] is True and st["fix"] is None and st["server"]["ok"] is True
    doctor.main([])
    assert "model server answers at " + models_server in capsys.readouterr().out
    cfg.base_url, cfg.api_key = "https://openrouter.ai/api/v1", "k"      # not on this machine: not asked
    config.save(cfg)
    assert doctor.checks()["setup"]["server"] is None


def test_doctor_help_prints_help_and_checks_nothing(monkeypatch, capsys):
    from legato import doctor
    monkeypatch.setattr(doctor, "checks", lambda **k: pytest.fail("--help must not run the checks"))
    assert doctor.main(["--help"]) == 0 and "legato doctor" in capsys.readouterr().out
