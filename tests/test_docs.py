"""The docs agree with the code. Coding agents copy these files verbatim (llms.txt, the skills, the docs), so a
renamed flag or a stale snippet breaks real setups. Checked in README, llms.txt, docs/, skills/, examples/README and
the install manifests:
- every `legato ...` command uses flags that command has, and a --sandbox it accepts;
- every `pip install "legatolabs[...]"` names extras that exist;
- every Python block parses, and every `legato.X` it uses exists, called with keywords it accepts.
"""
from __future__ import annotations

import ast
import inspect
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import legato  # noqa: E402
from legato import providers  # noqa: E402

TEXT = sorted({ROOT / "README.md", ROOT / "llms.txt", ROOT / "AGENTS.md", ROOT / "examples" / "README.md",
               *ROOT.glob("docs/**/*.md"), *ROOT.glob("skills/*/SKILL.md")})
MANIFESTS = [ROOT / p for p in ("mcp.json", "plugin.json", "gemini-extension.json", "server.json",
                                ".claude-plugin/plugin.json", "mcpb/manifest.json") if (ROOT / p).exists()]
SUBCOMMANDS = {"rl": ("probe", "collect", "export", "profile"), "bench": ("office", "h2h"),
               "office": ("generate", "report")}


@pytest.fixture(scope="module")
def flags() -> dict:
    from test_surface import cli_flags
    return cli_flags()


def _rel(p: Path) -> str:
    return str(p.relative_to(ROOT))


def _commands() -> list[tuple[str, str]]:
    """(where, 'legato mcp --sandbox docker ...') for every invocation in the docs and manifests."""
    out = []
    for p in TEXT:
        text = p.read_text().replace("\\\n", " ")                        # join shell line continuations
        for m in re.finditer(r"(?<![\w./-])legato (run|mcp|doctor|rl|bench)\b[^\n`|)\"]*", text):
            out.append((_rel(p), m.group(0)))
    for p in MANIFESTS:
        def walk(o):
            if isinstance(o, dict):
                args, command = o.get("args"), o.get("command")
                if isinstance(args, list) and args and all(isinstance(a, str) for a in args):
                    if command in ("legato", "legatolabs"):                     # "command": "legato", "args": [...]
                        out.append((_rel(p), "legato " + " ".join(args)))
                    elif "legato" in args:                                     # uvx --from legatolabs[mcp] legato ...
                        out.append((_rel(p), "legato " + " ".join(args[args.index("legato") + 1:])))
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
        walk(json.loads(p.read_text()))
    return out


def _command_key(words: list[str]) -> str:
    key = [words[0]]
    while key[-1] in SUBCOMMANDS and len(words) > len(key) and words[len(key)] in SUBCOMMANDS[key[-1]]:
        key.append(words[len(key)])
    return " ".join(key)


def test_commands_use_real_flags(flags):
    bad = []
    for where, cmd in _commands():
        words = cmd.split()[1:]
        key = _command_key(words)
        if key not in flags:                                             # e.g. "legato rl" in prose
            continue
        for f in re.findall(r"(?<![\w-])--[a-z][a-z0-9-]*", cmd):
            if f not in flags[key] and f != "--help":
                bad.append(f"{where}: `{cmd.strip()}` uses {f}, which `legato {key}` does not have")
        m = re.search(r"--sandbox[ =]([^\s\"',]+)", cmd)
        if m:
            spec = m.group(1)
            ok = spec in providers.BUILTIN or spec.startswith("docker:") or ":" in spec or spec.startswith("<")
            if not ok:
                bad.append(f"{where}: `--sandbox {spec}` is not a sandbox legato accepts")
    assert not bad, "\n".join(bad)


def test_install_extras_exist():
    from importlib.metadata import metadata
    extras = set(metadata("legatolabs").get_all("Provides-Extra") or [])
    assert {"mcp", "local", "e2b"} <= extras, f"cannot read the package's extras: {extras}"
    bad = []
    for p in TEXT + MANIFESTS:
        for m in re.finditer(r"legatolabs\[([a-z0-9,\s-]+)\]", p.read_text()):
            for e in (x.strip() for x in m.group(1).split(",")):
                if e not in extras:
                    bad.append(f"{_rel(p)}: legatolabs[{m.group(1)}] names an extra that does not exist: {e}")
    assert not bad, "\n".join(bad)


def _python_blocks():
    for p in TEXT:
        for m in re.finditer(r"```python\n(.*?)```", p.read_text(), re.S):
            yield _rel(p), m.group(1)


def _resolve(node: ast.AST, names: dict):
    """The legato object an expression like `legato.providers.docker` refers to, or None."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name) or node.id not in names:
        return None, None
    obj, path = names[node.id], node.id
    for a in reversed(parts):
        if not hasattr(obj, a):
            return KeyError, f"{path}.{a}"
        obj, path = getattr(obj, a), f"{path}.{a}"
    return obj, path


def test_python_snippets_use_the_real_api():
    bad = []
    for where, code in _python_blocks():
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            bad.append(f"{where}: a python block does not parse ({e.msg}, line {e.lineno}): {code[:60]!r}")
            continue
        names: dict = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name == "legato" or a.name.startswith("legato."):
                        names[(a.asname or a.name).split(".")[0]] = legato
            elif isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "legato":
                import importlib
                try:
                    mod = importlib.import_module(node.module)
                except ImportError as e:
                    bad.append(f"{where}: `from {node.module} import ...` fails: {e}")
                    continue
                for a in node.names:
                    if not hasattr(mod, a.name):
                        bad.append(f"{where}: {node.module} has no {a.name}")
                    else:
                        names[a.asname or a.name] = getattr(mod, a.name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                obj, path = _resolve(node, names)
                if obj is KeyError:
                    bad.append(f"{where}: {path} does not exist")
            if isinstance(node, ast.Call):
                obj, path = _resolve(node.func, names)
                if obj in (None, KeyError) or not callable(obj):
                    continue
                try:
                    params = inspect.signature(obj).parameters
                except (TypeError, ValueError):
                    continue
                if any(p.kind is p.VAR_KEYWORD for p in params.values()):
                    continue
                for kw in node.keywords:
                    if kw.arg and kw.arg not in params:
                        bad.append(f"{where}: {path}() has no keyword {kw.arg}=")
    assert not bad, "\n".join(sorted(set(bad)))


def test_the_checks_see_the_docs():
    """Guards the guards: if the patterns stop matching, the tests above would pass on nothing."""
    cmds = _commands()
    assert len(cmds) > 20 and any("--sandbox docker" in c for _, c in cmds)
    assert len(list(_python_blocks())) > 5
