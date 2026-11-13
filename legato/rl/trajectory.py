"""Trajectory files for RL rollouts (schema legato.rollout/v0) and their export for training.

Layout of an output directory:
    rollouts.jsonl       one record per rollout (see docs/rl.md)
    images/<sha>.png     every image the policy saw, stored once (requests refer to them as legato-image://<sha>)
    messages/<sha>.json  every chat message, stored once; a step's request is the list of its message hashes
    progress.json        heartbeat while collecting
    summary.json         profile of the batch (legato.rl.profile)

A step's exact prompt is stored as a list of message hashes, and images inside messages as content-addressed
references, so a rollout costs O(steps) storage instead of repeating the whole history (system prompt, earlier
replies, screenshots) at every step. `load_request` rebuilds the exact messages; `inline_images=True` puts the
images back as data URLs for a trainer that needs raw requests.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Iterable

SCHEMA = "legato.rollout/v0"
IMG_PREFIX = "legato-image://"
DATA_URL = re.compile(r"^data:image/[a-zA-Z]+;base64,")


def store_images(messages: list, images_dir: Path) -> tuple[list, int]:
    """A copy of `messages` with each data-URL image written to images_dir/<sha>.png and replaced by a reference."""
    out = copy.deepcopy(messages)
    n = 0
    images_dir.mkdir(parents=True, exist_ok=True)
    for m in out:
        content = m.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if part.get("type") != "image_url":
                continue
            url = (part.get("image_url") or {}).get("url", "")
            if DATA_URL.match(url):
                raw = base64.b64decode(url.split(",", 1)[1])
                sha = hashlib.sha256(raw).hexdigest()[:32]
                p = images_dir / f"{sha}.png"
                if not p.exists():
                    p.write_bytes(raw)
                part["image_url"]["url"] = IMG_PREFIX + sha
                n += 1
    return out, n


def load_messages(messages: list, images_dir: Path) -> list:
    """The inverse of store_images: references back to data URLs."""
    out = copy.deepcopy(messages)
    for m in out:
        content = m.get("content")
        if isinstance(content, list):
            for part in content:
                url = (part.get("image_url") or {}).get("url", "") if part.get("type") == "image_url" else ""
                if url.startswith(IMG_PREFIX):
                    b = (images_dir / f"{url[len(IMG_PREFIX):]}.png").read_bytes()
                    part["image_url"]["url"] = "data:image/png;base64," + base64.b64encode(b).decode()
    return out


def store_request(messages: list, out_dir: Path) -> tuple[list[str], int]:
    """Store a request's messages (images first, then each message once) and return their hashes in order."""
    msgs, n_img = store_images(messages, out_dir / "images")
    (out_dir / "messages").mkdir(parents=True, exist_ok=True)
    shas = []
    for m in msgs:
        blob = json.dumps(m, sort_keys=True, separators=(",", ":"))
        sha = hashlib.sha256(blob.encode()).hexdigest()[:24]
        p = out_dir / "messages" / f"{sha}.json"
        if not p.exists():
            p.write_text(blob)
        shas.append(sha)
    return shas, n_img


def load_request(shas: list[str], out_dir: str | Path, inline_images: bool = False) -> list:
    out_dir = Path(out_dir)
    msgs = [json.loads((out_dir / "messages" / f"{h}.json").read_text()) for h in shas]
    return load_messages(msgs, out_dir / "images") if inline_images else msgs


def read_rollouts(out_dir: str | Path) -> list[dict]:
    p = Path(out_dir) / "rollouts.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []


def group_advantages(rollouts: Iterable[dict], eps: float = 1e-6) -> dict:
    """GRPO-style advantage per rollout: (reward - group mean) / (group std + eps), over the completed rollouts of
    each group. Groups with one rollout, or with identical rewards, get 0 (no learning signal)."""
    groups: dict = {}
    for r in rollouts:
        if r.get("status") in ("ok", "truncated"):
            groups.setdefault(r["group_id"], []).append(r)
    adv = {}
    for rs in groups.values():
        xs = [float(r.get("reward") or 0.0) for r in rs]
        mu = sum(xs) / len(xs)
        sd = math.sqrt(sum((x - mu) ** 2 for x in xs) / len(xs))
        for r, x in zip(rs, xs):
            adv[r["rollout_id"]] = 0.0 if len(xs) < 2 or sd < eps else (x - mu) / (sd + eps)
    return adv


def export_steps(out_dir: str | Path, dest: str | Path, inline_images: bool = False) -> dict:
    """One training sample per policy step: the exact request (messages), the sampled response (text, token ids,
    logprobs), the rollout's reward and group advantage, and the policy version that produced it. Cancelled,
    skipped and failed rollouts are left out. Returns counts."""
    out_dir = Path(out_dir)
    rs = read_rollouts(out_dir)
    adv = group_advantages(rs)
    n_steps = n_roll = 0
    with open(dest, "w") as f:
        for r in rs:
            if r["rollout_id"] not in adv:
                continue
            n_roll += 1
            for s in r.get("steps", []):
                resp = s.get("response") or {}
                if not s.get("request"):
                    continue
                msgs = load_request(s["request"]["messages"], out_dir, inline_images=inline_images)
                f.write(json.dumps({
                    "rollout_id": r["rollout_id"], "group_id": r["group_id"], "task_id": r["task_id"], "step": s["i"],
                    "policy_version": r["policy"]["version"], "model": r["policy"]["model"],
                    "messages": msgs, "sampling": {k: s["request"].get(k) for k in ("max_tokens", "temperature", "top_p")},
                    "response_text": resp.get("content"), "response_reasoning": resp.get("reasoning"),
                    "response_token_ids": resp.get("token_ids"), "response_logprobs": resp.get("logprobs"),
                    "response_tokens": resp.get("tokens"),
                    "reward": r.get("reward"), "advantage": adv[r["rollout_id"]],
                }) + "\n")
                n_steps += 1
    return {"rollouts": n_roll, "steps": n_steps, "dest": str(dest)}
