"""MCP-H2H tasks: short Mac GUI tasks with an end state a script can check.

Each task has `setup()` (puts the start state in place and clears what an earlier run left), `prompt` (what the agent
is told) and `check() -> bool` (did the end state come out right). Files live under ~/LegatoBench; the web tasks talk
to `site.py` on localhost:8765, which records what was submitted under ~/LegatoBench/site/.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path.home() / "LegatoBench"
SITE = "http://localhost:8765"


def osa(script: str) -> str:
    return subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=20).stdout.strip()


def tidy_windows() -> None:
    """Close only what the tasks open: TextEdit documents and Finder windows in ~/LegatoBench, and Safari windows on the
    task site. The person's other windows are left alone."""
    root = str(ROOT)
    osa(f'''if application "TextEdit" is running then tell application "TextEdit"
          repeat with d in (documents whose path starts with "{root}" or path is missing value)
            close d saving no
          end repeat
        end tell''')
    osa(f'''tell application "Finder" to close (every Finder window whose target as alias as text contains "LegatoBench")''')
    osa(f'''if application "Safari" is running then tell application "Safari"
          close (every window whose URL of current tab starts with "{SITE}")
        end tell''')


def fresh(*paths: str) -> None:
    for p in paths:
        q = ROOT / p
        if q.is_dir():
            shutil.rmtree(q)
        elif q.exists():
            q.unlink()


def site_state(name: str) -> dict:
    f = ROOT / "site" / f"{name}.json"
    return json.loads(f.read_text()) if f.exists() else {}


@dataclass
class Task:
    id: str
    prompt: str
    setup: Callable[[], None]
    check: Callable[[], bool]


def _textedit_new_setup() -> None:
    ROOT.mkdir(exist_ok=True)
    for f in ROOT.glob("note*"):
        f.unlink()


def _textedit_new_check() -> bool:
    return any("legato bench" in f.read_text(errors="ignore") for f in ROOT.glob("note*") if f.is_file())


def _rename_setup() -> None:
    fresh("inbox")
    (ROOT / "inbox").mkdir(parents=True)
    (ROOT / "inbox" / "report-draft.txt").write_text("quarterly numbers\n")


def _rename_check() -> bool:
    return (ROOT / "inbox" / "report-final.txt").exists() and not (ROOT / "inbox" / "report-draft.txt").exists()


def _letter_setup() -> None:
    ROOT.mkdir(exist_ok=True)
    (ROOT / "letter.txt").write_text("Dear Bob,\n\nThanks for the quick reply. See you on Friday.\n\nBest,\nAda\n")


def _letter_check() -> bool:
    t = (ROOT / "letter.txt").read_text(errors="ignore")
    return "Dear Alice" in t and "Bob" not in t and "See you on Friday" in t


def _site_setup(name: str) -> Callable[[], None]:
    return lambda: fresh(f"site/{name}.json")


TASKS = [
    Task("textedit-new",
         "Open TextEdit, make a new document containing exactly the text `legato bench`, and save it as `note` in the "
         "LegatoBench folder in my home folder.",
         _textedit_new_setup, _textedit_new_check),
    Task("finder-rename",
         "In Finder, go to the folder LegatoBench/inbox in my home folder and rename report-draft.txt to "
         "report-final.txt.",
         _rename_setup, _rename_check),
    Task("textedit-edit",
         "Open letter.txt from the LegatoBench folder in my home folder in TextEdit, change the name Bob to Alice, and "
         "save it.",
         _letter_setup, _letter_check),
    Task("form-signup",
         f"In Safari, open {SITE}/signup and sign up with the name Ada Lovelace, the email ada@example.com and the "
         "Pro plan.",
         _site_setup("signup"),
         lambda: site_state("signup") == {"name": "Ada Lovelace", "email": "ada@example.com", "plan": "pro"}),
    Task("form-todo",
         f"In Safari, open {SITE}/todo and add three items to the list, in this order: milk, eggs, bread.",
         _site_setup("todo"),
         lambda: [i.strip().lower() for i in site_state("todo").get("items", [])] == ["milk", "eggs", "bread"]),
]
BY_ID = {t.id: t for t in TASKS}
