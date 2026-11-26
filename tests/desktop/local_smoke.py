"""Run the OS-dependent tests from the public PR and its baseline, retaining every result."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "local-shots"
OUT.mkdir(exist_ok=True)
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "pytest>=8"], check=True)
tests = ["tests/test_rl.py::test_collect_without_the_main_guard_raises_and_says_so"]
if sys.platform == "darwin":
    tests.append("tests/test_background_live.py")
if sys.platform != "darwin":
    # Windows already passed the original spawn test on both revisions.
    raise SystemExit(0)
results = []
with tempfile.TemporaryDirectory() as tmp:
    for arm, sha in (("candidate", "f7965a9d755b9a18aa07e310e1d3b0baff9a25af"),
                     ("baseline", "9451328418d89659f9c511790d7b11713eb0c83d")):
        repo = Path(tmp) / arm
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "fetch", "--quiet", "--depth=1",
                        "https://github.com/legatolabs/legato.git", sha], check=True)
        subprocess.run(["git", "-C", str(repo), "checkout", "--quiet", "--detach", "FETCH_HEAD"], check=True)
        env = {**os.environ, "PYTHONPATH": str(repo), "LEGATO_TEST_BACKGROUND": "1", "PYTHONUTF8": "1"}
        from PIL import Image
        witness = Path(tmp) / "witness.png"
        Image.new("RGB", (480, 320), (95, 126, 153)).save(witness)
        dest = OUT / f"{arm}-textedit.json"
        probe = subprocess.run([sys.executable, str(ROOT / "tests/desktop/mac_file_receipt.py"),
                                str(repo), str(witness), str(dest)], cwd=repo, env=env,
                               capture_output=True, text=True, timeout=220)
        (OUT / f"{arm}-textedit.log").write_text(probe.stdout+probe.stderr)
        record={"arm":arm, "sha":sha, "exit":probe.returncode, "cases":json.loads(dest.read_text()) if dest.is_file() else []}
        results.append(record)
        print(json.dumps(record),flush=True)
        (OUT/"tests.json").write_text(json.dumps(results,indent=2))
if any(r["exit"] or len(r["cases"]) != 4 for r in results):
    raise SystemExit("See per-case TextEdit results.")
