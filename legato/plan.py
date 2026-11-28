"""Plans: one model call carries a list of steps that run in order (Harness(plan=True)).

A step is a screen action (as `computer` takes it), click_text (a label found on the screen as it is when the step
runs), shell (the bash tool, AppleScript through osascript), wait_for (until a label is on the screen) or, last, done
or fail. The plan stops at the first step that cannot go on, and the model sees what ran and why. Labels are read by
the macOS Vision text reader; elsewhere click_text and wait_for report that they cannot run.
"""
from __future__ import annotations

import io
import re
import sys
from dataclasses import dataclass

PLAN_SYSTEM = """You control a computer desktop ({w}x{h} pixels) to complete the user's task. Each turn you see the \
current screenshot. Act by calling the `plan` tool once with `steps`: a list of up to {batch} steps that run in order, \
so one turn can do a whole part of the task.
{coords}
Steps (objects with "action"):
- left_click, right_click, double_click, triple_click (coordinate, and "text": the label of what you click, as it reads \
on the screen; left_click with keys "shift" or "cmd" holds that key). The click goes to that label where it is when \
the step runs, nearest your coordinate, so it still lands after earlier steps changed the screen; give the label \
whenever the target has one. left_click_drag (coordinate to end_coordinate), type (text, into the focused field; end \
it with a newline to press Return), key (keys, e.g. "cmd+s", "Return", "Tab"), scroll (coordinate, direction \
up/down/left/right, amount), wait.
- click_text (text; optional coordinate near it; "double": true to double-click, "right": true for a right click): \
clicks a label you cannot see yet, such as a menu item or a dialog button that an earlier step opens.
- shell (command): runs a bash command on this computer and returns its output and exit code. It is often much \
faster than the screen: open apps (open -a "Name") and links (open https://...), read, make or move files.
- applescript (script; check): reads or changes an app's data (Calendar, Reminders, Notes, Contacts, Mail, Music). The \
script is compiled against the app's dictionary first; an error comes back with the app's real classes and commands, \
so use exactly those names. check is a read-only script that reads back what you changed.
- wait_for (text; optional timeout_s, at most 15): waits until that label is on the screen.
- done or fail, last.
The plan stops at the first step that cannot go on (a label not found, a command or script that fails, a wait that \
times out) and you then see what ran, why it stopped, and the screen. done counts in the same plan only right after a \
check that passed (wait_for, a shell command that exits 0, or an applescript step); otherwise look at the screen and \
call done in the next turn. When the task is complete, always call done: an answer without a step does nothing.
Plan as far ahead as you safely can: for example open a menu, click its item by label, type in the dialog that opens, \
click "Save" by label, wait_for what proves it worked, done.
Keep any text before your tool call to one short sentence. Prefer reliable keyboard shortcuts. Save files in their \
current format."""

STEP_ACTIONS = ["left_click", "right_click", "double_click", "triple_click", "left_click_drag", "type", "key",
                "scroll", "wait", "click_text", "shell", "applescript", "wait_for", "done", "fail"]
LABELLED = ("left_click", "right_click", "double_click", "triple_click")     # clicks that may carry their target's label
MODIFIERS = {"shift", "cmd", "command", "ctrl", "control", "alt", "option", "super"}
POINTER = ("click", "double_click", "triple_click", "right_click", "drag", "scroll")
MAX_WAIT_S = 15.0


# What PLAN_SYSTEM says of labels, checks and the example, and what it says instead where they are not there.
_LABEL_CLICKS = ('(coordinate, and "text": the label of what you click, as it reads on the screen; left_click with keys '
                 '"shift" or "cmd" holds that key). The click goes to that label where it is when the step runs, nearest '
                 "your coordinate, so it still lands after earlier steps changed the screen; give the label whenever the "
                 "target has one. ")
_PLAIN_CLICKS = '(coordinate; left_click with keys "shift" or "ctrl" holds that key). '
_STOPS = "(a label not found, a command or script that fails, a wait that times out)"
_DONE_RULE = ("done counts in the same plan only right after a check that passed (wait_for, a shell command that exits "
              "0, or an applescript step); otherwise look at the screen and call done in the next turn.")
_EXAMPLE = ('for example open a menu, click its item by label, type in the dialog that opens, click "Save" by label, '
            "wait_for what proves it worked, done.")
_POWERSHELL = ("- shell (command): runs a PowerShell command on this computer and returns its output and exit code. It "
               "is often much faster than the screen: open apps and links (Start-Process), read, make or move files.")


def can_read() -> bool:
    """Whether read_lines has a text reader on this computer (macOS Vision, or rapidocr-onnxruntime)."""
    from importlib.util import find_spec
    try:
        return any(find_spec(m) is not None for m in ("Vision", "rapidocr_onnxruntime"))
    except (ImportError, ValueError):
        return False


def step_actions(shell: str = "bash", applescript: bool = True, labels: bool = True) -> list[str]:
    """STEP_ACTIONS without the steps that cannot run: shell with no shell tool, applescript with no applescript
    tool, click_text and wait_for with no text reader."""
    gone = ([] if shell else ["shell"]) + ([] if applescript else ["applescript"]) + \
        ([] if labels else ["click_text", "wait_for"])
    return [a for a in STEP_ACTIONS if a not in gone]


def fit_system(text: str, shell: str = "bash", applescript: bool = True, labels: bool = True) -> str:
    """A plan prompt (PLAN_SYSTEM, or one made from it) that promises only what can run: `shell` is what the shell
    tool runs ("" for none), applescript whether there is an applescript tool, labels whether the screen's text can
    be read (can_read). With all three it is returned as it is. Qwen3.8-27B given the full prompt on Windows with
    no shell tool planned shell steps for 20 turns, none of which ran."""
    if shell == "bash" and applescript and labels:
        return text
    lines = []
    for line in text.split("\n"):
        if line.startswith("- shell (") and shell != "bash":
            if shell:
                lines.append(_POWERSHELL if shell.lower() == "powershell" else line.replace("a bash command", f"a {shell} command"))
            continue
        if (line.startswith("- applescript (") and not applescript) or \
                (line.startswith(("- click_text (", "- wait_for (")) and not labels):
            continue
        lines.append(line)
    text = "\n".join(lines)
    if sys.platform != "darwin":
        text = text.replace('"cmd+s"', '"ctrl+s"')
    if not labels:
        text = text.replace(_LABEL_CLICKS, _PLAIN_CLICKS)
    stops = [w for w, on in (("a label not found", labels), ("a command or script that fails", shell or applescript),
                             ("a wait that times out", labels)) if on]
    text = text.replace(_STOPS, f"({', '.join(stops)})" if stops else "(an action the screen no longer fits)")
    checks = [w for w, on in (("wait_for", labels), ("a shell command that exits 0", shell),
                              ("an applescript step", applescript)) if on]
    if checks:
        text = text.replace(_DONE_RULE, _DONE_RULE.replace(
            "(wait_for, a shell command that exits 0, or an applescript step)", f"({', or '.join(checks)})"))
        example = ("for example " + ("open a menu, click its item by label, type in the dialog that opens" if labels else
                                     "click a field, type into it, press Return") + ", " +
                   ("wait_for what proves it worked" if labels else "a shell command that reads back what changed") +
                   ", done.")
    else:
        text = text.replace(_DONE_RULE, "done counts only in a plan of its own: after your steps you see the screen "
                                        "they left, and call done alone in the next turn if it shows the task is complete.")
        example = ("for example press a shortcut, type in the window that opens, press Return; then, in the next turn, "
                   "check the screen and call done.")
    return text.replace(_EXAMPLE, example)


@dataclass
class FindClick:
    """click_text: click `text` where it is on the screen when the step runs."""
    text: str
    kind: str = "click"                      # click, double_click, triple_click or right_click
    near: tuple[int, int] | None = None      # the model's guess, screen pixels
    fallback: tuple[int, int] | None = None  # a labelled click: where it aimed, used if the label is not there

    def describe(self) -> str:
        k = self.kind.replace("_", " ")
        return f'{k} text "{self.text[:40]}"'


@dataclass
class WaitFor:
    """wait_for: until `text` is on the screen, at most timeout_s."""
    text: str
    timeout_s: float = 5.0

    def describe(self) -> str:
        return f'wait for "{self.text[:40]}" ({self.timeout_s:g} s)'


# Harness(strict_done=True): what counts as the check before a done in the same plan, and the prompt that says so.
# In our tests, 8 of 25 such dones ended failed tasks: all 3 after a wait_for (the text was already on
# the screen) and 5 after a shell step, one of them `open -a Reminders; sleep 2`, which checks nothing.
STRICT_DONE_SWAPS = (
    ("done counts in the same plan only right after a check that passed (wait_for, a shell command that exits 0, or an "
     "applescript step); otherwise look at the screen and call done in the next turn.",
     "done counts in the same plan only right after a shell or applescript step that exits 0 and prints what it "
     "checked (the setting's value, the file's lines, the item's name); a wait_for does not count. Otherwise look at "
     "the screen and call done in the next turn."),
    ("wait_for what proves it worked, done.", "wait_for what proves it worked; you then see the screen and call done."),
)


def strict_system(text: str) -> str:
    """PLAN_SYSTEM with the strict done rule (Harness(strict_done=True))."""
    for a, b in STRICT_DONE_SWAPS:
        text = text.replace(a, b)
    return text


def printed(result: str) -> bool:
    """Whether a tool result shows output beyond its exit status: a check that prints what it checked."""
    body = re.sub(r"\[exit -?\d+\]\s*$", "", result).strip()
    if body.startswith("ran (exit 0). Result:"):                  # applescript: its result and any check's output
        body = body[len("ran (exit 0). Result:"):].replace("(nothing)", "")
        body = re.sub(r"check \(exit 0\):", "", body)
    return bool(body.strip())


PROGRESS_NOTE = ("\nEach plan also carries `progress`: one line, what is done so far and what is left. Older "
                 "screenshots are left out of what you see, so your progress lines are how you keep your place.")


def plan_tool(max_steps: int, progress: bool = False, actions: list[str] | None = None,
              extra: dict[str, dict] | None = None) -> dict:
    """The `plan` tool in OpenAI function format (progress: a required one-line `progress` beside the steps; actions:
    the steps it offers, as step_actions gives them, default all; extra: other tools, {name: parameters}, offered as
    steps of their own name)."""
    step = {"type": "object", "required": ["action"], "properties": {
        "action": {"type": "string", "enum": actions or STEP_ACTIONS},
        "coordinate": {"type": "array", "items": {"type": "number"}, "description": "[x, y] on the screenshot you see now"},
        "end_coordinate": {"type": "array", "items": {"type": "number"}, "description": "[x, y] for left_click_drag"},
        "text": {"type": "string", "description": "type: the text; click_text and wait_for: the label"},
        "keys": {"type": "string"}, "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
        "amount": {"type": "integer"}, "command": {"type": "string", "description": "shell: the bash command"},
        "timeout_s": {"type": "number", "description": f"wait_for: at most {MAX_WAIT_S:g}"},
        "script": {"type": "string", "description": "applescript: the script"},
        "check": {"type": "string", "description": "applescript: a read-only script that reads back what changed"},
        "double": {"type": "boolean"}, "right": {"type": "boolean"}}}
    tool = {"type": "function", "function": {
        "name": "plan", "description": "Run steps in order: screen actions, click_text, shell, wait_for, then done.",
        "parameters": {"type": "object", "required": ["steps"], "properties": {
            "steps": {"type": "array", "items": step, "maxItems": max_steps}}}}}
    if actions and len(actions) < len(STEP_ACTIONS):      # only what can run here is offered
        gone = {"shell": ("command",), "applescript": ("script", "check"), "wait_for": ("timeout_s",),
                "click_text": ("double", "right")}
        for name, keys in gone.items():
            if name not in actions:
                for k in keys:
                    step["properties"].pop(k, None)
        if "click_text" not in actions:
            step["properties"]["text"] = {"type": "string",
                                          "description": "type: the text; done: the answer, if the task asks for one"}
        tool["function"]["description"] = "Run steps in order: " + ", ".join(
            ["screen actions"] + [a for a in ("click_text", "shell", "applescript", "wait_for") if a in actions]) + \
            ", then done."
    for name, params in (extra or {}).items():
        step["properties"]["action"]["enum"] = step["properties"]["action"]["enum"] + [name]
        for k, v in (params.get("properties") or {}).items():
            step["properties"].setdefault(k, v)
    if extra:
        d = tool["function"]["description"]
        tool["function"]["description"] = d[: -len(", then done.")] + ", " + ", ".join(extra) + ", then done."
    if progress:
        params = tool["function"]["parameters"]
        params["properties"]["progress"] = {"type": "string",
                                            "description": "One line: what is done so far and what is left."}
        params["required"] = ["progress", "steps"]
    return tool


def plan_steps(rec: dict) -> list[dict] | None:
    """The steps of a reply, in order: from native `plan` calls ({"steps": [...]}), `computer` calls (one step each),
    `bash` calls (a shell step), and objects written in the text ({"steps": [...]}, {"name": "plan", "arguments":
    {...}}, or one step with "action"). None if the reply has none."""
    steps: list[dict] = []

    def take(name: str | None, a) -> None:
        if not isinstance(a, dict):
            return
        if isinstance(a.get("arguments"), dict) and "steps" not in a and "action" not in a:
            name, a = a.get("name"), a["arguments"]
        if isinstance(a.get("steps"), list):
            steps.extend(s for s in a["steps"] if isinstance(s, dict))
        elif "action" in a:
            steps.append(a)
        elif name in ("bash", "shell") and isinstance(a.get("command"), str):
            steps.append({"action": "shell", "command": a["command"]})
        elif name == "applescript" and isinstance(a.get("script"), str):   # the tool called by its own name
            steps.append({"action": "applescript", **{k: a[k] for k in ("script", "check") if a.get(k)}})
        elif name in ("click_text", "wait_for") and isinstance(a.get("text"), str):
            steps.append({"action": name, **a})

    import json
    content = rec.get("content") or ""
    if isinstance(content, list):
        content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
    for obj in _objects(content):
        take(None, obj)
    for tc in rec.get("tool_calls") or []:
        fn = tc.get("function") or {}
        a = fn.get("arguments")
        if isinstance(a, str):
            try:
                a = json.loads(a)
            except json.JSONDecodeError:
                continue
        take(fn.get("name"), a)
    return steps or None


def _objects(text: str) -> list:
    import json
    out, depth, start = [], 0, None
    for i, ch in enumerate(text):
        if ch == "{":
            depth, start = depth + 1, (i if depth == 0 else start)
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0:
                try:
                    out.append(json.loads(text[start:i + 1]))
                except json.JSONDecodeError:
                    continue
    return out


# ---------------------------------------------------------------- reading labels on the screen
def _norm(s: str) -> str:
    s = s.lower().replace("…", "...").replace("’", "'").strip()
    s = re.sub(r"\s+", " ", s)
    return s.strip(" .:")


def find_text(png: bytes, text: str, near: tuple[int, int] | None = None) -> tuple[int, int] | None:
    """The centre of `text` on the screenshot `png`, in its pixels; None if it is not there. A line that is the label
    beats a line that contains it; among equals, the one nearest `near`, else the first in reading order."""
    want = _norm(text)
    if not want:
        return None
    hits = []
    for line, box_of in read_lines(png):
        n = _norm(line)
        if not n:
            continue
        if n == want:
            rank, box = 0, box_of(None)
        elif " " in want and " " not in n and n == want.replace(" ", ""):
            rank, box = 0, box_of(None)          # a reader that dropped the spaces between the label's words
        else:
            i = n.find(want)
            if i < 0 or (i > 0 and n[i - 1].isalnum()) or (i + len(want) < len(n) and n[i + len(want)].isalnum()):
                continue
            rank, box = 1, box_of(want)
        if box is None:
            continue
        hits.append((rank, box))
    if not hits:
        return None
    if near is not None:
        key = lambda h: (h[0], (h[1][0] - near[0]) ** 2 + (h[1][1] - near[1]) ** 2)  # noqa: E731
    else:
        key = lambda h: (h[0], h[1][1], h[1][0])  # noqa: E731
    return min(hits, key=key)[1]


NEAR_HALF = (320, 140)       # Harness(ocr_near=True): the box read first around a labelled click, in screenshot pixels


def find_text_near(png: bytes, text: str, near: tuple[int, int], half: tuple[int, int] = NEAR_HALF):
    """find_text on the part of the screenshot around `near` (2*half wide and high): the reader's time grows with
    the area it reads (0.09 s for 640x280 against 0.29 s for 1024x768 on an M-series Mac; ~3 s a whole read in the
    task VMs). None if the label is not in that box."""
    from PIL import Image
    im = Image.open(io.BytesIO(png))
    w, h = im.size
    x0, y0 = max(0, near[0] - half[0]), max(0, near[1] - half[1])
    x1, y1 = min(w, near[0] + half[0]), min(h, near[1] + half[1])
    if x1 - x0 < 16 or y1 - y0 < 16:
        return None
    buf = io.BytesIO()
    im.crop((x0, y0, x1, y1)).save(buf, "PNG")
    xy = find_text(buf.getvalue(), text, (near[0] - x0, near[1] - y0))
    return (xy[0] + x0, xy[1] + y0) if xy else None


OCR_CACHE = False            # Harness(ocr_cache=True): a screenshot read before is not read again
_READ: "dict[bytes, list]" = {}


def read_lines(png: bytes) -> list:
    """[(line text, box_of(substring or None) -> (x, y) centre in pixels)] for the text on the screenshot, by the
    macOS Vision framework, or RapidOCR elsewhere when it is installed (rapidocr-onnxruntime). Raises RuntimeError
    when neither is available. With OCR_CACHE, the same screenshot (the
    same bytes: a screen that has not changed) is read once; a wait_for that polls a still screen reads it once."""
    if not OCR_CACHE:
        return _read_lines(png)
    import hashlib
    key = hashlib.blake2b(png, digest_size=16).digest()
    if key not in _READ:
        if len(_READ) >= 8:
            _READ.pop(next(iter(_READ)))
        _READ[key] = _read_lines(png)
    return _READ[key]


_RAPID: list = []      # the RapidOCR reader, made once (loading its models takes about a second)


def _read_lines_rapid(png: bytes) -> list:
    """read_lines where macOS Vision is not available (Linux): RapidOCR (pip install rapidocr-onnxruntime, models in
    the wheel, CPU). A substring's centre is placed along its line's box by character position."""
    if not _RAPID:
        from rapidocr_onnxruntime import RapidOCR
        _RAPID.append(RapidOCR())
    result, _ = _RAPID[0](png)
    out = []
    for box, text, _score in result or []:
        xs, ys = [p[0] for p in box], [p[1] for p in box]
        x0, x1, yc = min(xs), max(xs), (min(ys) + max(ys)) / 2
        line = str(text)

        def box_of(sub, line=line, x0=x0, x1=x1, yc=yc):
            if sub is None:
                return round((x0 + x1) / 2), round(yc)
            low = line.lower().replace("…", "...")
            i, n = low.find(sub), len(sub)
            if i < 0:                            # the reader can drop the spaces between words ("Documentsaved")
                keep = [k for k, c in enumerate(low) if c != " "]
                j = "".join(low[k] for k in keep).find(sub.replace(" ", ""))
                if j < 0:
                    return None
                n = len(sub.replace(" ", ""))
                i, n = keep[j], keep[j + n - 1] - keep[j] + 1
            mid = (i + n / 2) / max(1, len(line))
            return round(x0 + (x1 - x0) * mid), round(yc)
        out.append((line, box_of))
    return out


def _read_lines(png: bytes) -> list:
    try:
        import Vision
        from Foundation import NSData, NSMakeRange
    except Exception as e:  # noqa: BLE001
        try:
            import rapidocr_onnxruntime  # noqa: F401
        except Exception:  # noqa: BLE001
            raise RuntimeError("reading text on the screen needs macOS (pyobjc-framework-Vision) or "
                               "rapidocr-onnxruntime") from e
        return _read_lines_rapid(png)
    from PIL import Image
    w, h = Image.open(io.BytesIO(png)).size
    handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(NSData.dataWithBytes_length_(png, len(png)),
                                                                         None)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(False)
    ok = handler.performRequests_error_([req], None)
    if isinstance(ok, tuple):
        ok = ok[0]
    if not ok:
        return []

    def centre(rect) -> tuple[int, int]:
        (x, y), (bw, bh) = rect.origin, rect.size
        return round((x + bw / 2) * w), round((1 - (y + bh / 2)) * h)

    out = []
    for obs in req.results() or []:
        cands = obs.topCandidates_(1)
        if not cands:
            continue
        cand = cands[0]
        line = str(cand.string())

        def box_of(sub, cand=cand, line=line, obs=obs):
            if sub is None:
                return centre(obs.boundingBox())
            low = line.lower().replace("…", "...")
            i = low.find(sub)
            if i < 0 or len(low) != len(line):
                return centre(obs.boundingBox())
            r = cand.boundingBoxForRange_error_(NSMakeRange(i, len(sub)), None)
            r = r[0] if isinstance(r, tuple) else r
            return centre(r.boundingBox()) if r is not None else centre(obs.boundingBox())
        out.append((line, box_of))
    return out


def tool_failed(result: str) -> bool:
    """A tool result that reports a failure: a command's non-zero exit, or a handler's error."""
    m = re.search(r"\[exit (-?\d+)\]\s*$", result)
    return bool(m and m.group(1) != "0") or result.startswith(("error:", "did not compile", "compiled, then failed",
                                                               "not run:"))


def warm_up(png: bytes) -> None:
    """Read `png` once and drop the result: the first read in a process loads Vision's model (~10 s)."""
    try:
        read_lines(png)
    except Exception:  # noqa: BLE001 - no reader here: click_text and wait_for say so when they run
        pass


# ---------------------------------------------------------------- a planned click on a screen that moved
TARGET_HALF, TARGET_PIXELS, TOL_DELTA = 40, 64, 24


def target_moved(before_png: bytes, after_png: bytes, x: int, y: int) -> bool:
    """Whether the area around (x, y) changed between the screenshot a click was planned on and the screen now."""
    from PIL import Image, ImageChops
    try:
        b0 = Image.open(io.BytesIO(before_png)).convert("L")
        b1 = Image.open(io.BytesIO(after_png)).convert("L")
    except Exception:  # noqa: BLE001 - a screenshot that does not decode: no evidence the target moved
        return False
    if b0.size != b1.size:
        return True
    w, h = b0.size
    box = (max(0, x - TARGET_HALF), max(0, y - TARGET_HALF), min(w, x + TARGET_HALF), min(h, y + TARGET_HALF))
    if box[2] <= box[0] or box[3] <= box[1]:
        return False
    d = ImageChops.difference(b0.crop(box), b1.crop(box)).point(lambda v: 255 if v > TOL_DELTA else 0)
    return d.histogram()[255] > TARGET_PIXELS


# ---------------------------------------------------------------- the screen's text, for a log of what changed
def screen_lines(png: bytes) -> list[tuple[str, int, int]]:
    """[(line, x, y)] of the text on the screenshot, top to bottom (macOS Vision)."""
    out = []
    for line, box_of in read_lines(png):
        x, y = box_of(None)
        out.append((line.strip(), x, y))
    return sorted((l for l in out if l[0]), key=lambda l: (l[2] // 8, l[1]))


def describe_changes(before: list, now: list, most: int = 15, width: int = 60) -> str:
    """The text that appeared and went between two screens, for the model: what its steps changed, so older
    screenshots can be left out. Lines are matched by their text; a line that moved is neither new nor gone."""
    from collections import Counter
    def words(ls):                                # icons read as one letter ("Q", "X") are noise
        return [l for l in ls if sum(c.isalnum() for c in l[0]) >= 2]
    before, now = words(before), words(now)
    b, n = Counter(l[0] for l in before), Counter(l[0] for l in now)
    new = [l for l in now if n[l[0]] > b.get(l[0], 0)]
    gone = [l for l in before if b[l[0]] > n.get(l[0], 0)]
    if not new and not gone:
        return "Text on the screen after your steps: unchanged."

    def fmt(ls):
        shown = [f'"{l[0][:width]}" ({l[1]}, {l[2]})' for l in ls[:most]]
        more = f" ... and {len(ls) - most} more" if len(ls) > most else ""
        return "; ".join(shown) + more
    parts = ["Text on the screen after your steps (at x, y as you click):"]
    if new:
        parts.append(f"appeared: {fmt(new)}")
    if gone:
        parts.append(f"went: {fmt(gone)}")
    parts.append(f"unchanged: {sum((n & b).values())} lines.")
    return "\n".join(parts)

