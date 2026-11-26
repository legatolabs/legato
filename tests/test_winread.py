"""desktop_read on Windows: the PowerShell walk's output becomes the outline the Mac gives. No Windows needed."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from legato import winread  # noqa: E402


def rows(*r):
    return "\n".join("\t".join(map(str, x)) for x in r) + "\n[exit 0]"


PAGE = rows(("#page", "Counter", "http://localhost:3000/"),
            (0, "Heading", "", "Counter", ""),
            (0, "Group", "", "", ""),
            (1, "Text", "", "Count: 1", ""),
            (1, "Button", "f", "+1", ""),
            (2, "Text", "", "+1", ""),
            (1, "Hyperlink", "", "Docs", "http://localhost:3000/docs"),
            (1, "Edit", "", "Name", "Ada"),
            (1, "Edit", "p", "Password", ""),
            (1, "CheckBox", "u", "Remember me", ""),
            (1, "Button", "d", "Save", ""),
            (1, "Image", "", "", ""))


def test_a_page_reads_as_the_mac_outline():
    assert winread.outline(PAGE).splitlines() == [
        'page "Counter" http://localhost:3000/', '- heading "Counter"', '- text "Count: 1"', '- button "+1" [focused]',
        '- link "Docs"', '- textbox "Name": "Ada"', '- password "Password"',
        '- checkbox "Remember me" [not checked]', '- button "Save" [disabled]']


def test_a_window_nests_what_says_something_and_reads_classic_controls():
    raw = rows(("#window", "hello - Notepad"),
               (0, "TitleBar", "", "", ""), (1, "Button", "", "Close", ""),
               (0, "Pane/Edit", "f", r"Hello\nSecond \\ line", ""),
               (0, "Window", "", "hello - Notepad", ""),
               (1, "List", "", "", ""), (2, "ListItem", "s", "Display", ""), (3, "Text", "", "Display", ""),
               (0, "Pane/Static", "", "Save changes?", ""), (0, "Pane/Button", "", "Yes", ""),
               (0, "Pane/RICHEDIT50W", "", "rich", ""))
    assert winread.outline(raw).splitlines() == [
        'window "hello - Notepad"', '- textbox: "Hello\\nSecond \\\\ line" [focused]', '- list',
        '  - listitem "Display" [selected]', '- text "Save changes?"', '- button "Yes"', '- textbox: "rich"']


def test_it_is_cut_at_max_chars_and_says_when_the_walk_stopped():
    raw = rows(("#window", "W"), *[(0, "Text", "", f"line {i}", "") for i in range(200)])
    out = winread.outline(raw, 300)
    assert len(out) <= 300 and out.endswith("[cut at 300 characters: ask for more with max_chars]")
    assert winread.outline(raw, 60).endswith("[cut]") and len(winread.outline(raw, 5)) == 5
    assert winread.outline(rows(("#window", "W"), (0, "Text", "", "a", ""), ("#stopped", 4000))).endswith(
        "[stopped after 4000 elements]")
    assert "UI Automation tree is empty" in winread.outline(rows(("#window", "W")))


def test_a_powershell_failure_is_said():
    with pytest.raises(RuntimeError, match="UI Automation.*not recognized"):
        winread.outline("Add-Type : not recognized\n[exit 1]")


def test_the_script_travels_as_one_argument_and_read_runs_it():
    s = winread.script(500)
    assert '"' not in s and "__" not in s and "$maxText = 500;" in s
    sent = []
    assert winread.read(lambda cmd, t: sent.append((cmd, t)) or PAGE, 500).startswith('page "Counter"')
    assert sent == [(s, 20.0)]


def test_outline_lookahead_skips_junk_and_keeps_the_last_visible_row():
    raw = ("#window\tInspector\n0\tButton\t\t\t\nnot an element\n"
           "1\tText\t\tBuild\t\n0\tScrollBar\t\tHidden\t\n1\tText\t\tHidden child\t\n"
           "0\tEdit\tf\tOutput path\tC:\\\\src\\tline\\nend\\\n#stopped\t5\n"
           "0\tText\t\tAfter the stop marker\t")
    assert winread.outline(raw).splitlines() == [
        'window "Inspector"', '- button', '  - text "Build"',
        r'- textbox "Output path": "C:\\src\tline\nend\\" [focused]', '[stopped after 5 elements]']


def test_escaped_text_includes_unknown_escape_and_trailing_backslash():
    assert winread._unesc("plain") == "plain"
    assert winread._unesc(r"a\qb\\c\n\t" + "\\") == "aqb\\c\n\t\\"
