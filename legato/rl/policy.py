"""The policy call for RL rollouts: one OpenAI-compatible chat request per step, recorded for training.

What it adds over a plain client:
- Token logprobs for every generated token, and token ids where the server supports them (vLLM's
  `return_token_ids`), so the trainer gets exactly what was sampled.
- A per-step timeout and a bounded retry. In our OSWorld study, OSWorld's default client let a single hung request stall a
  rollout for 34 minutes; here a step costs at most `timeout * (retries + 1)` plus a short backoff.
- Patience with a busy or starting server: a 429 or 503 is retried after its Retry-After (at most 60 s at a time) for up
  to `busy_wait_s` in all, without using up `retries`. A cold GPU behind a gateway answers 503 for a minute or two.
- An optional cross-process semaphore that caps how many requests reach the model server at once (admission
  control). Our OSWorld study measured one H100 serving Qwen3.5-9B: latency doubled at ~32 agents, throughput peaked at ~48 and
  collapsed past 64 when the KV cache filled.
- The policy version on every record, so off-policy rollouts can be corrected by the trainer.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx

RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
BUSY_STATUS = {429, 503}               # "come back later": waited out for up to busy_wait_s, honouring Retry-After


@dataclass
class PolicyConfig:
    base_url: str                      # OpenAI-compatible root, e.g. https://host/v1
    model: str
    version: str = "v0"                # the trainer's policy version tag (step number, adapter name)
    api_key: str = ""
    timeout_s: float = 60.0            # per attempt, wall clock (keep-alive bytes from the server do not extend it)
    retries: int = 1
    busy_wait_s: float = 120.0         # total time to wait out 429/503 replies (Retry-After), on top of `retries`
    logprobs: bool = True
    token_ids: bool = True             # vLLM extra; turned off automatically if the server refuses it
    extra_body: dict = field(default_factory=dict)


class PolicyError(RuntimeError):
    pass


class _Deadline(Exception):
    pass


class _Resp:
    """Status and body of a response read under a wall-clock deadline."""

    def __init__(self, status_code: int, content: bytes, headers: dict | None = None):
        self.status_code, self.content, self.headers = status_code, content, headers or {}

    def retry_after(self) -> float | None:
        v = next((v for k, v in self.headers.items() if k.lower() == "retry-after"), None)
        try:
            return max(0.0, float(v)) if v is not None else None
        except ValueError:                             # an HTTP date: not worth parsing here
            return None

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", "replace")

    def json(self):
        import json
        return json.loads(self.content)


class PolicyClient:
    def __init__(self, cfg: PolicyConfig, semaphore: Any = None):
        self.cfg = cfg
        self.sem = semaphore
        self.http = httpx.Client(timeout=httpx.Timeout(cfg.timeout_s, connect=min(10.0, cfg.timeout_s)),
                                 headers={"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {})
        self.url = cfg.base_url.rstrip("/") + "/chat/completions"

    def _body(self, messages: list, max_tokens: int | None, temperature: float | None, top_p: float | None,
              tools: list | None = None, tool_choice=None, extra: dict | None = None) -> dict:
        b: dict = {"model": self.cfg.model, "messages": messages, **self.cfg.extra_body, **(extra or {})}
        if tools:
            b["tools"] = tools
            if tool_choice is not None:
                b["tool_choice"] = tool_choice
        if max_tokens is not None:
            b["max_tokens"] = max_tokens
        if temperature is not None:
            b["temperature"] = temperature
        if top_p is not None:
            b["top_p"] = top_p
        if self.cfg.logprobs:
            b["logprobs"] = True
        if self.cfg.token_ids:
            b["return_token_ids"] = True
        return b

    def chat(self, messages: list, max_tokens: int | None = None, temperature: float | None = None,
             top_p: float | None = None, tools: list | None = None, tool_choice=None, extra: dict | None = None) -> dict:
        """One policy step. `extra` is merged into this request's body. Returns a record: content (what the harness parses), reasoning, token ids and logprobs,
        usage, wall seconds, attempts. Raises PolicyError when every attempt fails."""
        last = ""
        t0 = time.perf_counter()
        attempt = failures = 0
        busy_waited = 0.0
        while True:
            attempt += 1
            wait = None                                    # set when the server asked us to come back later
            acquired = self.sem.acquire(timeout=max(600.0, 10 * self.cfg.timeout_s)) if self.sem is not None else True
            ta = time.perf_counter()
            try:
                r = self._post(self._body(messages, max_tokens, temperature, top_p, tools, tool_choice, extra), ta)
                if r.status_code == 400 and self.cfg.token_ids and "token_ids" in r.text:
                    self.cfg.token_ids = False             # server does not know return_token_ids: drop it, retry
                    r = self._post(self._body(messages, max_tokens, temperature, top_p, tools, tool_choice, extra), ta)
                if r.status_code in RETRY_STATUS:
                    last = f"HTTP {r.status_code}: {r.text[:200]}"
                    if r.status_code in BUSY_STATUS and busy_waited < self.cfg.busy_wait_s:
                        ra = r.retry_after()
                        wait = min(60.0, ra if ra is not None else 2.0 * min(attempt, 5),
                                   self.cfg.busy_wait_s - busy_waited)
                elif r.status_code != 200:
                    raise PolicyError(f"HTTP {r.status_code}: {r.text[:300]}")
                else:
                    return self._record(r.json(), time.perf_counter() - t0, time.perf_counter() - ta, attempt)
            except (httpx.TimeoutException, _Deadline):
                last = f"timeout after {self.cfg.timeout_s:.0f} s"
            except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadError) as e:
                last = f"{type(e).__name__}: {str(e)[:200]}"
            finally:
                if self.sem is not None and acquired:
                    self.sem.release()
            if wait is not None:                           # busy or starting: wait it out, not counted as a failure
                time.sleep(wait)
                busy_waited += wait
                continue
            failures += 1
            if failures > self.cfg.retries:
                break
            time.sleep(min(2.0, 0.5 * failures))
        raise PolicyError(f"policy call failed after {attempt} attempts: {last}")

    def _post(self, body: dict, t_start: float) -> _Resp:
        """POST and read the response, giving up `timeout_s` after t_start. httpx's own timeouts bound each wait for
        bytes, not the whole response: a hosted API that sends keep-alive bytes while it generates (OpenRouter does)
        would otherwise hold a call open for as long as the generation runs (818 s for one 32,768-token reply)."""
        buf = bytearray()
        with self.http.stream("POST", self.url, json=body) as r:
            for chunk in r.iter_bytes():
                buf += chunk
                if time.perf_counter() - t_start > self.cfg.timeout_s:
                    raise _Deadline()
            return _Resp(r.status_code, bytes(buf), dict(r.headers))

    def _record(self, j: dict, total_s: float, last_s: float, attempts: int) -> dict:
        ch = (j.get("choices") or [{}])[0]
        msg = ch.get("message") or {}
        lp = (ch.get("logprobs") or {}).get("content") or []
        u = j.get("usage") or {}
        return {
            "content": msg.get("content") or "",
            "tool_calls": msg.get("tool_calls") or None,
            "reasoning": msg.get("reasoning_content") or msg.get("reasoning") or None,
            "finish_reason": ch.get("finish_reason"),
            "token_ids": ch.get("token_ids"),
            "prompt_token_ids_len": len(j["prompt_token_ids"]) if isinstance(j.get("prompt_token_ids"), list) else None,
            "logprobs": [{"t": x.get("token"), "lp": x.get("logprob")} for x in lp] or None,
            "usage": {"prompt": u.get("prompt_tokens"), "completion": u.get("completion_tokens"),
                      "cached": (u.get("prompt_tokens_details") or {}).get("cached_tokens"),
                      "cost": u.get("cost")},                  # OpenRouter's billed USD, when it reports it
            "s": total_s, "last_attempt_s": last_s, "attempts": attempts,
            "model": self.cfg.model, "version": self.cfg.version,
        }

    def probe(self) -> dict:
        """Check the server once: does it return logprobs, token ids and cached-token counts?"""
        msgs = [{"role": "user", "content": "legato probe " + " ".join(f"w{i}" for i in range(300))}]
        a = self.chat(msgs, max_tokens=4, temperature=0.0)
        b = self.chat(msgs, max_tokens=4, temperature=0.0)
        return {"logprobs": bool(a["logprobs"]), "token_ids": a["token_ids"] is not None and self.cfg.token_ids,
                "cached_tokens_reported": b["usage"]["cached"] is not None, "cached_on_repeat": b["usage"]["cached"],
                "call_s": round(b["s"], 3)}

    def close(self) -> None:
        self.http.close()
