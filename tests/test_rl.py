"""legato.rl without VMs: a stub OpenAI-compatible policy server and the fake desktop."""
from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from legato.rl import (DesktopConfig, HarnessConfig, PolicyClient, PolicyConfig, PolicyError,  # noqa: E402
                       collect, export_steps, group_advantages, load_request, plan_jobs, read_rollouts)


class Stub:
    """Behaviour knobs, set per test: delay_s, fail_first (HTTP 500 count), reject_token_ids, reply."""

    def __init__(self):
        self.delay_s, self.fail_first, self.reject_token_ids, self.reply = 0.0, 0, False, "click it"
        self.lock = threading.Lock()
        self.inflight = self.max_inflight = self.calls = 0
        self.bodies = []


@pytest.fixture()
def server():
    stub = Stub()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            with stub.lock:
                stub.calls += 1
                n = stub.calls
                stub.inflight += 1
                stub.max_inflight = max(stub.max_inflight, stub.inflight)
                stub.bodies.append(body)
            try:
                time.sleep(stub.delay_s)
                if n <= stub.fail_first:
                    return self._send(500, {"error": "boom"})
                if stub.reject_token_ids and body.get("return_token_ids"):
                    return self._send(400, {"error": "unknown field return_token_ids"})
                reply = stub.reply(body) if callable(stub.reply) else stub.reply
                toks = reply.split()
                out = {"choices": [{"message": {"content": reply, "reasoning_content": "think"},
                                    "finish_reason": "stop",
                                    "logprobs": {"content": [{"token": t, "logprob": -0.5} for t in toks]},
                                    **({"token_ids": list(range(len(toks)))} if body.get("return_token_ids") else {})}],
                       "usage": {"prompt_tokens": 100, "completion_tokens": len(toks),
                                 "prompt_tokens_details": {"cached_tokens": 80}}}
                return self._send(200, out)
            finally:
                with stub.lock:
                    stub.inflight -= 1

        def _send(self, code, obj):
            b = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    stub.url = f"http://127.0.0.1:{srv.server_address[1]}/v1"
    yield stub
    srv.shutdown()


def fake(**kw):
    return dict(desktop=DesktopConfig(backend="fake", fake=kw), harness=HarnessConfig(name="fake"), start_stagger_s=0.0)


def test_policy_records_logprobs_and_token_ids(server):
    rec = PolicyClient(PolicyConfig(server.url, "m", version="v7")).chat([{"role": "user", "content": "hi"}])
    assert rec["content"] == "click it" and rec["reasoning"] == "think"
    assert [x["lp"] for x in rec["logprobs"]] == [-0.5, -0.5] and rec["token_ids"] == [0, 1]
    assert rec["usage"] == {"prompt": 100, "completion": 2, "cached": 80, "cost": None} and rec["version"] == "v7"
    assert server.bodies[-1]["logprobs"] is True


def test_policy_retries_then_gives_up(server):
    server.fail_first = 1
    rec = PolicyClient(PolicyConfig(server.url, "m", retries=1)).chat([{"role": "user", "content": "x"}])
    assert rec["attempts"] == 2
    server.delay_s = 1.0
    t = time.perf_counter()
    with pytest.raises(PolicyError, match="timeout"):
        PolicyClient(PolicyConfig(server.url, "m", timeout_s=0.3, retries=1)).chat([{"role": "user", "content": "x"}])
    assert time.perf_counter() - t < 3.0          # bounded: 2 x 0.3 s + backoff, not minutes


def test_policy_drops_token_ids_when_refused(server):
    server.reject_token_ids = True
    c = PolicyClient(PolicyConfig(server.url, "m"))
    rec = c.chat([{"role": "user", "content": "x"}])
    assert rec["token_ids"] is None and rec["logprobs"] and c.cfg.token_ids is False


def test_plan_spreads_attempts_across_desktops():
    jobs = plan_jobs([{"task_id": "a"}, {"task_id": "b"}], group=2, oversample=0.5, version="v1")
    assert len(jobs) == 6 and [j["task_id"] for j in jobs[:2]] == ["a", "b"]
    assert {j["group_id"] for j in jobs} == {"a@v1", "b@v1"}


def test_collect_export_roundtrip(server, tmp_path):
    server.reply = lambda body: "DONE" if len(body["messages"]) >= 4 else "click"   # 2 steps per rollout
    tasks = [{"task_id": f"t{i}", "reward": [1.0, 0.0]} for i in range(3)]
    s = collect(tasks, PolicyConfig(server.url, "m", version="v3"), tmp_path, desktops=3, group=4, **fake())
    rs = read_rollouts(tmp_path)
    assert s["status"] == {"ok": 12} and s["groups_full"] == 3 and s["logprob_coverage"] == 1.0
    assert all(r["n_steps"] == 2 and r["policy"]["version"] == "v3" for r in rs)
    assert s["prefix_cache_hit"] == pytest.approx(0.8)
    step = rs[0]["steps"][1]
    msgs = load_request(step["request"]["messages"], tmp_path)
    url = msgs[1]["content"][0]["image_url"]["url"]
    assert url.startswith("legato-image://") and (tmp_path / "images" / f"{url.split('//')[1]}.png").exists()
    assert load_request(step["request"]["messages"], tmp_path, inline_images=True)[1]["content"][0]["image_url"]["url"].startswith("data:image/png")
    assert step["response"]["logprobs"] == [-0.5] and step["response"]["tokens"] is None   # ids present: floats only
    # the system prompt and step 0's messages are stored once, not again at step 1
    assert set(rs[0]["steps"][0]["request"]["messages"]) <= set(step["request"]["messages"])
    n_files = len(list((tmp_path / "messages").glob("*.json")))
    assert n_files < sum(len(s_["request"]["messages"]) for r in rs for s_ in r["steps"])
    adv = group_advantages(rs)
    for g in {r["group_id"] for r in rs}:
        assert abs(sum(adv[r["rollout_id"]] for r in rs if r["group_id"] == g)) < 1e-6
    out = export_steps(tmp_path, tmp_path / "train.jsonl")
    lines = [json.loads(x) for x in (tmp_path / "train.jsonl").read_text().splitlines()]
    assert out == {"rollouts": 12, "steps": 24, "dest": str(tmp_path / "train.jsonl")} and len(lines) == 24
    assert {round(x["advantage"], 3) for x in lines} == {1.0, -1.0} and lines[0]["response_logprobs"]
    assert lines[0]["messages"][0]["role"] == "system"


def test_oversample_closes_groups_and_cancels_stragglers(server, tmp_path):
    server.reply = "click"                       # never DONE: rollouts run to max_steps
    tasks = [{"task_id": f"t{i}", "reward": 1.0} for i in range(2)]
    s = collect(tasks, PolicyConfig(server.url, "m"), tmp_path, desktops=4, group=2, oversample=1.0, max_steps=3,
                **fake(act_s=0.05))
    rs = read_rollouts(tmp_path)
    for g in ("t0@v0", "t1@v0"):
        assert sum(1 for r in rs if r["group_id"] == g and r["status"] in ("ok", "truncated")) == 2
    assert s["groups_full"] == 2 and len(rs) == 8
    assert set(s["status"]) <= {"ok", "cancelled", "skipped", "surplus"}


def test_env_failure_is_retried(server, tmp_path):
    server.reply = "DONE"
    s = collect([{"task_id": "t", "reward": 1.0}], PolicyConfig(server.url, "m"), tmp_path, desktops=1, group=4,
                env_retries=2, **fake(fail_reset_every=3))
    assert s["status"].get("retried", 0) >= 1 and s["status"]["ok"] == 4 and s["env_errors"] >= 1


def test_admission_cap_limits_concurrent_requests(server, tmp_path):
    server.delay_s, server.reply = 0.2, "DONE"
    collect([{"task_id": f"t{i}"} for i in range(4)], PolicyConfig(server.url, "m"), tmp_path, desktops=4, group=1,
            max_inflight=1, **fake())
    assert server.max_inflight == 1


def test_timeout_is_a_wall_clock_deadline_even_with_keep_alive_bytes():
    """A server that keeps the connection busy with whitespace (as OpenRouter does while generating) must not hold a
    call open past timeout_s."""
    import socket
    import threading
    import time as _t
    from legato.rl.policy import PolicyClient, PolicyConfig, PolicyError

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def serve():
        c, _ = srv.accept()
        c.recv(65536)
        c.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n")
        try:
            for _ in range(40):                      # 0.1 s apart for 4 s: never quiet long enough for a read timeout
                c.sendall(b"1\r\n \r\n")
                _t.sleep(0.1)
        except OSError:
            pass
        c.close()
    threading.Thread(target=serve, daemon=True).start()
    cli = PolicyClient(PolicyConfig(f"http://127.0.0.1:{port}/v1", "m", timeout_s=0.5, retries=0, logprobs=False,
                                    token_ids=False))
    t = _t.perf_counter()
    with pytest.raises(PolicyError, match="timeout"):
        cli.chat([{"role": "user", "content": "x"}])
    assert _t.perf_counter() - t < 1.5
