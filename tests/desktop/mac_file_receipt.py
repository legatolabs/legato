"""Run the four public TextEdit test bodies on disposable files using native AX for independent read-back."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time

from AppKit import NSRunningApplication, NSApplicationActivateIgnoringOtherApps, NSWorkspace
from legato import Action, safety
from legato.background import BackgroundEnv, MacApp, _pump


def main(repo, witness, output):
    spec = importlib.util.spec_from_file_location("native_tests", repo / "tests/test_background_live.py")
    tests = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tests)

    def front():
        return str(NSWorkspace.sharedWorkspace().frontmostApplication().bundleIdentifier())

    def foreground(bundle, timeout=10):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            _pump(.12)
            if front() == bundle:
                return
            apps = NSRunningApplication.runningApplicationsWithBundleIdentifier_(bundle)
            if apps:
                apps[0].activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
        raise RuntimeError(f"Expected foreground {bundle}, observed {front()}")

    subprocess.run(["open", "-a", "Preview", str(witness)], check=True, timeout=10)
    foreground("com.apple.Preview")
    records = []
    for index, name in enumerate(("test_textedit_typing_lands_in_the_background",
                                  "test_textedit_click_places_the_insertion_point",
                                  "test_textedit_double_click_selects_a_word_and_shortcuts_work",
                                  "test_textedit_drag_is_not_run")):
        path = repo / f"Legato {repo.name} receipt {index}.txt"
        path.write_text("alpha beta")
        env = None
        text = None
        record = {"test": name}
        try:
            subprocess.run(["open", "-a", "TextEdit", str(path)], check=True, timeout=10)
            foreground("com.apple.TextEdit")
            app = MacApp("TextEdit")
            end = time.monotonic() + 15
            while True:
                _pump(.15)
                win = app.attr(app.app, "AXFocusedWindow")
                visible = next((w for w in app.windows() if w["layer"] == 0), None)
                if (win is not None and path.stem in str(app.attr(win, "AXTitle") or "")
                        and visible and path.stem in visible.get("name","")):
                    area = tests.find(win, lambda e, role: role == "AXTextArea")
                    if tests.text_of(area, "AXValue") == "alpha beta":
                        break
                if time.monotonic() >= end:
                    raise RuntimeError("The native foreground document did not match the fresh fixture")
            env = BackgroundEnv("TextEdit", window="attached", confirm=None, mac=app)
            assert path.stem in env.window()["name"]
            assert "alpha beta" in env.read()
            def text():
                element=tests.find(env._ax_window(),lambda e,role: role=="AXTextArea")
                return tests.text_of(element, "AXValue")
            tests.document_text = text
            subprocess.run(["open","-a","Preview",str(witness)],check=True,timeout=10)
            foreground("com.apple.Preview")
            getattr(tests,name)(env)
            record["passed"]=True
            if index==0:
                try:
                    env.act(Action("key",keys="super+s"))
                except safety.Unavailable:
                    record["background_save"]="refused by the runtime"
                else:
                    raise AssertionError("Background Save was unexpectedly accepted")
        except Exception as exc:
            record.update(passed=False,error=f"{type(exc).__name__}: {exc}")
        finally:
            if env is not None:
                try:
                    record["text_via_native_AX"]=text() if text is not None else None
                except Exception as exc:
                    record["read_error"]=str(exc)
                _pump(.15)
                record["front_preserved"]=front()=="com.apple.Preview"
                env.close()
        records.append(record)
        print(json.dumps(record),flush=True)
        output.write_text(json.dumps(records,indent=2))
    assert all(r.get("passed") and r.get("front_preserved") for r in records)


if __name__=="__main__":
    main(*(Path(v) for v in sys.argv[1:]))
