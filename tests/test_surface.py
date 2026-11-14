"""Legato's public surface, checked against tests/snapshots/surface.json: the Python API, every CLI flag and the MCP
tools. A PR that removes or renames any of them fails here. To change the surface on purpose, regenerate the
snapshot and commit it with the change, so the diff shows exactly what changed:

    LEGATO_UPDATE_SNAPSHOTS=1 pytest -q tests/test_surface.py
"""
from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import inspect
import io
import json
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import legato  # noqa: E402

SNAPSHOT = ROOT / "tests" / "snapshots" / "surface.json"

# every command a user can type; `legato doctor` reads its one flag by hand
COMMANDS = [["run"], ["mcp"], ["rl", "probe"], ["rl", "collect"], ["rl", "export"], ["rl", "profile"],
            ["bench", "office", "generate"], ["bench", "office", "report"], ["bench", "h2h"]]
HAND_PARSED = {"doctor": ["--json"]}
FLAG = re.compile(r"(?<![\w-])--[a-z][a-z0-9-]*")


def _default(p: inspect.Parameter) -> str | None:
    if p.default is inspect.Parameter.empty:
        return None
    d = p.default
    if isinstance(d, (str, int, float, bool, type(None), tuple)):
        return repr(d)
    return "<factory>" if "DEFAULT_FACTORY" in type(d).__name__ else f"<{type(d).__name__}>"


def _sig(fn) -> list[str]:
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return ["?"]
    out = []
    for p in sig.parameters.values():
        if p.name in ("self", "cls"):
            continue
        d = _default(p)
        out.append(f"{p.kind.name.lower()} {p.name}" + (f"={d}" if d is not None else ""))
    return out


def _class(cls) -> dict:
    out: dict = {"init": _sig(cls.__init__) if "__init__" in cls.__dict__ else []}
    if dataclasses.is_dataclass(cls):
        out["fields"] = [f.name for f in dataclasses.fields(cls)]
    for name, member in sorted(vars(cls).items()):
        if name.startswith("_"):
            continue
        if isinstance(member, (staticmethod, classmethod)):
            member = member.__func__
        if inspect.isfunction(member):
            out[name] = _sig(member)
        elif isinstance(member, property):
            out[name] = "property"
    return out


def _module(mod) -> dict:
    names = getattr(mod, "__all__", None) or [n for n, v in vars(mod).items()
                                               if not n.startswith("_") and inspect.isfunction(v)
                                               and v.__module__ == mod.__name__]
    return {n: _sig(getattr(mod, n)) for n in sorted(names) if callable(getattr(mod, n))}


def python_api() -> dict:
    out = {}
    for name in sorted(legato.__all__):
        obj = getattr(legato, name)
        if inspect.ismodule(obj):
            out[name] = _module(obj)
        elif inspect.isclass(obj):
            out[name] = _class(obj)
        else:
            out[name] = _sig(obj)
    return out


def cli_flags() -> dict:
    from legato.__main__ import main
    out = {}
    for cmd in COMMANDS:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()), \
                contextlib.suppress(SystemExit):
            main([*cmd, "--help"])
        flags = sorted(set(FLAG.findall(buf.getvalue())) - {"--help"})
        assert buf.getvalue(), f"`legato {' '.join(cmd)} --help` printed nothing"
        out[" ".join(cmd)] = flags
    out.update(HAND_PARSED)
    return out


def mcp_tools() -> dict:
    pytest.importorskip("mcp")
    from legato.mcp_server import build_server
    srv = build_server(lambda res: (None, None))
    tools = asyncio.run(srv.list_tools())
    out = {}
    for t in tools:
        schema = getattr(t, "input_schema", None) or getattr(t, "inputSchema", None) or {}   # mcp 2.x / 1.x
        out[t.name] = {"params": sorted(schema.get("properties", {})), "required": sorted(schema.get("required", []))}
    return out


def surface() -> dict:
    return {"python": python_api(), "cli": cli_flags(), "mcp": mcp_tools()}


def _diff(old, new, path="") -> list[str]:
    if isinstance(old, dict) and isinstance(new, dict):
        out = []
        for k in sorted(set(old) | set(new)):
            p = f"{path}.{k}" if path else k
            if k not in new:
                out.append(f"removed: {p}")
            elif k not in old:
                out.append(f"added:   {p}")
            else:
                out += _diff(old[k], new[k], p)
        return out
    if old != new:
        if isinstance(old, list) and isinstance(new, list):
            gone, came = [x for x in old if x not in new], [x for x in new if x not in old]
            return [f"changed: {path}" + (f" (removed {gone})" if gone else "") + (f" (added {came})" if came else "")]
        return [f"changed: {path}: {old!r} -> {new!r}"]
    return []


def test_public_surface_matches_snapshot():
    now = surface()
    if os.environ.get("LEGATO_UPDATE_SNAPSHOTS"):
        SNAPSHOT.parent.mkdir(exist_ok=True)
        SNAPSHOT.write_text(json.dumps(now, indent=1, sort_keys=True) + "\n")
    old = json.loads(SNAPSHOT.read_text())
    changes = _diff(old, now)
    assert not changes, ("The public surface changed:\n  " + "\n  ".join(changes) + "\nIf that is intended, run "
                         "LEGATO_UPDATE_SNAPSHOTS=1 pytest -q tests/test_surface.py and commit the snapshot; removals "
                         "also need a CHANGELOG entry.")


def test_doctor_json_flag():
    from legato.doctor import main
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        main(["--json"])
    assert "legato" in json.loads(buf.getvalue())
