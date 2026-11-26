"""Run the four public TextEdit test bodies on disposable files using native AX for independent read-back."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time

from AppKit import NSWorkspace
from legato import Action, safety
from legato.background import BackgroundEnv


def main(repo, witness, output):
    spec = importlib.util.spec_from_file_location("native_tests", repo / "tests/test_background_live.py")
    tests = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tests)

    def front():
        return str(NSWorkspace.sharedWorkspace().frontmostApplication().bundleIdentifier())

    records = []
    for index, name in enumerate(("test_textedit_typing_lands_in_the_background",
                                  "test_textedit_click_places_the_insertion_point",
                                  "test_textedit_double_click_selects_a_word_and_shortcuts_work",
                                  "test_textedit_drag_is_not_run")):
        path = repo / f"Legato owned receipt {index}.txt"
        path.write_text("alpha beta")
        env = None
        record = {"test": name}
        try:
            subprocess.run(["open", "-a", "TextEdit", str(path)], check=True, timeout=10)
            env = BackgroundEnv("TextEdit", window="attached", confirm=None)
            end = time.monotonic() + 10
            while path.stem not in env.window().get("name", "") or "alpha beta" not in env.read():
                if time.monotonic() >= end:
                    raise RuntimeError("The attached target did not match the disposable document")
                time.sleep(.2)
            def text():
                element=tests.find(env._ax_window(),lambda e,role: role=="AXTextArea")
                return tests.text_of(element, "AXValue")
            tests.document_text = text
            subprocess.run(["open","-a","Preview",str(witness)],check=True,timeout=10)
            end=time.monotonic()+8
            while front()!="com.apple.Preview" and time.monotonic()<end:
                time.sleep(.1)
            assert front()=="com.apple.Preview","Preview must own the foreground before the test"
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
                    record["text_via_native_AX"]=text()
                except Exception as exc:
                    record["read_error"]=str(exc)
                record["front_preserved"]=front()=="com.apple.Preview"
                env.close()
        records.append(record)
        print(json.dumps(record),flush=True)
        output.write_text(json.dumps(records,indent=2))
    assert all(r.get("passed") and r.get("front_preserved") for r in records)


if __name__=="__main__":
    main(*(Path(v) for v in sys.argv[1:]))
