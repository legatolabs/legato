"""Paired native comparison for PR 107. Run on a disposable desktop using the local-desktop workflow."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[2]
BASE = "9451328418d89659f9c511790d7b11713eb0c83d"
CANDIDATE = "f7965a9d755b9a18aa07e310e1d3b0baff9a25af"
OUT = (REPO / "local-shots").resolve()
OUT.mkdir(exist_ok=True)
probe = Path(__file__).with_name("mac_receipt.py" if sys.platform == "darwin" else "windows_wheel.py")
with tempfile.TemporaryDirectory() as tmp:
    roots = {}
    for name, sha in (("baseline", BASE), ("candidate", CANDIDATE)):
        checkout = Path(tmp) / name
        subprocess.run(["git", "init", "-q", str(checkout)], check=True)
        subprocess.run(["git", "-C", str(checkout), "fetch", "--quiet", "--depth=1",
                        "https://github.com/legatolabs/legato.git", sha], check=True)
        subprocess.run(["git", "-C", str(checkout), "checkout", "--quiet", "--detach", "FETCH_HEAD"], check=True)
        actual = subprocess.check_output(["git","-C",str(checkout),"rev-parse","HEAD"],text=True).strip()
        assert actual == sha
        roots[name] = checkout
    summary = []
    for block, arms in enumerate((("baseline","candidate"),("candidate","baseline"),
                                  ("candidate","baseline"),("baseline","candidate"))):
        for arm in arms:
            dest = OUT / f"{block}-{arm}.json"
            env = {**os.environ,"PYTHONPATH":str(roots[arm]),"BENCH_CHECKOUT":str(roots[arm]),
                   "BENCH_ARM":arm,"BENCH_OUT":str(dest)}
            subprocess.run([sys.executable,"-u",str(probe)],cwd=REPO,env=env,check=True,timeout=220)
            data = json.loads(dest.read_text())
            summary.append({"block":block,"arm":arm,**data})
            (OUT/"comparison.json").write_text(json.dumps(summary,indent=2))
    print("Completed native paired comparison for",sys.platform)
