"""Check the records and screenshots from a Mac app test session."""
import json
from pathlib import Path
import sys


def check_history(runs: Path, since: int) -> bool:
    checked = 0
    ok = True
    for directory in sorted(runs.glob("*/")):
        if not directory.is_dir() or int(directory.stat().st_mtime) < since:
            continue
        checked += 1
        record = json.loads((directory / "run.json").read_text())
        turns = record["turns"]
        shots = []
        if turns or record.get("first"):
            shots.append(("initial screen", record.get("first")))
        for turn in turns:
            # Older engines did not record whether a fresh frame was available.
            acted = any(a not in ("done", "fail") for a in turn.get("actions", []))
            if turn.get("shot") or turn.get("shot_expected", acted):
                shots.append((f"turn {turn['turn']}", turn.get("shot")))
        for label, name in shots:
            if not name or not (directory / name).is_file():
                print(f"{directory.name}: missing screenshot for {label}")
                ok = False
    print(f"{checked} runs since start")
    return checked > 0 and ok


if __name__ == "__main__":
    sys.exit(0 if check_history(Path(sys.argv[1]), int(sys.argv[2])) else 1)
