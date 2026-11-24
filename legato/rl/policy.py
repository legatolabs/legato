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

import base64
import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
BUSY_STATUS = {429, 503}               # "come back later": waited out for up to busy_wait_s, honouring Retry-After


PROBE_TOOL = {"type": "function", "function": {
    "name": "click", "description": "Click a point on the screen.",
    "parameters": {"type": "object", "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}},
                   "required": ["x", "y"]}}}


def probe_verdict(p: dict) -> list[str]:
    """What a server can be used for, in words, from PolicyClient.probe()'s result."""
    lines = []
    if p.get("vision", True):
        lines.append("evaluation and data collection: yes")
    else:
        lines.append(f"evaluation and data collection: no, the model did not take an image ({p.get('vision_error')}). "
                     "Every step sends a screenshot: use a model with image input")
    if p["logprobs"]:
        lines.append("policy-gradient training (GRPO): yes, it returns logprobs"
                     + (" and token ids" if p["token_ids"] else "; no token ids, so re-tokenize response_text"))
    else:
        lines.append("policy-gradient training (GRPO): no, it returns no logprobs; use vLLM or another server that "
                     "does (docs/rl.md, \"GRPO with a local vLLM server\")")
    if "tool_calls" in p:
        lines.append("tool calls: yes" if p["tool_calls"] else
                     f"tool calls: no ({p.get('tool_calls_error')}). With --harness legato, ask for actions as JSON "
                     "in the reply instead: \"native_tools\": false in --harness-opts")
    return lines


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
    temperature: float | None = None   # when set, every request samples at this temperature, whatever the harness asks
    seed: int | None = None            # when set, requests carry a seed: this one, then one more per request


class PolicyError(RuntimeError):
    """Every attempt at a model call failed. `cut`: attempts that were sent and cut off before a reply (a timeout,
    a dropped connection), which the provider may still bill."""
    cut: int = 0


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


def _failed(message: str, cut: int) -> PolicyError:
    e = PolicyError(message)
    e.cut = cut
    return e


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
        # where the API key came from, by name ("the key in $OPENROUTER_API_KEY"), never its value: a 401 or 403 says it
        self.key_source = ""
        headers = {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}
        headers.update(attribution_headers(cfg.base_url))
        self.http = httpx.Client(timeout=httpx.Timeout(cfg.timeout_s, connect=min(10.0, cfg.timeout_s)),
                                 headers=headers)
        self.url = cfg.base_url.rstrip("/") + "/chat/completions"
        self.seed = cfg.seed               # the next request's seed (None: the server picks)
        self._seed_lock = threading.Lock()

    def reseed(self, seed: int | None) -> None:
        """Seed the next request with `seed`, and each one after it with one more (None: send no seed)."""
        with self._seed_lock:
            self.seed = seed

    def _take_seed(self) -> int | None:
        with self._seed_lock:
            s = self.seed
            if s is not None:
                self.seed = s + 1
            return s

    def _body(self, messages: list, max_tokens: int | None, temperature: float | None, top_p: float | None,
              tools: list | None = None, tool_choice=None, extra: dict | None = None, seed: int | None = None) -> dict:
        b: dict = {"model": self.cfg.model, "messages": messages, **self.cfg.extra_body, **(extra or {})}
        if self.cfg.temperature is not None:
            temperature = self.cfg.temperature
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
        if seed is not None:
            b["seed"] = seed
        if self.cfg.logprobs:
            b["logprobs"] = True
        if self.cfg.token_ids:
            b["return_token_ids"] = True
        return b

    def chat(self, messages: list, max_tokens: int | None = None, temperature: float | None = None,
             top_p: float | None = None, tools: list | None = None, tool_choice=None, extra: dict | None = None) -> dict:
        """One policy step. `extra` is merged into this request's body. Returns a record: content (what the harness parses), reasoning, token ids and logprobs,
        usage, the sampling actually sent (temperature, top_p, seed), wall seconds, attempts. Raises PolicyError when
        every attempt fails. A retry resends the same seed.

        attempts counts every request sent: a busy server's 429 or 503 is waited out and sent again without using up
        `retries`, so attempts can exceed retries + 1. retry_reasons (when attempts > 1) says why each earlier one was
        sent again."""
        last = ""
        why: list[str] = []
        seed = self._take_seed()
        t0 = time.perf_counter()
        attempt = failures = cut = 0                       # cut: attempts sent and cut off before a reply
        busy_waited = 0.0
        while True:
            limit = self._limit()
            if self.deadline is not None and limit <= 0:   # the run's deadline has passed: no new attempt
                raise _failed("the run's time limit ran out" + (f" ({last})" if last else ""), cut)
            attempt += 1
            wait = None                                    # set when the server asked us to come back later
            acquired = self.sem.acquire(timeout=max(600.0, 10 * self.cfg.timeout_s)) if self.sem is not None else True
            ta = time.perf_counter()
            try:
                body = self._body(messages, max_tokens, temperature, top_p, tools, tool_choice, extra, seed)
                r = self._post(body, ta, limit)
                if r.status_code == 400 and self.cfg.token_ids and "token_ids" in r.text:
                    self.cfg.token_ids = False             # server does not know return_token_ids: drop it, retry
                    body = self._body(messages, max_tokens, temperature, top_p, tools, tool_choice, extra, seed)
                    r = self._post(body, ta, limit)
                if r.status_code in RETRY_STATUS:
                    last = provider_error(r.status_code, r.content, 200)
                    if r.status_code in BUSY_STATUS and busy_waited < self.cfg.busy_wait_s:
                        ra = r.retry_after()
                        wait = min(60.0, ra if ra is not None else 2.0 * min(attempt, 5),
                                   self.cfg.busy_wait_s - busy_waited)
                elif r.status_code != 200:
                    msg = provider_error(r.status_code, r.content)
                    if r.status_code in (401, 403):
                        msg += ("" if msg.endswith((".", "!", "?")) else ".") + self._key_hint(r.status_code)
                    raise _failed(msg, cut)
                else:
                    rec = self._record(r.json(), time.perf_counter() - t0, time.perf_counter() - ta, attempt)
                    rec["sampling"] = {k: body.get(k) for k in ("temperature", "top_p", "seed")}
                    if why:
                        rec["retry_reasons"] = why
                    rec["cut"] = cut
                    return rec
            except (httpx.TimeoutException, _Deadline) as e:
                last = f"timeout after {limit:.0f} s"
                cut += not isinstance(e, (httpx.ConnectTimeout, httpx.PoolTimeout))     # those were never sent
            except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadError) as e:
                last = f"{type(e).__name__}: {str(e)[:200]}"
                cut += not isinstance(e, httpx.ConnectError)
            finally:
                if self.sem is not None and acquired:
                    self.sem.release()
            why.append(last + (" (busy: waited, not counted against retries)" if wait is not None else ""))
            if wait is not None:                           # busy or starting: wait it out, not counted as a failure
                wait = min(wait, max(0.0, self._limit()))
                time.sleep(wait)
                busy_waited += wait
                continue
            failures += 1
            if failures > self.cfg.retries:
                break
            time.sleep(min(2.0, 0.5 * failures, max(0.0, self._limit())))
        hint = (" Is the model server running?" if "ConnectError" in last and any(
            h in self.cfg.base_url for h in ("localhost", "127.0.0.1", "0.0.0.0")) else "")
        raise _failed(f"the model at {self.cfg.base_url} did not answer ({attempt} attempt"
                      f"{'' if attempt == 1 else 's'}): {last}.{hint}", cut)

    def _key_hint(self, status: int) -> str:
        """What to do about a 401 or 403: which key was refused, by where it came from (never the key)."""
        if not self.cfg.api_key:
            return (f" No API key was sent to {self.cfg.base_url}: pass api_key=, export the provider's key (such as "
                    "OPENROUTER_API_KEY), or run legato setup.")
        src = self.key_source or "the API key"
        var = re.search(r"\$(\w+)", src)
        if var:
            fix = f"check it, export a working one as {var.group(1)}, or run legato setup"
        elif "legato setup" in src:
            fix = "run legato setup to save a working one, or export LEGATO_API_KEY"
        elif self.key_source:
            fix = "check the key you passed"
        else:
            fix = "check it, or run legato setup"
        refused = "was refused" if status == 401 else "was refused, or may not use this model"
        return f" {src[0].upper()}{src[1:]} {refused} by {self.cfg.base_url}: {fix}."

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
        """Check the server with five small requests: does it return logprobs, token ids and cached-token counts,
        does the model take an image, and does it answer with a tool call? Raises PolicyError when the plain text
        request fails (a wrong URL, key or model name). `vision_error` and `tool_calls_error` say why those failed."""
        from legato.rl.fake import tiny_png
        msgs = [{"role": "user", "content": "legato probe " + " ".join(f"w{i}" for i in range(300))}]
        a = self.chat(msgs, max_tokens=4, temperature=0.0)
        b = self.chat(msgs, max_tokens=4, temperature=0.0)
        out = {"logprobs": bool(a["logprobs"]), "token_ids": a["token_ids"] is not None and self.cfg.token_ids,
               "cached_tokens_reported": b["usage"]["cached"] is not None, "cached_on_repeat": b["usage"]["cached"],
               "call_s": round(b["s"], 3)}
        img = "data:image/png;base64," + base64.b64encode(tiny_png(1, 64, 64)).decode()
        try:
            self.chat([{"role": "user", "content": [{"type": "image_url", "image_url": {"url": img}},
                                                    {"type": "text", "text": "Reply with the word ok."}]}],
                      max_tokens=16, temperature=0.0)
            out["vision"] = True
        except PolicyError as e:
            out["vision"], out["vision_error"] = False, str(e)
        try:
            t = self.chat([{"role": "user", "content": "Call the click tool at x=10, y=20."}], max_tokens=1024,
                          temperature=0.0, tools=[PROBE_TOOL], tool_choice="auto")
            out["tool_calls"] = bool(t["tool_calls"])
            if not t["tool_calls"]:
                out["tool_calls_error"] = "the model answered without calling the tool it was asked to call"
        except PolicyError as e:
            out["tool_calls"], out["tool_calls_error"] = False, str(e)
        return out

    def close(self) -> None:
        self.http.close()
