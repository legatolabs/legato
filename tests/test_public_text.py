"""This repo is public: nothing in it names private repos or internal run ids."""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = re.compile(r"legato-research|\bEXP-\d{3,4}\b(?!-\d{8}T)")     # raw benchmark rows keep their run ids


def test_no_private_names_in_tracked_text():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True)
    if out.returncode:
        return                                  # not a git checkout (an sdist): nothing to check
    hits = []
    for name in out.stdout.split("\n"):
        path = ROOT / name
        if not name or name == "tests/test_public_text.py" or not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        hits += [f"{name}:{i}" for i, line in enumerate(text.split("\n"), 1) if PRIVATE.search(line)]
    assert not hits, hits
