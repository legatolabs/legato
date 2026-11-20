"""Background mode on a real Mac: TextEdit, Safari and Chrome are worked while another app stays in front, and the
person's front app, pointer and clipboard are checked after every test.

Opt-in, because it opens apps on the screen it runs on:

    LEGATO_TEST_BACKGROUND=1 pytest -q tests/test_background_live.py

Run it from a terminal that has Accessibility and Screen Recording permission, leave that terminal in front, and don't
touch the mouse or keyboard until it ends. Chrome's tests skip when Chrome is not installed.
"""
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin" or os.environ.get("LEGATO_TEST_BACKGROUND") != "1",
                                reason="macOS, with LEGATO_TEST_BACKGROUND=1 (it opens apps on this screen)")

if sys.platform == "darwin" and os.environ.get("LEGATO_TEST_BACKGROUND") == "1":
    import ApplicationServices as AS
    import Quartz

from legato import safety  # noqa: E402
from legato.actions import Action  # noqa: E402


def osa(script: str) -> str:
    return subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=20).stdout.strip()


def clipboard() -> bytes:
    return subprocess.run(["pbpaste"], capture_output=True, timeout=5).stdout


def front_pid():
    info = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID) or []
    return next((w.get("kCGWindowOwnerPID") for w in info
                 if w.get("kCGWindowLayer") == 0 and w.get("kCGWindowAlpha", 1) > 0), None)


def pointer():
    p = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
    return round(p.x), round(p.y)


@pytest.fixture(autouse=True)
def persons_mac_untouched():
    """Every test: the person's front app, pointer and clipboard are what they were."""
    if not AS.AXIsProcessTrusted():
        pytest.skip("this terminal needs Accessibility permission")
    subprocess.run(["pbcopy"], input=b"the person's clipboard", timeout=5)
    before = (front_pid(), pointer())
    yield
    assert front_pid() == before[0], "the front app changed"
    assert pointer() == before[1], "the pointer moved (did someone touch the mouse?)"
    assert clipboard() == b"the person's clipboard"


def find(el, pred, depth=0):
    err, role = AS.AXUIElementCopyAttributeValue(el, "AXRole", None)
    if pred(el, role):
        return el
    if depth > 30:
        return None
    err, kids = AS.AXUIElementCopyAttributeValue(el, "AXChildren", None)
    for k in kids or []:
        if (f := find(k, pred, depth + 1)) is not None:
            return f
    return None


def text_of(el, name):
    err, v = AS.AXUIElementCopyAttributeValue(el, name, None)
    return str(v) if err == 0 and v is not None else ""


def frame(el):
    err, p = AS.AXUIElementCopyAttributeValue(el, "AXPosition", None)
    err, s = AS.AXUIElementCopyAttributeValue(el, "AXSize", None)
    _, pt = AS.AXValueGetValue(p, AS.kAXValueCGPointType, None)
    _, sz = AS.AXValueGetValue(s, AS.kAXValueCGSizeType, None)
    return pt.x, pt.y, sz.width, sz.height


def in_window(env, x, y):
    """Screen point -> the window-relative point the model would use."""
    w = env.window()
    return round(x - w["x"]), round(y - w["y"])


# ------------------------------------------------------------ TextEdit
@pytest.fixture()
def textedit():
    from legato.background import BackgroundEnv
    osa('tell application "TextEdit" to make new document with properties {text:"alpha beta"}')
    time.sleep(1.0)
    env = BackgroundEnv("TextEdit", confirm=None)
    yield env
    osa('tell application "TextEdit" to close document 1 saving no')


def document_text() -> str:
    return osa('tell application "TextEdit" to get text of document 1')


def test_textedit_typing_lands_in_the_background(textedit):
    obs = textedit.observe()
    w = textedit.window()
    assert (obs.width, obs.height) == (int(w["w"]), int(w["h"]))
    textedit.act(Action("key", keys="super+Down"))            # to the end, by shortcut (Cmd+Down on a Mac)
    textedit.act(Action("type", text=" gamma"))
    assert document_text() == "alpha beta gamma"


def test_textedit_click_places_the_insertion_point(textedit):
    textedit.observe()
    area = find(textedit.m.app, lambda e, r: r == "AXTextArea")
    x, y, _, h = frame(area)
    textedit.act(Action("click", *in_window(textedit, x + 6, y + 8)))       # on the first letter
    textedit.act(Action("type", text="X"))
    assert document_text() == "Xalpha beta"


def test_textedit_double_click_selects_a_word_and_shortcuts_work(textedit):
    textedit.observe()
    area = find(textedit.m.app, lambda e, r: r == "AXTextArea")
    x, y, _, _ = frame(area)
    textedit.act(Action("double_click", *in_window(textedit, x + 30, y + 8)))     # in "alpha"
    textedit.act(Action("type", text="delta"))
    assert document_text() == "delta beta"
    textedit.act(Action("key", keys="ctrl+a"))               # Cmd+A, as on a Mac
    textedit.act(Action("type", text="replaced"))
    assert document_text() == "replaced"


def test_textedit_drag_is_not_run(textedit):
    textedit.observe()
    with pytest.raises(safety.Unavailable):
        textedit.act(Action("drag", x=10, y=100, x2=200, y2=100))
    assert document_text() == "alpha beta"


# ------------------------------------------------------------ browsers
PAGE = b"""<!doctype html><body style="margin:0;font-size:24px">
<input id=t aria-label="name" style="width:90%;font-size:24px">
<button id=b style="font-size:24px">click me</button>
<a id=l href="#" onclick="say({link: true}); return false">a link</a>
<div>""" + "".join(f"<p>paragraph {i}</p>" for i in range(80)).encode() + b"""</div>
<script>
function say(o) { fetch('/log', {method: 'POST', body: JSON.stringify(o)}); }
t.addEventListener('input', () => say({value: t.value}));
b.addEventListener('click', (e) => say({click: e.isTrusted}));
let sent = 0;
window.addEventListener('scroll', () => { if (!sent++) say({scrolled: true}); });
say({loaded: location.search});
</script>"""


@pytest.fixture(scope="module")
def site():
    log: list = []

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(PAGE)

        def do_POST(self):
            log.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *a):
            pass
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/", log
    srv.shutdown()


def wait_for(log, pred, s=8.0):
    end = time.monotonic() + s
    while time.monotonic() < end:
        if any(pred(e) for e in log):
            return True
        time.sleep(0.1)
    return False


BROWSERS = [b for b in ("Safari", "Google Chrome") if Path(f"/Applications/{b}.app").exists()]


@pytest.fixture(params=BROWSERS)
def browser(request, site):
    from legato.background import BackgroundEnv
    url, log = site
    log.clear()
    tag = f"?{request.param.replace(' ', '')}{time.time():.0f}"
    env = BackgroundEnv(request.param, confirm=None)
    env.open_url(url + tag)                       # Chrome takes the front for a new URL: open_url gives it back
    if not wait_for(log, lambda e: e.get("loaded") == tag, 40.0):
        pytest.fail(f"the page never loaded in {request.param}")
    time.sleep(1.0)
    env.observe()
    yield env, log


def element(env, role, label):
    end = time.monotonic() + 5
    while time.monotonic() < end:
        el = find(env.m.app, lambda e, r: r == role and label in " ".join(
            text_of(e, k) for k in ("AXTitle", "AXDescription", "AXValue")))
        if el is not None:
            return el
        time.sleep(0.3)
    pytest.fail(f"no {role} {label!r} in the page")


def center(env, el):
    x, y, w, h = frame(el)
    return in_window(env, x + w / 2, y + h / 2)


def test_browser_click_presses_a_button_and_a_link(browser):
    env, log = browser
    env.act(Action("click", *center(env, element(env, "AXButton", "click me"))))
    assert wait_for(log, lambda e: e.get("click") is True), (log, env.target)    # isTrusted: a real click
    link = element(env, "AXLink", "a link")
    words = find(link, lambda e, r: r == "AXStaticText") or link
    x, y, w, h = frame(words)
    # Chrome's frame for an inline link that starts a line runs from the end of the line above: the words are at
    # its bottom left, where a model sees and clicks them (Safari's frame is just the words)
    env.act(Action("click", *in_window(env, x + min(w / 2, 20), y + h - min(h / 2, 12))))
    if not wait_for(log, lambda e: e.get("link")):
        direct = AS.AXUIElementPerformAction(link, "AXPress")       # what a press on the link itself does
        pytest.fail(f"no link click: {log}, pressed {env.target}, link {frame(link)}, words {frame(words)}; "
                    f"AXPress on the link directly: error {direct}, page saw it: {wait_for(log, lambda e: e.get('link'))}")


def test_browser_click_in_a_field_then_typing_fills_it(browser):
    env, log = browser
    env.act(Action("click", *center(env, element(env, "AXTextField", "name"))))
    env.act(Action("type", text="Ada"))
    assert wait_for(log, lambda e: e.get("value") == "Ada"), log


def test_browser_scrolls_the_page(browser):
    env, log = browser
    w = env.window()
    env.act(Action("scroll", x=int(w["w"] / 2), y=int(w["h"] * 0.7), direction="down", amount=3))
    assert wait_for(log, lambda e: e.get("scrolled")), log
