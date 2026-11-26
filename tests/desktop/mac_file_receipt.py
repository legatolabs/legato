"""Check a candidate or baseline on an explicitly named disposable TextEdit document."""
from pathlib import Path
import subprocess
import sys
import time

import legato
from legato.background import BackgroundEnv


def verify(folder):
    path = Path(folder) / "Legato owned text receipt.txt"
    path.write_text("alpha beta")
    subprocess.run(["open", "-a", "TextEdit", str(path)], check=True, timeout=10)
    env = BackgroundEnv("TextEdit", window="attached", confirm=None)
    try:
        end = time.monotonic() + 12
        while True:
            window = env.window()
            if path.stem in window.get("name", "") and "alpha beta" in env.read():
                break
            if time.monotonic() >= end:
                raise RuntimeError("The attached window was not the owned test file")
            time.sleep(.2)
        # Keep a different known window in front while exercising background editing.
        subprocess.run(["open", "-a", "Preview", str(Path(folder).parent / "witness.png")], check=True, timeout=10)
        from AppKit import NSWorkspace
        def front():
            return str(NSWorkspace.sharedWorkspace().frontmostApplication().bundleIdentifier())
        until = time.monotonic() + 8
        while front() != "com.apple.Preview" and time.monotonic() < until:
            time.sleep(.1)
        assert front() == "com.apple.Preview"
        env.act(legato.Action("key", keys="super+Down"))
        env.act(legato.Action("type", text=" gamma"))
        env.act(legato.Action("key", keys="super+s"))
        end = time.monotonic()+8
        while time.monotonic()<end and path.read_text() != "alpha beta gamma":
            time.sleep(.1)
        contents = path.read_text()
        record = {"file_changed":contents=="alpha beta gamma","front_preserved":front()=="com.apple.Preview"}
        assert all(record.values()), record
        return record
    finally:
        env.close()


if __name__ == "__main__":
    import json
    print(json.dumps(verify(sys.argv[1])))
