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
- A provider's error as its status code and message only (`provider_error`): OpenRouter's error body also carries the
  account's user_id.
"""
from __future__ import annotations

import json
import re
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


def provider_error(status: int, body: bytes | str, most: int = 300) -> str:
    """"HTTP <status>: <the provider's message>", and nothing else from the body (OpenRouter's carries the account's
    user_id), at most `most` characters. Reads the usual shapes: {"error": {"message"}} (OpenAI, OpenRouter, with
    the upstream provider's own message from error.metadata.raw), {"error": "..."}, {"message"}, {"detail"}."""
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body or "")
    try:
        msg = _message(json.loads(text))
    except ValueError:                                 # not JSON: an HTML error page or plain text
        msg = re.sub(r"<[^>]*>", " ", text)
    msg = " ".join(msg.split())
    out = f"HTTP {status}: {msg}" if msg else f"HTTP {status}"
    return out if len(out) <= most else out[: most - 3] + "..."


def _message(j: Any, depth: int = 0) -> str:
    """The human-readable message in a JSON error body, or ""."""
    if isinstance(j, str):
        return j
    if not isinstance(j, dict) or depth > 2:
        return ""
    err = j.get("error")
    if isinstance(err, str):
        return err
    if isinstance(err, dict):
        msg = err.get("message") if isinstance(err.get("message"), str) else ""
        raw = err.get("metadata", {}).get("raw") if isinstance(err.get("metadata"), dict) else None
        if isinstance(raw, str):                       # the upstream provider's own body, as text
            try:
                raw = json.loads(raw)
            except ValueError:
                pass
        inner = _message(raw, depth + 1) if raw is not None else ""
        if inner and inner not in msg:
            msg = f"{msg}: {inner}" if msg else inner
        return msg
    for k in ("message", "detail"):
        v = j.get(k)
        if isinstance(v, str):
            return v
        if isinstance(v, list):                        # FastAPI's validation errors: [{"msg": ...}, ...]
            return "; ".join(str(x.get("msg")) for x in v if isinstance(x, dict) and x.get("msg"))
    return ""


def attribution_headers(base_url: str) -> dict[str, str]:
    """OpenRouter's app attribution: names Legato (not you or your task) on OpenRouter's public app rankings.
    Sent only to openrouter.ai; set LEGATO_NO_ATTRIBUTION=1 to turn it off."""
    import os
    if "openrouter.ai" not in base_url or os.environ.get("LEGATO_NO_ATTRIBUTION"):
        return {}
    return {"HTTP-Referer": "https://legato.computer", "X-Title": "Legato"}


class PolicyClient:
    def __init__(self, cfg: PolicyConfig, semaphore: Any = None):
        self.cfg = cfg
        self.sem = semaphore
        # a time.monotonic() deadline (Harness.run's timeout_s): no attempt, retry or wait runs past it, and each
        # attempt's timeout is at most the time left. None: no deadline (each attempt gets timeout_s).
        self.deadline: float | None = None
        headers = {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}
        headers.update(attribution_headers(cfg.base_url))
        self.http = httpx.Client(timeout=httpx.Timeout(cfg.timeout_s, connect=min(10.0, cfg.timeout_s)),
                                 headers=headers)
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
            limit = self._limit()
            if self.deadline is not None and limit <= 0:   # the run's deadline has passed: no new attempt
                raise PolicyError("the run's time limit ran out" + (f" ({last})" if last else ""))
            attempt += 1
            wait = None                                    # set when the server asked us to come back later
            acquired = self.sem.acquire(timeout=max(600.0, 10 * self.cfg.timeout_s)) if self.sem is not None else True
            ta = time.perf_counter()
            try:
                r = self._post(self._body(messages, max_tokens, temperature, top_p, tools, tool_choice, extra), ta,
                               limit)
                if r.status_code == 400 and self.cfg.token_ids and "token_ids" in r.text:
                    self.cfg.token_ids = False             # server does not know return_token_ids: drop it, retry
                    r = self._post(self._body(messages, max_tokens, temperature, top_p, tools, tool_choice, extra), ta,
                                   limit)
                if r.status_code in RETRY_STATUS:
                    last = provider_error(r.status_code, r.content, 200)
                    if r.status_code in BUSY_STATUS and busy_waited < self.cfg.busy_wait_s:
                        ra = r.retry_after()
                        wait = min(60.0, ra if ra is not None else 2.0 * min(attempt, 5),
                                   self.cfg.busy_wait_s - busy_waited)
                elif r.status_code != 200:
                    raise PolicyError(provider_error(r.status_code, r.content))
                else:
                    return self._record(r.json(), time.perf_counter() - t0, time.perf_counter() - ta, attempt)
            except (httpx.TimeoutException, _Deadline):
                last = f"timeout after {limit:.0f} s"
            except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadError) as e:
                last = f"{type(e).__name__}: {str(e)[:200]}"
            finally:
                if self.sem is not None and acquired:
                    self.sem.release()
            if wait is not None:                           # busy or starting: wait it out, not counted as a failure
                wait = min(wait, max(0.0, self._limit()))
                time.sleep(wait)
                busy_waited += wait
                continue
            failures += 1
            if failures > self.cfg.retries:
                break
            time.sleep(min(2.0, 0.5 * failures, max(0.0, self._limit())))
        raise PolicyError(f"policy call failed after {attempt} attempts: {last}")

    def _limit(self) -> float:
        """This attempt's timeout: timeout_s, or the time left before the deadline if that is less."""
        if self.deadline is None:
            return self.cfg.timeout_s
        return min(self.cfg.timeout_s, self.deadline - time.monotonic())

    def _post(self, body: dict, t_start: float, limit: float | None = None) -> _Resp:
        """POST and read the response, giving up `limit` (default timeout_s) after t_start. httpx's own timeouts bound
        each wait for bytes, not the whole response: a hosted API that sends keep-alive bytes while it generates
        (OpenRouter does) would otherwise hold a call open for as long as the generation runs (818 s for one
        32,768-token reply)."""
        limit = self.cfg.timeout_s if limit is None else limit
        kw = {} if limit >= self.cfg.timeout_s else {"timeout": httpx.Timeout(limit, connect=min(10.0, limit))}
        buf = bytearray()
        with self.http.stream("POST", self.url, json=body, **kw) as r:
            for chunk in r.iter_bytes():
                buf += chunk
                if time.perf_counter() - t_start > limit:
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
