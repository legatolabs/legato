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
        warm_file = Path(tmp) / "Preflight.txt"
        warm_file.write_text("TextEdit is open")
        subprocess.run(["open","-g","-a","TextEdit",str(warm_file)],check=True,timeout=10)
        subprocess.run(["open","-a","Preview",str(witness)],check=True,timeout=10)
        from AppKit import NSWorkspace
        until=time.monotonic()+12
        while str(NSWorkspace.sharedWorkspace().frontmostApplication().bundleIdentifier()) != "com.apple.Preview":
            if time.monotonic()>=until:
                raise RuntimeError("A known foreground Preview window is required for this run")
            time.sleep(.2)
        try:
            check=subprocess.run(["osascript","-e",'tell application "TextEdit" to count documents'],
                                 capture_output=True,text=True,timeout=8)
            preflight="available" if check.returncode==0 else "AppleScript returned an error"
        except subprocess.TimeoutExpired:
            preflight="AppleScript preflight timed out after 8 seconds"
        selected=tests if preflight=="available" else ["tests/test_background_live.py","-k","browser"]
        print("TextEdit preflight",arm,preflight,flush=True)
        xml = OUT / f"{arm}.xml"
        with (OUT / f"{arm}.log").open("w",encoding="utf-8") as log:
            result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-rs", "--tb=short",
                                     "--junitxml", str(xml), *selected], cwd=repo, env=env,
                                    stdout=log, stderr=subprocess.STDOUT, timeout=450)
        rows = []
        for case in ET.parse(xml).getroot().iter("testcase"):
            outcome = next((e for e in case if e.tag in ("skipped", "failure", "error")), None)
            rows.append({"case": case.get("name"), "outcome": "pass" if outcome is None else outcome.tag,
                         "reason": "" if outcome is None else outcome.get("message", "")[:350]})
        extra = subprocess.run([sys.executable, str(ROOT / "tests/desktop/mac_file_receipt.py"), str(repo)],
                               cwd=repo, env=env,capture_output=True,text=True,timeout=90)
        (OUT/f"{arm}-file.log").write_text(extra.stdout+extra.stderr)
        file_result = {"exit":extra.returncode, "output":extra.stdout.strip()[-300:]}
        record = {"arm": arm, "sha": sha, "exit": result.returncode,"cases": rows,"script_preflight":preflight,"file_receipt":file_result}
        print(json.dumps(record),flush=True)
        results.append(record)
        (OUT/"tests.json").write_text(json.dumps(results,indent=2))
if any(r["exit"] or r["file_receipt"]["exit"] or not r["cases"] or any(c["outcome"] != "pass" for c in r["cases"]) for r in results):
    raise SystemExit("At least one OS-dependent test failed or skipped. See per-case evidence.")
