"""Every place that names the release version agrees with pyproject.toml."""
import json
import re
from pathlib import Path

import legato

ROOT = Path(__file__).resolve().parents[1]


def test_versions_agree():
    v = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M).group(1)
    server = json.loads((ROOT / "server.json").read_text())
    assert legato.__version__ == v
    assert server["version"] == server["packages"][0]["version"] == v
    assert json.loads((ROOT / "gemini-extension.json").read_text())["version"] == v
    assert json.loads((ROOT / "mcpb/manifest.json").read_text())["version"] == v
