"""Run the OS-dependent tests from the public PR and its baseline, retaining every result."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "local-shots"
OUT.mkdir(exist_ok=True)
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "pytest>=8"], check=True)
tests = ["tests/test_rl.py::test_collect_without_the_main_guard_raises_and_says_so"]
if sys.platform == "darwin":
    tests.append("tests/test_background_live.py")
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
        xml = OUT / f"{arm}.xml"
        with (OUT / f"{arm}.log").open("w",encoding="utf-8") as log:
            result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-rs", "--tb=short",
                                     "--junitxml", str(xml), *tests], cwd=repo, env=env,
                                    stdout=log, stderr=subprocess.STDOUT, timeout=450)
        rows = []
        for case in ET.parse(xml).getroot().iter("testcase"):
            outcome = next((e for e in case if e.tag in ("skipped", "failure", "error")), None)
            rows.append({"case": case.get("name"), "outcome": "pass" if outcome is None else outcome.tag,
                         "reason": "" if outcome is None else outcome.get("message", "")[:350]})
        record = {"arm": arm, "sha": sha, "exit": result.returncode,"cases": rows}
        print(json.dumps(record),flush=True)
        results.append(record)
        (OUT/"tests.json").write_text(json.dumps(results,indent=2))
if any(r["exit"] or not r["cases"] or any(c["outcome"] != "pass" for c in r["cases"]) for r in results):
    raise SystemExit("At least one OS-dependent test failed or skipped. See per-case evidence.")
