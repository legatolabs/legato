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
    """Behaviour knobs, set per test: delay_s, fail_first (HTTP 500 count), busy_first (HTTP 503 with Retry-After),
    reject_token_ids, reply (text, or a function of the body returning text or a message: content, tool_calls and
    tokens, the logprob tokens)."""

    def __init__(self):
        self.delay_s, self.fail_first, self.reject_token_ids, self.reply = 0.0, 0, False, "click it"
        self.busy_first, self.retry_after = 0, "0.2"
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
                if n <= stub.fail_first + stub.busy_first:
                    return self._send(503, {"error": "starting"}, {"Retry-After": stub.retry_after})
                if stub.reject_token_ids and body.get("return_token_ids"):
                    return self._send(400, {"error": "unknown field return_token_ids"})
                reply = stub.reply(body) if callable(stub.reply) else stub.reply
                msg = reply if isinstance(reply, dict) else {"content": reply}
                toks = msg.get("tokens", (msg.get("content") or "").split())
                calls = {"tool_calls": msg["tool_calls"]} if msg.get("tool_calls") else {}
                out = {"choices": [{"message": {"content": msg.get("content"), "reasoning_content": "think", **calls},
                                    "finish_reason": "tool_calls" if calls else "stop",
                                    "logprobs": {"content": [{"token": t, "logprob": -0.5} for t in toks]},
                                    **({"token_ids": list(range(len(toks)))} if body.get("return_token_ids") else {})}],
                       "usage": {"prompt_tokens": 100, "completion_tokens": len(toks),
                                 "prompt_tokens_details": {"cached_tokens": 80}}}
                return self._send(200, out)
            finally:
                with stub.lock:
                    stub.inflight -= 1

        def _send(self, code, obj, headers=None):
            b = json.dumps(obj).encode()
            self.send_response(code)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.handle_error = lambda *a: None   # a reply to a client that gave up (a timeout test): no traceback in later tests
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


def test_policy_waits_out_a_starting_server(server):
    server.busy_first = 3                          # three 503s with Retry-After 0.2 s, then a reply
    t = time.perf_counter()
    rec = PolicyClient(PolicyConfig(server.url, "m", retries=0)).chat([{"role": "user", "content": "x"}])
    assert rec["attempts"] == 4 and 0.5 < time.perf_counter() - t < 3.0   # waited, and retries=0 was not used up
    server.calls, server.busy_first = 0, 50
    with pytest.raises(PolicyError, match="503"):  # but only for busy_wait_s in all
        PolicyClient(PolicyConfig(server.url, "m", retries=0, busy_wait_s=0.5)).chat([{"role": "user", "content": "x"}])


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
    # the re-run says why it was re-run
    assert any("reset failed" in e for r in read_rollouts(tmp_path) if r["status"] == "ok"
               for e in r.get("retried_after", []))


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


# ---------------------------------------------------------------- the RL researcher's first run

def run_cli(argv: list, capsys) -> tuple[int, str, str]:
    from legato.rl.__main__ import main
    code = main(argv)
    out, err = capsys.readouterr()
    return code, out, err


def cli_collect(server, tmp_path, *extra) -> list:
    tasks = tmp_path / "tasks.json"
    if not tasks.exists():
        tasks.write_text(json.dumps([{"task_id": "fake-1"}]))
    return ["collect", "--tasks", str(tasks), "--policy-url", server.url, "--model", "m", "--version", "v0",
            "--out", str(tmp_path / "run"), "--backend", "fake", "--desktops", "1", *extra]


def test_cli_fake_backend_runs_out_of_the_box(server, tmp_path, capsys):
    """--backend fake with the wiring-only harness collects (and says what that harness is); --temperature, --seed
    and the fake reward take effect and are recorded."""
    server.reply = "DONE"
    code, out, err = run_cli(cli_collect(server, tmp_path, "--group", "2", "--temperature", "0.7", "--seed", "3",
                                         "--harness", "fake", "--fake-opts", '{"reward": [1, 0]}'), capsys)
    assert code == 0 and "rollouts 2 (ok 2)" in out and "warning" not in err
    assert "--harness fake tests the wiring only" in err and "rewards are FAKE" in out
    rs = sorted(read_rollouts(tmp_path / "run"), key=lambda r: r["attempt"])
    assert [r["reward"] for r in rs] == [1.0, 0.0]
    assert all(r["sampling"]["temperature"] == 0.7 for r in rs) and all(b["temperature"] == 0.7 for b in server.bodies)
    seeds = [r["sampling"]["seed"] for r in rs]
    assert len(set(seeds)) == 2 and sorted(b["seed"] for b in server.bodies) == sorted(seeds)
    assert [r["steps"][0]["request"]["seed"] for r in rs] == seeds
    run = json.loads((tmp_path / "run" / "run.json").read_text())
    assert run["harness"]["name"] == "fake" and run["policy"]["temperature"] == 0.7 and run["policy"]["seed"] == 3
    export_steps(tmp_path / "run", tmp_path / "train.jsonl")
    lines = [json.loads(x) for x in (tmp_path / "train.jsonl").read_text().splitlines()]
    assert [round(x["advantage"], 3) for x in sorted(lines, key=lambda x: x["advantage"])] == [-1.0, 1.0]
    assert {x["sampling"]["seed"] for x in lines} == set(seeds) and all(x["tokens_cover_response"] for x in lines)
    assert sorted(x["attempt"] for x in lines) == [0, 1]


def test_cli_fails_fast_when_the_harness_cannot_load(server, tmp_path, capsys, monkeypatch):
    """The qwen harness needs OSWorld's mm_agents: say so before any worker starts. Also: the warning for a group
    sampled at temperature 0, and none with --eval-only."""
    from legato.rl.collect import SetupError, preflight
    monkeypatch.delenv("OSWORLD_PATH", raising=False)
    code, _, err = run_cli(cli_collect(server, tmp_path, "--harness", "qwen", "--temperature", "0"), capsys)
    assert code == 2 and "mm_agents" in err and "--harness legato" in err and "--backend fake" not in err
    assert "near-identical" in err and "--temperature 1.0" in err and len(err.strip().splitlines()) == 2
    assert not (tmp_path / "run" / "rollouts.jsonl").exists() and server.calls == 0
    code, _, err = run_cli(cli_collect(server, tmp_path, "--harness", "qwen", "--eval-only"), capsys)
    assert code == 2 and "near-identical" not in err
    with pytest.raises(SetupError, match="--osworld PATH.*Linux host with KVM.*--backend fake"):
        preflight(DesktopConfig(backend="osworld"), HarnessConfig(name="legato"))
    with pytest.raises(SetupError, match="desktop_env.*Linux host with KVM"):       # the default: say Linux first
        preflight(DesktopConfig(backend="osworld"), HarnessConfig(name="qwen"))


def test_cli_exits_1_when_every_rollout_fails(server, tmp_path, capsys):
    server.reply = "DONE"
    code, out, err = run_cli(cli_collect(server, tmp_path, "--group", "1", "--env-retries", "0",
                                         "--fake-opts", '{"fail_reset_every": 1}'), capsys)
    assert code == 1 and "rollouts 1 (error 1)" in out
    assert "no rollout completed" in err and "reset failed" in err


def test_cli_help_names_the_legato_command(capsys):
    from legato.rl.__main__ import main
    with pytest.raises(SystemExit):
        main(["collect", "--help"])
    out = capsys.readouterr().out
    assert out.startswith("usage: legato rl collect") and "python -m" not in out
    for flag in ("--temperature", "--seed", "--fake-opts", "--backend", "--group", "--max-steps", "--start-stagger"):
        assert flag in out
    assert "GRPO group" in out and "base sampling seed" in out and "docs/rl#tasks" in out
    assert main([]) == 2 and capsys.readouterr().out.startswith("usage: legato rl")


def test_temperature_given_any_way_applies_to_every_request(server, tmp_path):
    from legato.rl.collect import effective_temperature, given_temperature
    pc = PolicyConfig(server.url, "m")
    assert given_temperature(pc, HarnessConfig(name="legato")) is None
    assert effective_temperature(pc, HarnessConfig(name="legato")) == 0.0
    assert effective_temperature(pc, HarnessConfig(name="qwen")) is None             # QwenAgent decides
    assert given_temperature(pc, HarnessConfig(name="legato", options={"temperature": 0.4})) == 0.4
    assert given_temperature(PolicyConfig(server.url, "m", extra_body={"temperature": 0.3}),
                             HarnessConfig(options={"temperature": 0.4})) == 0.3
    assert given_temperature(PolicyConfig(server.url, "m", temperature=0.2, extra_body={"temperature": 0.3}),
                             HarnessConfig()) == 0.2
    # PolicyConfig.temperature beats the harness's own; the record says what was sent
    rec = PolicyClient(PolicyConfig(server.url, "m", temperature=0.9)).chat([{"role": "user", "content": "x"}],
                                                                            temperature=0.0)
    assert server.bodies[-1]["temperature"] == 0.9 and rec["sampling"]["temperature"] == 0.9
    # through collect: a temperature in extra_body is no longer overridden by the harness (the fake one asks for 1.0)
    server.reply, server.bodies = "DONE", []
    collect([{"task_id": "t"}], PolicyConfig(server.url, "m", extra_body={"temperature": 0.3}), tmp_path, desktops=1,
            group=1, **fake())
    r = read_rollouts(tmp_path)[0]
    assert [b["temperature"] for b in server.bodies] == [0.3] and r["sampling"] == {"seed": None, "temperature": 0.3}
    assert r["steps"][0]["request"]["temperature"] == 0.3 and "seed" not in server.bodies[0]


def test_seeds_are_per_rollout_and_reproducible(server):
    from legato.rl.collect import rollout_seed
    jobs = plan_jobs([{"task_id": "a"}, {"task_id": "b"}], group=2, oversample=0.0, version="v1", seed=5)
    assert len({j["seed"] for j in jobs}) == 4
    assert [j["seed"] for j in jobs] == [j["seed"] for j in plan_jobs([{"task_id": "a"}, {"task_id": "b"}], 2, 0.0,
                                                                        "v2", seed=5)]
    assert rollout_seed(5, "a", 0) != rollout_seed(6, "a", 0)
    assert all(j["seed"] is None for j in plan_jobs([{"task_id": "a"}], 2, 0.0, "v1"))
    server.fail_first = 1                          # a retried request resends its seed; the next request takes seed + 1
    c = PolicyClient(PolicyConfig(server.url, "m", seed=10, retries=1))
    a = c.chat([{"role": "user", "content": "x"}])
    b = c.chat([{"role": "user", "content": "y"}])
    assert [x["seed"] for x in server.bodies] == [10, 10, 11]
    assert (a["sampling"]["seed"], b["sampling"]["seed"]) == (10, 11)
    c.reseed(None)
    c.chat([{"role": "user", "content": "z"}])
    assert "seed" not in server.bodies[-1]


def test_generated_text_keeps_the_tool_call():
    from legato.rl.trajectory import generated_text
    call = [{"type": "function", "id": "c1", "function": {"name": "computer", "arguments": '{"action": "done"}'}}]
    assert generated_text("click it", None, ["click", " it"], True) == ("click it", True)
    assert generated_text("click it", None, None, False) == ("click it", False)
    # tool call only, no tokens for it (OpenRouter): the call as JSON, not covered
    assert generated_text("", call, None, False) == ('{"name": "computer", "arguments": {"action": "done"}}', False)
    # text plus a call whose tokens cover only the text: the text, then the call
    text, covered = generated_text("I am done.", call, ["I", " am", " done", "."], True)
    assert text == 'I am done.\n{"name": "computer", "arguments": {"action": "done"}}' and covered is False
    # the server's tokens hold the call markup (vLLM): exactly those tokens, covered
    toks = ["I am done.", "\n<tool_call>", '\n{"name": "computer", "arguments": {"action": "done"}}', "\n</tool_call>"]
    assert generated_text("I am done.", call, toks, True) == ("".join(toks), True)


def test_export_keeps_tool_calls_and_marks_uncovered_steps(server, tmp_path):
    call = {"type": "function", "id": "c1", "function": {"name": "computer", "arguments": '{"action": "done"}'}}
    markup = ["<tool_call>", '\n{"name": "computer", "arguments": {"action": "done"}}', "\n</tool_call>"]
    replies = iter([{"content": "", "tool_calls": [call], "tokens": []},          # OpenRouter: no tokens for the call
                    {"content": "", "tool_calls": [call], "tokens": markup}])     # vLLM: the markup among the tokens
    server.reply = lambda body: next(replies)
    s = collect([{"task_id": "t"}], PolicyConfig(server.url, "m", token_ids=False), tmp_path, desktops=1, group=2,
                max_steps=1, **fake())
    assert s["full_response_coverage"] == 0.5
    export_steps(tmp_path, tmp_path / "train.jsonl")
    a, b = sorted((json.loads(x) for x in (tmp_path / "train.jsonl").read_text().splitlines()),
                  key=lambda x: x["rollout_id"])
    assert a["response_text"] == '{"name": "computer", "arguments": {"action": "done"}}'
    assert a["tokens_cover_response"] is False and a["response_tool_calls"] == [call] and a["response_content"] == ""
    assert b["response_text"] == "".join(markup) and b["tokens_cover_response"] is True
    assert b["response_tokens"] == markup and b["finish_reason"] == "tool_calls"


def test_fake_desktop_ends_on_fail_and_takes_a_default_reward():
    from legato.rl.fake import FakeDesktop
    d = FakeDesktop(DesktopConfig(backend="fake", fake={"reward": [0.0, 1.0]}))
    d.reset({"task_id": "t", "_attempt": 1})
    assert d.act("FAIL")[1] is True and d.evaluate() == 1.0
    d.reset({"task_id": "t", "_attempt": 1, "reward": 0.5})
    assert d.evaluate() == 0.5


def test_provider_error_keeps_the_status_and_the_message_only():
    from legato.rl.policy import provider_error
    body = json.dumps({"error": {"message": "qwen/no-such-model-xyz is not a valid model ID", "code": 400},
                       "user_id": "user_3HscRKwEPQm2iykBVKsF6DLA36Q"}).encode()
    assert provider_error(400, body) == "HTTP 400: qwen/no-such-model-xyz is not a valid model ID"
    raw = json.dumps({"error": {"message": "max_tokens is too large", "type": "invalid_request_error"}})
    upstream = {"error": {"message": "Provider returned error", "code": 400,
                          "metadata": {"raw": raw, "provider_name": "Together"}}, "user_id": "user_x"}
    assert provider_error(400, json.dumps(upstream)) == "HTTP 400: Provider returned error: max_tokens is too large"
    assert provider_error(422, json.dumps({"detail": [{"msg": "field required"}]})) == "HTTP 422: field required"
    assert provider_error(404, json.dumps({"object": "error", "message": "The model `m` does not exist."})) == \
        "HTTP 404: The model `m` does not exist."
    assert provider_error(502, b"<html><body><h1>502 Bad Gateway</h1></body></html>") == "HTTP 502: 502 Bad Gateway"
    assert provider_error(500, json.dumps({"id": "abc", "user_id": "u"})) == "HTTP 500"
    long = provider_error(400, json.dumps({"error": "x" * 1000}))
    assert len(long) == 300 and long.endswith("...")


def test_policy_errors_carry_no_body_and_rl_clients_have_no_deadline(server):
    c = PolicyClient(PolicyConfig(server.url, "m", token_ids=False, retries=0))
    assert c.deadline is None
    server.fail_first = 1
    with pytest.raises(PolicyError, match=r"^the model at .* did not answer \(1 attempt\): HTTP 500: boom\.$"):
        c.chat([{"role": "user", "content": "x"}])


def test_a_deadline_caps_each_attempt_and_stops_retries(server):
    server.delay_s = 2.0
    c = PolicyClient(PolicyConfig(server.url, "m", timeout_s=60.0, retries=3))
    c.deadline = time.monotonic() + 0.4
    t = time.perf_counter()
    with pytest.raises(PolicyError, match="time limit"):
        c.chat([{"role": "user", "content": "x"}])
    assert time.perf_counter() - t < 1.2


# ---------------------------------------------------------------- round two

def test_collect_raises_when_no_rollout_completed(server, tmp_path):
    from legato.rl import CollectError
    server.reply = "DONE"
    with pytest.raises(CollectError, match="no rollout completed .*reset failed") as e:
        collect([{"task_id": "t"}], PolicyConfig(server.url, "m"), tmp_path, desktops=1, group=1, env_retries=0,
                **fake(fail_reset_every=1))
    assert e.value.summary["status"] == {"error": 1}
    assert json.loads((tmp_path / "summary.json").read_text())["completed"] == 0


def test_a_worker_importing_the_script_names_the_main_guard(tmp_path):
    """With the spawn start method each worker imports the calling script again; collect() there must not start
    workers of its own, and the parent must say to use the guard."""
    import multiprocessing as mp
    from types import SimpleNamespace

    from legato.rl.collect import _no_rollout
    me = mp.current_process()
    me._inheriting = True
    try:
        with pytest.raises(RuntimeError, match='if __name__ == "__main__":'):
            collect([{"task_id": "t"}], PolicyConfig("http://x/v1", "m"), tmp_path, **fake())
    finally:
        del me._inheriting
    assert not (tmp_path / "run.json").exists()
    ctx = mp.get_context("spawn")
    died = [SimpleNamespace(exitcode=1), SimpleNamespace(exitcode=1)]
    assert 'if __name__ == "__main__":' in _no_rollout([], {"status": {}}, died, ctx.SimpleQueue(), ctx)
    q = ctx.SimpleQueue()
    q.put("worker 0 could not start: ImportError: no module named x")
    assert _no_rollout([], {"status": {}}, died, q, ctx).endswith("ImportError: no module named x")


@pytest.mark.skipif(sys.platform.startswith("linux"), reason="fork start method: workers do not import the script")
def test_collect_without_the_main_guard_raises_and_says_so(server, tmp_path):
    import os
    import subprocess
    script = tmp_path / "loop.py"
    script.write_text(
        "from legato.rl import collect, PolicyConfig, DesktopConfig, HarnessConfig\n"
        f"collect([{{'task_id': 't'}}], PolicyConfig({server.url!r}, 'm'), {str(tmp_path / 'run')!r}, desktops=1,\n"
        "        group=1, desktop=DesktopConfig(backend='fake'), harness=HarnessConfig(name='fake'))\n")
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    p = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=120, env=env)
    assert p.returncode == 1 and "CollectError" in p.stderr
    assert p.stderr.strip().splitlines()[-1].count('if __name__ == "__main__":') == 1 and server.calls == 0


def test_rollouts_per_hour_counts_real_wall_clock(server, tmp_path, capsys):
    server.reply, server.delay_s = "DONE", 0.3       # 4 rollouts of 0.3 s on 2 desktops: at least 0.6 s of work
    t = time.time()
    s = collect([{"task_id": f"t{i}"} for i in range(2)], PolicyConfig(server.url, "m"), tmp_path, desktops=2,
                group=2, desktop=DesktopConfig(backend="fake"), harness=HarnessConfig(name="fake"),
                start_stagger_s=0.5)
    elapsed = time.time() - t
    assert 0.6 <= s["wall_s"] < elapsed and s["rollouts_per_hour"] == pytest.approx(4 / s["wall_s"] * 3600)
    assert json.loads((tmp_path / "run.json").read_text())["start_stagger_s"] == 0.5
    from legato.rl.__main__ import main
    assert main(["profile", str(tmp_path)]) == 0
    assert f"over {s['wall_s']:.2f} s of wall clock" in capsys.readouterr().out
    server.delay_s = 0.0
    collect([{"task_id": "t"}], PolicyConfig(server.url, "m"), tmp_path / "b", desktops=1, group=1, **{
        k: v for k, v in fake().items() if k != "start_stagger_s"})
    assert json.loads((tmp_path / "b" / "run.json").read_text())["start_stagger_s"] == 0.0     # fake: no stagger


def test_retries_say_why(server, tmp_path):
    server.busy_first, server.fail_first = 1, 1       # a 500 (counted against retries), then a 503 (waited out)
    rec = PolicyClient(PolicyConfig(server.url, "m", retries=1)).chat([{"role": "user", "content": "x"}])
    assert rec["attempts"] == 3 and len(rec["retry_reasons"]) == 2
    assert rec["retry_reasons"][0] == "HTTP 500: boom" and "busy" in rec["retry_reasons"][1]
    server.calls, server.busy_first, server.fail_first, server.reply = 0, 1, 0, "DONE"
    collect([{"task_id": "t"}], PolicyConfig(server.url, "m"), tmp_path, desktops=1, group=1, **fake())
    m = read_rollouts(tmp_path)[0]["steps"][0]["model"]
    assert m["attempts"] == 2 and m["retry_reasons"][0].startswith("HTTP 503: starting")


def test_fake_screen_is_readable_and_tiny_png_is_unchanged():
    from PIL import Image
    import io
    from legato.rl.fake import FakeDesktop, fake_screen, tiny_png
    d = FakeDesktop(DesktopConfig(backend="fake"))
    d.reset({"task_id": "t", "instruction": "Open the report"})
    a = d.observe()["screenshot"]
    b = d.act("pyautogui.click(1, 1)")[0]["screenshot"]
    assert Image.open(io.BytesIO(a)).size == (640, 400) and a != b
    assert Image.open(io.BytesIO(fake_screen("x", 0, (320, 200)))).size == (320, 200)
    assert Image.open(io.BytesIO(tiny_png(1, w=8, h=8))).size == (8, 8)


def test_cut_off_attempts_are_counted_on_the_error_and_the_record(server):
    server.delay_s = 1.0
    with pytest.raises(PolicyError) as e:
        PolicyClient(PolicyConfig(server.url, "m", timeout_s=0.3, retries=1)).chat([{"role": "user", "content": "x"}])
    assert e.value.cut == 2                          # both were sent and may be billed
    server.delay_s = 0.0
    assert PolicyClient(PolicyConfig(server.url, "m")).chat([{"role": "user", "content": "x"}])["cut"] == 0


# ---------------------------------------------------------------- the first try on a machine without VMs

def tool(action: str, **kw) -> dict:
    args = json.dumps({"action": action, **kw})
    return {"content": "", "tool_calls": [{"type": "function", "id": "c1",
                                           "function": {"name": "computer", "arguments": args}}], "tokens": []}


def click_then_done(body: dict) -> dict:
    """A policy that clicks once, then finishes."""
    seen = sum(1 for m in body["messages"] if m["role"] == "assistant")
    return tool("left_click", coordinate=[100, 50]) if not seen else tool("done")


def test_the_fake_backend_runs_the_models_own_actions_and_labels_the_reward(server, tmp_path, capsys):
    """--backend fake alone uses legato.Harness: the actions are what the model called, the export carries them
    parsed, and the reward says it is fake in the record, the export and the report."""
    server.reply = click_then_done
    code, out, err = run_cli(cli_collect(server, tmp_path, "--group", "1", "--fake-opts", '{"reward": 1}'), capsys)
    assert code == 0 and "--harness fake" not in err
    assert "rewards are FAKE" in out and "legato rl export" in out and "rollouts.jsonl" in out
    assert "0% of steps came back with logprobs" in err and "vLLM" in err
    (r,) = read_rollouts(tmp_path / "run")
    assert json.loads((tmp_path / "run" / "run.json").read_text())["harness"]["name"] == "legato"
    assert r["reward"] == 1.0 and r["reward_source"] == "fake"
    first = r["steps"][0]["actions"][0]
    assert first["action"] == "pyautogui.click(64, 20)" and first["parsed"] == {"type": "click", "x": 64, "y": 20}
    export_steps(tmp_path / "run", tmp_path / "train.jsonl")
    a, b = [json.loads(x) for x in (tmp_path / "train.jsonl").read_text().splitlines()]
    assert a["actions"] == [{"action": "pyautogui.click(64, 20)", "parsed": {"type": "click", "x": 64, "y": 20}}]
    assert b["actions"][0]["parsed"] == {"type": "done"} and a["reward_source"] == b["reward_source"] == "fake"
    assert a["response_tool_calls"]                       # the fields from before are still there


def test_json_options_take_a_file_and_a_bad_one_is_one_line(server, tmp_path, capsys):
    """PowerShell strips the quotes inside an argument: @file.json works everywhere, and what PowerShell passes
    ({reward: [1, 0, 1]}) is answered in one line that shows the way."""
    server.reply = "DONE"
    opts = tmp_path / "opts.json"
    opts.write_text('\ufeff{"reward": [1, 0, 1]}', encoding="utf-8")       # PowerShell 5's Set-Content writes a BOM
    code, out, _ = run_cli(cli_collect(server, tmp_path, "--group", "3", "--harness", "fake", "--fake-opts", f"@{opts}",
                                       "--extra-body", f"@{opts}"), capsys)
    assert code == 0 and sorted(r["reward"] for r in read_rollouts(tmp_path / "run")) == [0.0, 1.0, 1.0]
    assert all(b["reward"] == [1, 0, 1] for b in server.bodies)
    (tmp_path / "tasks.json").write_text('[{"task_id": "t"}]', encoding="utf-16")      # PowerShell 5's `>` redirect
    assert run_cli(cli_collect(server, tmp_path, "--group", "1", "--harness", "fake"), capsys)[0] == 0
    for flag in ("--fake-opts", "--harness-opts", "--extra-body", "--backend-opts"):
        code, _, err = run_cli(cli_collect(server, tmp_path, flag, "{reward: [1, 0, 1]}"), capsys)
        assert code == 2 and len(err.strip().splitlines()) == 1 and "Traceback" not in err
        assert err.startswith(f"legato rl collect: {flag}: not valid JSON") and f"{flag} @file.json" in err
    code, _, err = run_cli(cli_collect(server, tmp_path, "--fake-opts", "@nope.json"), capsys)
    assert code == 2 and "cannot read nope.json" in err and len(err.strip().splitlines()) == 1
    with pytest.raises(SystemExit):                       # double-quoted in PowerShell: the JSON arrives in pieces
        run_cli(cli_collect(server, tmp_path, "--fake-opts", "{reward:", "[1,", "0]}"), capsys)
    assert "unrecognized arguments" in (err := capsys.readouterr().err) and "--fake-opts @opts.json" in err
    code, _, err = run_cli(["collect", "--tasks", str(tmp_path / "none.json"), "--policy-url", server.url, "--model",
                            "m", "--version", "v", "--out", str(tmp_path / "o")], capsys)
    assert code == 2 and "cannot read it" in err and len(err.strip().splitlines()) == 1


def probing(stub, vision=True, tools=True, logprobs=True):
    """Make the stub a server with or without image input, tool calls and logprobs."""
    def reply(body):
        last = body["messages"][-1]["content"]
        if isinstance(last, list) and not vision:
            raise LookupError("No endpoints found that support image input")
        if body.get("tools") and tools:
            return {"content": "", "tool_calls": [{"type": "function", "id": "c", "function": {
                "name": body["tools"][0]["function"]["name"], "arguments": '{"x": 10, "y": 20}'}}]}
        return {"content": "ok"} if logprobs else {"content": "ok", "tokens": []}
    stub.reply = reply


def test_probe_sends_an_image_and_a_tool_call_and_says_what_the_server_is_good_for(server, capsys):
    probing(server)
    code, out, err = run_cli(["probe", "--policy-url", server.url, "--model", "m"], capsys)
    p = json.loads(out)
    assert code == 0 and p["logprobs"] and p["vision"] and p["tool_calls"] and p["cached_on_repeat"] == 80
    assert any(isinstance(b["messages"][-1]["content"], list) for b in server.bodies)
    assert any(b.get("tools") for b in server.bodies)
    assert "evaluation and data collection: yes" in err and "policy-gradient training (GRPO): yes" in err
    probing(server, tools=False, logprobs=False)          # a hosted API: no logprobs; a model that calls no tools
    code, out, err = run_cli(["probe", "--policy-url", server.url, "--model", "m"], capsys)
    p = json.loads(out)
    assert code == 0 and not p["logprobs"] and p["vision"] and not p["tool_calls"]
    assert "policy-gradient training (GRPO): no, it returns no logprobs; use vLLM" in err
    assert "tool calls: no" in err and "native_tools" in err


def test_probe_fails_a_text_only_model_and_a_wrong_model_in_one_line(capsys):
    """A model without image input passes the text checks; collect would then fail on its first screenshot."""
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if body["model"] == "nope":
                code, out = 400, {"error": {"message": "nope is not a valid model ID"}}
            elif isinstance(body["messages"][-1]["content"], list):
                code, out = 404, {"error": {"message": "No endpoints found that support image input"}}
            else:
                code, out = 200, {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": {}}
            b = json.dumps(out).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/v1"
    try:
        code, out, err = run_cli(["probe", "--policy-url", url, "--model", "text-only"], capsys)
        assert code == 1 and json.loads(out)["vision"] is False
        assert "evaluation and data collection: no" in err and "support image input" in err
        code, out, err = run_cli(["probe", "--policy-url", url, "--model", "nope"], capsys)
        assert code == 1 and not out
        assert err.strip() == "legato rl probe: HTTP 400: nope is not a valid model ID"
    finally:
        srv.shutdown()


def test_require_logprobs_stops_before_any_rollout(server, tmp_path, capsys):
    probing(server, logprobs=False)
    code, out, err = run_cli(cli_collect(server, tmp_path, "--require-logprobs", "--harness", "fake"), capsys)
    assert code == 1 and server.calls == 1 and not (tmp_path / "run").exists()
    assert "returned no logprobs" in err and "Nothing was collected" in err and "vLLM" in err
    probing(server)
    code, out, err = run_cli(cli_collect(server, tmp_path, "--require-logprobs", "--harness", "fake", "--group", "1",
                                         "--max-steps", "1"), capsys)
    assert code == 0 and "came back with logprobs" not in err
    code, _, err = run_cli(cli_collect(server, tmp_path, "--require-logprobs", "--eval-only"), capsys)
    assert code == 2 and "contradict" in err


EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "rl_custom_backend.py"


def test_your_own_backend_by_path_runs_in_the_workers(server, tmp_path, capsys, monkeypatch):
    """--backend file.py:Class: each worker (a spawned process on macOS and Windows) imports the file by its path,
    from whatever directory; --backend-opts are the class's keyword arguments."""
    server.reply = click_then_done
    monkeypatch.chdir(EXAMPLE.parent)
    code, out, err = run_cli([*cli_collect(server, tmp_path, "--group", "2", "--desktops", "2", "--backend-opts",
                                           '{"size": [320, 200]}'),
                              "--backend", "rl_custom_backend.py:ButtonDesktop"], capsys)
    assert code == 0 and "rollouts 2 (ok 2)" in out and "FAKE" not in out, err
    rs = read_rollouts(tmp_path / "run")
    assert all(r["reward_source"].startswith("ButtonDesktop") and r["reward"] in (0.0, 1.0) for r in rs)
    assert all(r["steps"][0]["actions"][0]["parsed"]["type"] == "click" for r in rs)
    run = json.loads((tmp_path / "run" / "run.json").read_text())
    assert run["desktop"]["backend"] == f"{EXAMPLE}:ButtonDesktop" and run["harness"]["name"] == "legato"
    assert len(list((tmp_path / "run" / "images").glob("*.png"))) >= 2          # 320x200 screens the class drew


def test_the_example_backend_rewards_a_click_on_its_button():
    from legato.actions import Action
    from legato.rl.collect import load_backend, to_pyautogui
    d = load_backend(f"{EXAMPLE}:ButtonDesktop")()
    d.reset({"task_id": "t"})
    x, y = d.box[0] + 5, d.box[1] + 5
    assert d.act(to_pyautogui(Action("click", x=0, y=0)))[1] is False and d.evaluate() == 0.0
    assert d.act(to_pyautogui(Action("click", x=x, y=y)))[1] is False and d.evaluate() == 1.0
    assert d.act(to_pyautogui(Action("done")))[1] is True and d.observe()["screenshot"][:4] == b"\x89PNG"


def test_a_backend_that_cannot_load_is_one_line(server, tmp_path, capsys):
    bad = tmp_path / "mine.py"
    bad.write_text("class Half:\n    def reset(self, task): ...\nimport nothing_like_this\n")
    for spec, why in ((f"{tmp_path / 'gone.py'}:X", "no file"), (f"{bad}:Half", "cannot import"), ("nonsense", "use osworld"),
                      ("json:Missing", "has no Missing"), ("json:JSONDecoder", "has no reset, observe, act, evaluate"),
                      ("local", "--backend local needs --backend-opts")):
        code, _, err = run_cli([*cli_collect(server, tmp_path), "--backend", spec], capsys)
        assert code == 2 and why in err and len(err.strip().splitlines()) <= 2 and "Traceback" not in err, err
    assert server.calls == 0


def test_the_local_backend_runs_your_reset_and_your_grading(tmp_path):
    """--backend local: legato's own screen driver, with the reset and the reward from the user's hooks file."""
    from legato import safety
    from legato.actions import Action
    from legato.env import Obs
    from legato.rl.collect import SetupError, to_pyautogui
    from legato.rl.local import LocalDesktop, check
    hooks = tmp_path / "hooks.py"
    hooks.write_text("log = []\ndef reset(task): log.append(('reset', task['task_id']))\n"
                     "def evaluate(task): return 0.5\n")

    class Screen:
        ran: list = []

        def observe(self):
            return Obs(b"png", 1, 1)

        def act(self, a):
            if a.type == "key":
                raise safety.Denied("cmd+q")
            if a.type == "wait":
                raise safety.Stopped("the mouse was moved into a corner")
            self.ran.append(a)
            return Obs(b"after", 1, 1)

    d = LocalDesktop(str(hooks), env=Screen())
    d.reset({"task_id": "t1", "instruction": "Save the file"})
    assert d.instruction == "Save the file" and d.hooks.log == [("reset", "t1")] and d.observe() == {"screenshot": b"png"}
    obs, done, _ = d.act(to_pyautogui(Action("click", x=3, y=4)))
    assert obs == {"screenshot": b"after"} and not done and Screen.ran[0].x == 3
    assert d.act(to_pyautogui(Action("key", keys="cmd+q")))[:2] == ({"screenshot": b"png"}, False)   # refused, not run
    assert d.act(to_pyautogui(Action("done")))[1] is True
    assert d.evaluate() == 0.5 and d.reward_source == "hooks.py:evaluate"
    with pytest.raises(SystemExit, match="stopped by the person"):
        d.act(to_pyautogui(Action("wait")))
    with pytest.raises(SetupError, match="--harness legato"):
        check(DesktopConfig(backend="local", options={"hooks": str(hooks)}), HarnessConfig(name="qwen"))
    with pytest.raises(SetupError, match="--desktops 1"):
        collect([{"task_id": "t"}], PolicyConfig("http://127.0.0.1:1/v1", "m"), tmp_path / "o", desktops=2,
                desktop=DesktopConfig(backend="local", options={"hooks": str(hooks)}), harness=HarnessConfig(name="legato"))


def test_bench_help_lists_its_commands(capsys):
    from legato.__main__ import main
    assert main(["bench", "--help"]) == 0
    out = capsys.readouterr().out
    assert "legato bench office" in out and "legato bench h2h" in out and "docs/benchmarks.md" in out
    assert main(["bench", "nope"]) == 2 and "legato bench office" in capsys.readouterr().err
