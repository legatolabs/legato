"""The optional hosted API (legato.computer): a stateful session per task, and `run` for the whole loop. The model runs
on Legato's servers and needs an `lg_` API key; the open harness (`legato.Harness`) needs neither.

    from legato import hosted
    agent = hosted.Agent(task="Add a Total row", api_key="lg_...")
    action = agent.step(png_bytes)            # the server keeps the history warm; you send only the new screen

    result = hosted.run("Add a Total row", e2b_sandbox, api_key="lg_...")
"""
from __future__ import annotations

import base64
import os
import time
from dataclasses import dataclass, field

import httpx

from legato.actions import Action
from legato.env import Env, attach, png_size

DEFAULT_BASE = "https://legato.computer/api"
MODEL = "legato/qwen3.8-27b-cu"


class LegatoError(RuntimeError):
    pass


def _client(base_url: str | None, api_key: str | None, timeout: float) -> tuple[httpx.Client, str]:
    key = api_key or os.environ.get("LEGATO_API_KEY")
    if not key:
        raise LegatoError("no API key: pass api_key= or set LEGATO_API_KEY")
    base = (base_url or os.environ.get("LEGATO_BASE_URL") or DEFAULT_BASE).rstrip("/")
    return httpx.Client(base_url=base, headers={"Authorization": f"Bearer {key}"},
                        timeout=httpx.Timeout(timeout, connect=15), http2=False), base


def _check(r: httpx.Response) -> dict:
    try:
        d = r.json()
    except ValueError:
        raise LegatoError(f"HTTP {r.status_code}: {r.text[:200]}") from None
    if r.status_code >= 400:
        e = d.get("error") if isinstance(d, dict) else None
        msg = e.get("message") if isinstance(e, dict) else str(d)[:200]
        raise LegatoError(f"HTTP {r.status_code}: {msg}")
    return d


class Agent:
    """One task = one server-side session. The server keeps the model's view of the history on the GPU and prepares
    the next request while your screen settles."""

    def __init__(self, task: str, api_key: str | None = None, base_url: str | None = None, max_steps: int = 50,
                 timeout: float = 120.0):
        self.task, self.max_steps = task, max_steps
        self.http, self.base = _client(base_url, api_key, timeout)
        self.session_id: str | None = None
        self.steps = 0
        self.model_s: list[float] = []

    def _open(self, width: int, height: int) -> None:
        d = _check(self.http.post("/v1/sessions", json={"task": self.task, "screen": {"width": width, "height": height},
                                                         "max_steps": self.max_steps}))
        self.session_id = d["session_id"]

    def step(self, screenshot: bytes, result: str | None = None) -> Action:
        if self.session_id is None:
            self._open(*png_size(screenshot))
        body = {"screenshot": base64.b64encode(screenshot).decode()}
        if result is not None:
            body["result"] = result
        d = _check(self.http.post(f"/v1/sessions/{self.session_id}/step", json=body))
        self.steps = d.get("step", self.steps + 1)
        self.model_s.append(float((d.get("timing") or {}).get("model_s") or 0.0))
        return Action.from_dict(d["action"])

    def close(self) -> None:
        if self.session_id:
            try:
                self.http.delete(f"/v1/sessions/{self.session_id}")
            except httpx.HTTPError:
                pass
            self.session_id = None
        self.http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


@dataclass
class Result:
    done: bool
    steps: int
    wall_s: float
    actions: list[str] = field(default_factory=list)
    timings: list[dict] = field(default_factory=list)

    @property
    def mean_step_s(self) -> float:
        return self.wall_s / self.steps if self.steps else 0.0


def run(task: str, sandbox=None, *, env: Env | None = None, api_key: str | None = None, base_url: str | None = None,
        max_steps: int = 30, on_step=None) -> Result:
    """Run a task to completion on an attached sandbox. on_step(i, action, timing) is called after each step."""
    env = env or attach(sandbox)
    t0 = time.monotonic()
    res = Result(False, 0, 0.0)
    with Agent(task, api_key=api_key, base_url=base_url, max_steps=max_steps) as agent:
        obs = env.observe()
        for i in range(max_steps):
            t = time.monotonic()
            a = agent.step(obs.screenshot)
            model_s = time.monotonic() - t
            res.actions.append(a.describe())
            if a.done:
                res.done = a.type == "done"
                res.timings.append({"model_s": model_s})
                res.steps = i + 1
                break
            obs = env.act(a)
            timing = {"model_s": model_s, **env.last}
            res.timings.append(timing)
            res.steps = i + 1
            if on_step:
                on_step(i, a, timing)
    res.wall_s = time.monotonic() - t0
    return res
