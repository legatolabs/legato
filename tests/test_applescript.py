"""legato.tools.applescript: compiled before it runs, the dictionary with any error, a read-back, a cap."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from legato.tools import applescript, dictionary_summary  # noqa: E402

SDEF = """<dictionary><suite name="Standard Suite"><command name="make"/><class name="item"/></suite>
<suite name="Reminders Suite"><class name="list"><property name="name"/><element type="reminder"/></class>
<class name="reminder"><property name="name"/><property name="completed"/></class>
<command name="show"><parameter name="in"/></command></suite></dictionary>"""


class Desk:
    """A fake macOS shell: osacompile fails on 'reminder list', osascript prints what the script returns."""
    def __init__(self):
        self.cmds = []

    def shell(self, cmd, timeout_s):
        self.cmds.append(cmd)
        if cmd.startswith("cat > "):                  # remember each file's script, as the real shell would
            path = cmd.split()[2]
            self.files = getattr(self, "files", {})
            self.files[path] = cmd.split("<<'LEGATO_AS_EOF'\n", 1)[1].split("\nLEGATO_AS_EOF", 1)[0]
        if cmd.startswith("osascript "):
            cmd = cmd + " " + self.files.get(cmd.split()[1].removesuffix(".scpt"), "")
        if cmd.startswith("for p in"):
            return SDEF + "\n[exit 0]"
        if "osacompile" in cmd:
            return ("syntax error: Expected class name but found identifier. (-2741)\n[exit 1]"
                    if "reminder list" in cmd else "[exit 0]")
        if "osascript" in cmd:
            if "-check" in cmd:
                return "Book venue, Send invites\n[exit 0]"
            return "list id x-apple\n[exit 0]" if "boom" not in cmd else "execution error: Can't get list (-1728)\n[exit 1]"
        return "[exit 0]"


def test_a_script_that_does_not_compile_never_runs_and_comes_back_with_the_dictionary():
    d = Desk()
    t = applescript(d)
    out = t.handler({"script": 'tell application "Reminders" to make new reminder list with properties {name:"L"}'})
    assert "did not compile, so nothing ran" in out and "Expected class name" in out
    assert "Reminders's dictionary:" in out and "class list (properties: name; elements: reminder)" in out
    assert not any(c.startswith("osascript") for c in d.cmds)


def test_a_good_script_runs_and_its_check_reads_back():
    d = Desk()
    out = applescript(d).handler({"script": 'tell application "Reminders" to make new list with properties {name:"L"}',
                                  "check": 'tell application "Reminders" to get name of reminders of list "L"'})
    assert out.startswith("ran (exit 0)") and "list id x-apple" in out and "check (exit 0):\nBook venue" in out


def test_after_two_failures_an_app_is_refused_and_others_still_run():
    d = Desk()
    t = applescript(d)
    bad = 'tell application "Reminders" to make new reminder list'
    t.handler({"script": bad})
    t.handler({"script": 'tell application "Reminders" to boom'})       # compiles, fails when it runs
    out = t.handler({"script": 'tell application "Reminders" to get name of lists'})
    assert out.startswith("not run: AppleScript for Reminders already failed 2 times")
    assert applescript(d).handler({"script": 'tell application "Reminders" to get name of lists'}).startswith("ran")
    assert t.handler({"script": 'tell application "Notes" to get name of notes'}).startswith("ran")


def test_the_summary_keeps_the_apps_own_suites():
    s = dictionary_summary(SDEF)
    assert "class list" in s and "command show (in)" in s and "class item" not in s and "command make" not in s
    assert dictionary_summary("not xml") == ""
