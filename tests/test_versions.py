"""Every place that names the release version agrees with pyproject.toml."""
import json
import re
from pathlib import Path

import legato

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "legato"


def test_versions_agree():
    v = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M).group(1)
    server = json.loads((ROOT / "server.json").read_text())
    assert legato.__version__ == v
    assert server["version"] == server["packages"][0]["version"] == v
    assert json.loads((ROOT / "gemini-extension.json").read_text())["version"] == v
    assert json.loads((ROOT / "mcpb/manifest.json").read_text())["version"] == v
    assert json.loads((ROOT / ".cursor-plugin/plugin.json").read_text())["version"] == v
    # The Claude Code plugin pins the package it runs (the plugin directory refuses an unpinned uvx), so a new
    # release raises the plugin's version and its pin together.
    assert json.loads((PLUGIN / ".claude-plugin/plugin.json").read_text())["version"] == v
    args = json.loads((PLUGIN / ".mcp.json").read_text())["mcpServers"]["legato"]["args"]
    assert args[:2] == ["--from", f"legatolabs[mcp]=={v}"]


def test_plugin_copies_match_the_root():
    """The plugin folder is installed on its own, so it carries copies of the root skills and LICENSE."""
    root_skills = sorted(p.relative_to(ROOT / "skills") for p in (ROOT / "skills").rglob("*") if p.is_file())
    plugin_skills = sorted(p.relative_to(PLUGIN / "skills") for p in (PLUGIN / "skills").rglob("*") if p.is_file())
    assert plugin_skills == root_skills
    for rel in root_skills:
        assert (PLUGIN / "skills" / rel).read_bytes() == (ROOT / "skills" / rel).read_bytes(), rel
    assert (PLUGIN / "LICENSE").read_bytes() == (ROOT / "LICENSE").read_bytes()
