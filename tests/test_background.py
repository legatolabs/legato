"""Background mode (legato/background.py) against a fake Mac: what each action turns into, what is refused, and that
nothing reaches the person's pointer, front app or clipboard. Runs anywhere; tests/test_background_live.py runs the
same on a real Mac."""
import io
import time

import pytest
from PIL import Image

from legato import safety
from legato.actions import Action
from legato.background import FLAGS, KEYCODES, BackgroundEnv
from legato.harness import UNAVAILABLE_NOTE, _refused
from test_harness import server, tc  # noqa: F401


class El:
    """A fake Accessibility element."""

    def __init__(self, role, parent=None, actions=(), value=None, selectable=False, index_at=None, menu=False,
                 vbar=None, title="", frame=None):
        self.role, self.parent, self.acts, self.value = role, parent, list(actions), value
        self.selectable, self._index_at, self.vbar, self.title = selectable, index_at, vbar, title
        if menu:
            self.acts.append("AXShowMenu")
        self.focused, self.selected, self.range = False, False, None
        self.frame, self.children = frame, []
        if parent is not None:
            parent.children.append(self)

    def __repr__(self):
        return f"El({self.role} {self.title})"


class FakeMac:
    """MacApp's methods over a fake window and element tree. Records every call."""

    def __init__(self, front=999, pid=42):
        self.pid, self.flag = pid, "-a"
        self.front, self.cursor = front, (500.0, 500.0)
        self.wins = [{"id": 7, "x": 100.0, "y": 50.0, "w": 400.0, "h": 300.0, "layer": 0}]
        self.hits: list = []             # (x0, y0, x1, y1, element) in screen points, first match wins
        self.keys: list = []             # (code, flags, char)
        self.performed: list = []
        self.sets: list = []
        self.captures: list = []
        self.reopened = 0
        self.shade = 0
        self.activated: list = []
        self.app = El("AXApplication")
        self.urls: list = []

    def windows(self):
        return list(self.wins)

    def reopen(self):
        self.reopened += 1

    def capture(self, ids, rect):
        self.captures.append((ids, rect))
        im = Image.new("RGB", (int(rect[2]), int(rect[3])), (self.shade % 256, 0, 0))
        b = io.BytesIO()
        im.save(b, "PNG")
        return b.getvalue()

    def front_pid(self):
        return self.front

    def activate(self, pid):
        self.activated.append(pid)
        self.front = pid

    def open_url(self, url):
        self.urls.append(url)

    def pointer(self):
        return self.cursor

    def key(self, code, flags=0, char=None):
        self.keys.append((code, flags, char))

    def element_at(self, x, y):
        return next((e for x0, y0, x1, y1, e in self.hits if x0 <= x < x1 and y0 <= y < y1), None)

    def attr(self, el, name):
        return {"AXRole": el.role, "AXParent": el.parent, "AXValue": el.value, "AXChildren": el.children,
                "AXVerticalScrollBar": el.vbar, "AXTitle": el.title, **getattr(el, "extra", {})}.get(name)

    def frame(self, el):
        return el.frame

    def settable(self, el, name):
        return (name == "AXSelected" and el.selectable) or (name == "AXValue" and el.role == "AXScrollBar")

    def set(self, el, name, value):
        self.sets.append((el, name, value))
        if name == "AXFocused":
            el.focused = value
        elif name == "AXSelected":
            el.selected = value
        elif name == "AXSelectedTextRange":
            el.range = value
        elif name == "AXValue":
            el.value = value
        return True

    def actions(self, el):
        return list(el.acts)

    def perform(self, el, action):
        self.performed.append((el, action))
        return True

    def index_at(self, el, x, y):
        return el._index_at(x, y) if el._index_at else None

    def scroll_bar(self, el, vertical):
        return el.vbar if vertical else None


def env(mac=None, **kw):
    mac = mac or FakeMac()
    kw.setdefault("confirm", None)
    e = BackgroundEnv("TextEdit", mac=mac, **kw)
    e.waiter.rules = {k: {**v, "floor": 0, "quiet": 0, "cap": 0.2, "min_same": 1, "poll": 0.01}
                      for k, v in e.waiter.rules.items()}
    return e, mac


def size(png):
    return Image.open(io.BytesIO(png)).size


# ------------------------------------------------------------ seeing
def test_the_screenshot_is_the_apps_front_window_only():
    e, m = env()
    obs = e.observe()
    assert (obs.width, obs.height) == (400, 300) and size(obs.screenshot) == (400, 300)
    assert m.captures[-1] == ([7], (100.0, 50.0, 400.0, 300.0))


def test_sheets_and_menus_over_the_window_are_in_the_screenshot_and_other_windows_are_not():
    e, m = env()
    m.wins = [m.wins[0],
              {"id": 8, "x": 150.0, "y": 80.0, "w": 200.0, "h": 100.0, "layer": 0},     # its second document
              {"id": 9, "x": 200.0, "y": 60.0, "w": 120.0, "h": 200.0, "layer": 101},   # a menu over it
              {"id": 10, "x": 900.0, "y": 900.0, "w": 50.0, "h": 50.0, "layer": 101}]   # elsewhere
    e.observe()
    assert m.captures[-1][0] == [7, 9]


def test_a_window_is_asked_for_once_when_the_app_has_none(monkeypatch):
    import legato.background as bg
    monkeypatch.setattr(bg.time, "sleep", lambda s: None)
    m = FakeMac()
    m.wins = []
    e, _ = env(m)
    with pytest.raises(RuntimeError, match="no window"):
        e.observe()
    assert m.reopened == 1


def test_coordinates_are_points_in_the_window():
    e, m = env()
    field = El("AXTextField")
    m.hits = [(100 + 10, 50 + 20, 100 + 60, 50 + 40, field)]
    e.observe()
    e.act(Action("click", x=30, y=30))
    assert field.focused


# ------------------------------------------------------------ typing and keys
def test_typing_is_posted_to_the_app_as_text():
    e, m = env()
    e.act(Action("type", text="Hé!\n"))
    assert [c for _, _, c in m.keys] == ["H", "é", "!", None]
    assert m.keys[-1][0] == KEYCODES["enter"]


def test_shortcuts_carry_their_flags_and_ctrl_is_cmd():
    e, m = env()
    e.act(Action("key", keys="ctrl+s"))
    e.act(Action("key", keys="super+shift+z"))
    e.act(Action("key", keys="Return"))
    assert m.keys == [(KEYCODES["s"], FLAGS["command"], None),
                      (KEYCODES["z"], FLAGS["command"] | FLAGS["shift"], None),
                      (KEYCODES["enter"], 0, None)]


def menu(m, *items):
    """A menu bar with items (title, cmd char, modifiers, enabled)."""
    bar = El("AXMenuBar")
    edit = El("AXMenu", bar)
    out = []
    for title, ch, mods, enabled in items:
        it = El("AXMenuItem", edit, actions=["AXPress"], title=title)
        it.extra = {"AXMenuItemCmdChar": ch, "AXMenuItemCmdModifiers": mods, "AXEnabled": enabled}
        out.append(it)
    m.app.extra = {"AXMenuBar": bar}
    return out


def test_menu_shortcuts_press_the_menu_item_since_posted_keys_never_reach_menus():
    e, m = env()
    select_all, redo, find = menu(m, ("Select All", "A", 0, True), ("Redo", "Z", 1, True), ("Find", "F", 0, False))
    e.act(Action("key", keys="ctrl+a"))                      # no text field in focus: the menu item
    e.act(Action("key", keys="super+shift+z"))
    assert m.performed == [(select_all, "AXPress"), (redo, "AXPress")] and m.keys == []
    with pytest.raises(safety.Unavailable, match="Find"):
        e.act(Action("key", keys="super+f"))                 # turned off right now
    e.act(Action("key", keys="super+Down"))                  # no menu item: a key press, as before
    assert m.keys == [(KEYCODES["down"], FLAGS["command"], None)]


def test_select_all_in_a_field_selects_its_text_without_the_menu():
    e, m = env()
    (select_all,) = menu(m, ("Select All", "A", 0, False))      # off while the app is in the back
    field = El("AXTextArea", value="hello world")
    m.app.extra["AXFocusedUIElement"] = field
    e.act(Action("key", keys="ctrl+a"))
    assert field.range == (0, 11) and m.performed == []


def test_copy_cut_and_paste_are_not_run_since_the_clipboard_is_the_persons():
    e, m = env()
    menu(m, ("Copy", "C", 0, True), ("Paste", "V", 0, True))
    for k in ("ctrl+c", "ctrl+x", "super+v", "super+shift+alt+v"):
        with pytest.raises(safety.Unavailable, match="clipboard"):
            e.act(Action("key", keys=k))
    assert m.performed == [] and m.keys == []


def test_a_key_with_no_code_or_a_lone_modifier_is_not_run():
    e, m = env()
    with pytest.raises(safety.Unavailable):
        e.act(Action("key", keys="shift"))
    with pytest.raises(safety.Unavailable):
        e.act(Action("key", keys="ctrl+XF86Calculator"))
    assert m.keys == []


# ------------------------------------------------------------ clicks through Accessibility
def tree():
    win = El("AXWindow")
    btn = El("AXButton", win, actions=["AXPress"], title="Save")
    label = El("AXStaticText", btn, title="Save label")              # a click on a button's label presses the button
    text = El("AXTextArea", win, value="hello world\nsecond line", index_at=lambda x, y: int(x - 300))
    row = El("AXRow", win, selectable=True)
    cell = El("AXStaticText", row)
    canvas = El("AXGroup", win)
    return win, btn, label, text, row, cell, canvas


def place(m, **els):
    """Lay elements out in 10-point-tall bands, in the order given, starting at the window's top."""
    m.hits = [(100, 50 + 10 * i, 500, 60 + 10 * i, e) for i, e in enumerate(els.values())]
    return {k: 5 + 10 * i for i, k in enumerate(els)}         # window-relative y of each band


def test_a_click_presses_the_button_under_it_even_on_its_label():
    e, m = env()
    win, btn, label, *_ = tree()
    y = place(m, label=label)
    e.observe()
    e.act(Action("click", x=50, y=y["label"]))
    assert m.performed == [(btn, "AXPress")]


def test_a_click_in_text_places_the_insertion_point_there():
    e, m = env()
    win, btn, label, text, *_ = tree()
    y = place(m, text=text)
    e.observe()
    e.act(Action("click", x=205, y=y["text"]))       # screen x 305: index 5
    assert text.focused and text.range == (5, 0)


def test_double_and_triple_clicks_in_text_select_the_word_and_the_line():
    e, m = env()
    win, btn, label, text, *_ = tree()
    y = place(m, text=text)
    e.observe()
    e.act(Action("double_click", x=208, y=y["text"]))      # index 8: inside "world"
    assert text.range == (6, 5)
    e.act(Action("triple_click", x=214, y=y["text"]))      # index 14: in "second line"
    assert text.range == (12, 11)


def test_a_click_on_a_row_selects_it_and_a_double_click_without_open_says_how_to_open():
    e, m = env()
    win, btn, label, text, row, cell, _ = tree()
    y = place(m, cell=cell)
    e.observe()
    e.act(Action("click", x=50, y=y["cell"]))
    assert row.selected
    with pytest.raises(safety.Unavailable, match="cmd\\+o"):
        e.act(Action("double_click", x=50, y=y["cell"]))
    row.acts.append("AXOpen")
    e.act(Action("double_click", x=50, y=y["cell"]))
    assert (row, "AXOpen") in m.performed


def test_a_click_on_nothing_pressable_is_not_run_and_says_what_was_there():
    e, m = env()
    *_, canvas = tree()
    y = place(m, canvas=canvas)
    e.observe()
    with pytest.raises(safety.Unavailable, match="AXGroup"):
        e.act(Action("click", x=50, y=y["canvas"]))
    with pytest.raises(safety.Unavailable, match="nothing"):
        e.act(Action("click", x=50, y=290))                # no element at all
    assert m.performed == [] and m.sets == []


def test_right_click_shows_the_menu_when_there_is_one():
    e, m = env()
    win, btn, *_ = tree()
    btn.acts.append("AXShowMenu")
    y = place(m, btn=btn)
    e.observe()
    e.act(Action("right_click", x=50, y=y["btn"]))
    assert m.performed == [(btn, "AXShowMenu")]
    btn.acts.remove("AXShowMenu")
    with pytest.raises(safety.Unavailable, match="menu"):
        e.act(Action("right_click", x=50, y=y["btn"]))


def test_drags_and_clicks_with_keys_held_are_not_run():
    e, m = env()
    win, btn, *_ = tree()
    y = place(m, btn=btn)
    e.observe()
    with pytest.raises(safety.Unavailable, match="drag"):
        e.act(Action("drag", x=1, y=1, x2=50, y2=50))
    with pytest.raises(safety.Unavailable, match="keys held"):
        e.act(Action("click", x=50, y=y["btn"], keys="shift"))
    assert m.performed == []


def test_scroll_moves_the_scroll_bar_of_the_area_under_the_point():
    e, m = env()
    bar = El("AXScrollBar", value=0.5)
    area = El("AXScrollArea", vbar=bar)
    inner = El("AXStaticText", area)
    y = place(m, inner=inner)
    e.observe()
    e.act(Action("scroll", x=50, y=y["inner"], direction="down", amount=2))
    assert bar.value == pytest.approx(0.7)
    e.act(Action("scroll", x=50, y=y["inner"], direction="up", amount=10))
    assert bar.value == 0.0
    with pytest.raises(safety.Unavailable, match="Page Down"):
        e.act(Action("scroll", x=50, y=290, direction="down"))


# ------------------------------------------------------------ the person's computer stays theirs
def test_nothing_reaches_the_pointer_the_front_app_or_the_clipboard(monkeypatch):
    import subprocess
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a) or subprocess.CompletedProcess(a, 0, b""))
    e, m = env()
    win, btn, label, text, *_ = tree()
    y = place(m, btn=btn, text=text)
    e.observe()
    for a in (Action("click", x=50, y=y["btn"]), Action("click", x=205, y=y["text"]), Action("type", text="hi"),
              Action("key", keys="ctrl+a"), Action("wait")):
        e.act(a)
        assert "front_taken" not in e.last
    assert calls == []                         # no pbcopy, no osascript, no `open` to bring anything forward
    assert m.cursor == (500.0, 500.0) and m.front == 999


def test_when_the_app_takes_the_front_it_is_given_back():
    e, m = env()
    win, btn, *_ = tree()
    y = place(m, btn=btn)
    e.observe()
    orig = m.perform

    def steal(el, action):
        m.front = m.pid
        return orig(el, action)
    m.perform = steal
    e.act(Action("click", x=50, y=y["btn"]))
    assert m.activated == [999] and m.front == 999 and "given back" in e.last["front_taken"]


def test_opening_a_page_keeps_the_persons_app_in_front():
    e, m = env()
    m.open_url = lambda url: (m.urls.append(url), setattr(m, "front", m.pid))
    e.open_url("http://example.test/")
    assert m.urls == ["http://example.test/"] and m.front == 999 and m.activated == [999]


def page():
    """A web page as Chrome reports it: an inline link whose frame spans the whole line, a button on that line."""
    win = El("AXWindow")
    area = El("AXScrollArea", win, frame=(100, 50, 400, 300))
    web = El("AXWebArea", area, frame=(100, 50, 400, 300))
    link = El("AXLink", web, actions=["AXPress", "AXScrollToVisible"], frame=(100, 60, 400, 30))
    text = El("AXStaticText", link, frame=(100, 60, 400, 30))
    btn = El("AXButton", web, actions=["AXPress", "AXScrollToVisible"], frame=(400, 62, 80, 26))
    field = El("AXTextField", web, actions=["AXPress"], frame=(100, 100, 300, 30))
    below = [El("AXParagraph", web, actions=["AXScrollToVisible"], frame=(100, 350 + 40 * i, 400, 30))
             for i in range(5)]
    return web, link, text, btn, field, below


def test_in_a_web_page_the_smallest_element_under_the_click_wins():
    e, m = env()
    web, link, text, btn, field, below = page()
    m.hits = [(100, 60, 500, 90, text)]                      # Chrome's hit test: the link's text, even over the button
    e.observe()
    e.act(Action("click", x=340, y=25))                      # screen (440, 75): on the button
    assert m.performed == [(btn, "AXPress")]
    e.act(Action("click", x=50, y=25))                       # screen (150, 75): on the link alone
    assert m.performed[-1] == (link, "AXPress")


def test_a_web_page_with_no_scroll_bar_scrolls_by_bringing_the_next_element_into_view():
    e, m = env()
    web, link, text, btn, field, below = page()
    m.hits = [(100, 50, 500, 350, web)]
    e.observe()
    e.act(Action("scroll", x=200, y=200, direction="down", amount=2))     # about 60 points past the bottom (350)
    assert m.performed == [(below[1], "AXScrollToVisible")]               # its bottom edge, 420, is nearest 410


def test_scroll_finds_the_page_when_the_hit_test_comes_back_empty():
    e, m = env()
    web, link, text, btn, field, below = page()
    m.app.extra = {"AXWindows": [web.parent.parent]}
    m.hits = []                                              # Chrome: nothing at that point
    e.observe()
    e.act(Action("scroll", x=200, y=200, direction="down", amount=2))
    assert m.performed == [(below[1], "AXScrollToVisible")]


def test_a_web_page_that_shows_nothing_past_its_edge_scrolls_with_arrow_keys_once_focused():
    e, m = env()
    web, link, text, btn, field, below = page()
    for b in below:                                          # Chrome: nothing below the visible part
        web.children.remove(b)
    m.hits = [(100, 50, 500, 350, web)]
    e.observe()
    e.act(Action("scroll", x=200, y=200, direction="down", amount=2))
    assert web.focused and m.keys == [(KEYCODES["down"], 0, None)] * 6


def test_a_scroll_area_with_no_bar_scrolls_a_page_when_it_can():
    e, m = env()
    area = El("AXScrollArea", actions=["AXScrollDownByPage", "AXScrollUpByPage"])
    inner = El("AXTextArea", area)
    y = place(m, inner=inner)
    e.observe()
    e.act(Action("scroll", x=50, y=y["inner"], direction="down"))
    assert m.performed == [(area, "AXScrollDownByPage")]


def test_the_persons_stop_signals_do_not_apply():
    e, _ = env()
    assert e.stop_requested() is None


def test_risky_keys_still_wait_for_a_yes():
    asked = []
    e, m = env(confirm=lambda what: asked.append(what) or False)
    with pytest.raises(safety.Denied) as err:
        e.act(Action("key", keys="super+q"))
    assert not isinstance(err.value, safety.Unavailable)
    assert asked and m.keys == []


# ------------------------------------------------------------ what the model and the MCP client are told
def test_the_model_is_told_it_was_not_possible_not_refused():
    note = _refused(safety.Unavailable("drag: background mode can't drag"))
    assert note == UNAVAILABLE_NOTE.format(what="drag: background mode can't drag")
    assert "person" not in note
    assert "did not allow" in _refused(safety.Denied("x"))


def test_the_harness_tells_the_model_and_carries_on_after_an_unavailable_action(server):  # noqa: F811
    import legato
    server["script"] = [
        {"tool_calls": [tc({"action": "left_click_drag", "coordinate": [100, 100], "end_coordinate": [500, 500]}),
                        tc({"action": "type", "text": "skipped"})]},
        {"tool_calls": [tc({"action": "type", "text": "ok"})]},
    ]
    e, m = env()
    res = legato.Harness("m", server["url"]).run("t", e, max_steps=5)
    assert res.done and not res.stopped
    assert res.actions[0].startswith("not possible: drag") and res.actions[1] == 'type "ok"'
    assert [c for _, _, c in m.keys] == ["o", "k"]               # the type after the drag did not run
    told = [p["text"] for p in server["bodies"][1]["messages"][-1]["content"] if p["type"] == "text"]
    assert any("can't do that action" in t and "drag" in t for t in told)
    assert not any("did not allow" in t for t in told)


def test_mcp_background_desktop_defaults_to_the_browser_and_reports_the_window(monkeypatch):
    pytest.importorskip("mcp")
    import asyncio

    from legato import config
    from legato.mcp_server import build_server
    m = FakeMac()
    opened = []
    monkeypatch.setattr(config, "default_browser", lambda: "Google Chrome")
    srv = build_server(lambda res: (_ for _ in ()).throw(AssertionError("no full-screen desktop")),
                       background=lambda app: opened.append(app) or BackgroundEnv(app, mac=m, confirm=None))

    async def call(name, **args):
        return await srv.call_tool(name, args)
    asyncio.run(call("desktop_open"))                       # no app: Safari (Chrome 158 ignores background clicks)
    assert opened == ["Safari"]
    out = asyncio.run(call("desktop_open", app="TextEdit"))
    text = out.content[0].text
    assert "(400x300)" in text and "background" in text and out.content[1].type == "image"
    did = text.split("desktop_id: ")[1].split()[0]
    with pytest.raises(Exception, match="can't do that"):
        asyncio.run(call("desktop_act", desktop_id=did, action="drag", x=1, y=1, end_x=9, end_y=9))


def test_local_with_background_returns_the_background_desktop(monkeypatch):
    import legato.background as bg
    made = []
    monkeypatch.setattr(bg, "MacApp", lambda app, **kw: made.append(app) or FakeMac())
    monkeypatch.setattr(bg.sys, "platform", "darwin")
    import legato
    e = legato.local(background="Safari", confirm=None)
    assert isinstance(e, BackgroundEnv) and made == ["Safari"]


def test_background_mode_is_macos_only(monkeypatch):
    import legato.background as bg
    monkeypatch.setattr(bg.sys, "platform", "linux")
    with pytest.raises(RuntimeError, match="macOS only"):
        BackgroundEnv("TextEdit")


def test_hide_and_minimize_keys_are_not_run():
    from legato.actions import Action
    from legato.safety import Unavailable
    m = FakeMac()
    env = BackgroundEnv("TextEdit", mac=m, confirm=None)
    for keys in ("cmd+h", "ctrl+m", "cmd+alt+h"):
        with pytest.raises(Unavailable, match="hide or minimize"):
            env.act(Action("key", keys=keys))
    assert m.keys == []


def test_a_hidden_app_is_shown_again_not_reopened():
    m = FakeMac()
    wins, m.wins = m.wins, []
    shown = []

    def unhide():
        shown.append(1)
        m.wins = wins
        return True
    m.unhide = unhide
    env = BackgroundEnv("TextEdit", mac=m, confirm=None)
    assert env.window()["id"] == 7 and shown and m.reopened == 0


def test_an_app_that_is_not_open_yet_is_waited_for_with_the_run_loop(monkeypatch):
    """NSWorkspace only sees an app opened after the first look if the run loop runs: the wait must pump it, not
    sleep (a first run with Safari closed failed with "Safari did not start")."""
    import legato.background as bg
    looks, pumps = iter([None, None, 4321]), []
    app = bg.MacApp.__new__(bg.MacApp)
    app.name, app.flag = "Safari", "-a"
    monkeypatch.setattr(bg.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(app, "_find", lambda: next(looks))
    monkeypatch.setattr(bg, "_pump", lambda s: pumps.append(s))
    assert app._launch(5) == 4321 and pumps == [0.2, 0.2]
    looks = iter([])
    monkeypatch.setattr(app, "_find", lambda: None)
    monkeypatch.setattr(bg.time, "monotonic", iter([0.0, 0.0, 9.0]).__next__)
    with pytest.raises(RuntimeError, match="did not open within 5 s"):
        app._launch(5)


def test_a_stalled_window_capture_falls_back_to_screencapture(monkeypatch):
    """macOS's window capture can hang 30 s and return nothing while its capture service is busy; `screencapture`
    still works. The API gets a short wait, then the fallback is used for a while without trying the API again."""
    import time
    import legato.background as bg
    calls = {"api": 0, "fallback": 0}

    class Q:
        kCGWindowImageBoundsIgnoreFraming = kCGWindowImageNominalResolution = 0

        @staticmethod
        def CGRectMake(*r):
            return r

        @staticmethod
        def CGWindowListCreateImageFromArray(rect, ids, opts):
            calls["api"] += 1
            time.sleep(0.3)                       # stalls past the wait below
            return None

    app = bg.MacApp.__new__(bg.MacApp)
    app.Q = Q
    app.windows = lambda: []
    monkeypatch.setattr(bg.MacApp, "API_WAIT_S", 0.05)
    monkeypatch.setattr(bg, "_screencapture", lambda wid, rect, hosts, scale: calls.__setitem__(
        "fallback", calls["fallback"] + 1) or f"png {wid} {rect}".encode())
    assert app.capture([7, 8], (10, 20, 300, 200)) == b"png 7 (10, 20, 300, 200)"
    assert app.capture([7], (10, 20, 300, 200)) == b"png 7 (10, 20, 300, 200)"
    assert calls == {"api": 1, "fallback": 2}      # the API was tried once, then left alone
    monkeypatch.setattr(bg, "_screencapture", lambda wid, rect, hosts, scale: None)
    with pytest.raises(RuntimeError, match="capture is busy"):
        app.capture([7], (10, 20, 300, 200))


def test_menus_go_in_front_of_the_window_in_the_capture(monkeypatch):
    """The window capture API draws the first window of its list on top: with the window first, an open menu or
    popover was hidden behind it (seen with a Chess pop-up). The fallback still takes the window itself, and is told
    which of the app's windows could hold it (a sheet's `screencapture` is its whole parent window)."""
    import legato.background as bg
    seen, taken = [], []

    class Q:
        kCGWindowImageBoundsIgnoreFraming = kCGWindowImageNominalResolution = 0

        @staticmethod
        def CGRectMake(*r):
            return r

        @staticmethod
        def CGWindowListCreateImageFromArray(rect, ids, opts):
            seen.append(list(ids))
            return None                           # then the fallback, which shows what it was given

    app = bg.MacApp.__new__(bg.MacApp)
    app.Q = Q
    app.windows = lambda: [
        {"id": 9, "x": 130, "y": 70, "w": 60, "h": 40, "layer": 101},      # a menu over the sheet
        {"id": 7, "x": 120, "y": 60, "w": 40, "h": 30, "layer": 0},        # the sheet: the front window
        {"id": 3, "x": 100, "y": 50, "w": 100, "h": 80, "layer": 0},       # the window it hangs from
        {"id": 4, "x": 500, "y": 50, "w": 100, "h": 80, "layer": 0}]       # another window, elsewhere
    app.scale_at = lambda x, y: 2.0
    monkeypatch.setattr(bg, "_screencapture", lambda *a: taken.append(a) or b"png")
    assert app.capture([7, 9, 8], (120, 60, 40, 30)) == b"png"
    assert seen == [[9, 8, 7]]
    assert taken == [(7, (120, 60, 40, 30), [(100, 50, 100, 80)], 2.0)]


def _fake_screencapture(monkeypatch, im):
    import legato.background as bg

    def run(cmd, **kw):
        im.save(cmd[-1], "PNG")

        class R:
            returncode = 0
        return R()
    monkeypatch.setattr(bg.subprocess, "run", run)


def test_a_sheet_is_cut_out_of_the_window_screencapture_takes(monkeypatch):
    """`screencapture -l<sheet>` gives the whole window the sheet hangs from: squeezed to the sheet's size, the model's
    coordinates were wrong. The sheet's part is cut out instead."""
    import legato.background as bg
    host, sheet = (100, 50, 100, 80), (120, 60, 40, 30)
    im = Image.new("RGB", (200, 160), (255, 0, 0))                 # the host at 2 pixels per point
    im.paste((0, 0, 255), (40, 20, 120, 80))                       # the sheet, at (20, 10) points in it
    _fake_screencapture(monkeypatch, im)
    out = Image.open(io.BytesIO(bg._screencapture(7, sheet, [host], 2.0))).convert("RGB")
    assert out.size == (40, 30)
    assert {out.getpixel(p) for p in [(0, 0), (39, 0), (0, 29), (39, 29), (20, 15)]} == {(0, 0, 255)}


def test_a_window_screencapture_takes_is_only_scaled_to_points(monkeypatch):
    """A window that only sits inside another of the app's windows (no sheet) comes back as itself: it is not cut."""
    import legato.background as bg
    im = Image.new("RGB", (80, 60), (0, 255, 0))
    im.paste((0, 0, 255), (0, 0, 40, 60))
    _fake_screencapture(monkeypatch, im)
    out = Image.open(io.BytesIO(bg._screencapture(7, (120, 60, 40, 30), [(100, 50, 100, 80)], 2.0))).convert("RGB")
    assert out.size == (40, 30) and out.getpixel((5, 15)) == (0, 0, 255) and out.getpixel((35, 15)) == (0, 255, 0)
    out = Image.open(io.BytesIO(bg._screencapture(7, (120, 60, 40, 30)))).convert("RGB")      # no scale: as before
    assert out.size == (40, 30)


def test_an_app_missing_from_the_stale_list_is_found_by_bundle_id(monkeypatch):
    """In the MCP server (tools on worker threads) NSWorkspace's list can stay stale: a lookup by bundle id is fresh."""
    import sys
    import types
    import legato.background as bg

    class App:
        def processIdentifier(self):
            return 4242
    fake_appkit = types.SimpleNamespace(NSRunningApplication=types.SimpleNamespace(
        runningApplicationsWithBundleIdentifier_=lambda bid: [App()] if bid == "com.example.Chess" else []))
    monkeypatch.setitem(sys.modules, "AppKit", fake_appkit)
    app = bg.MacApp.__new__(bg.MacApp)
    app.name, app.flag, app._bid = "Chess", "-a", "com.example.Chess"
    app.ws = types.SimpleNamespace(runningApplications=lambda: [])          # the stale list: nothing
    assert app._find() == 4242
    app._bid = None
    assert app._find() is None


# ------------------------------------------------------------ the title bar's buttons
def _window_with_buttons(m):
    """The fake window as Accessibility shows it, with its close, minimize and zoom buttons at the usual places."""
    win = El("AXWindow", m.app, frame=(100.0, 50.0, 400.0, 300.0))
    buttons = {}
    for i, name in enumerate(("AXCloseButton", "AXMinimizeButton", "AXZoomButton")):
        b = El("AXButton", win, actions=["AXPress"], frame=(108.0 + 20 * i, 56.0, 14.0, 16.0))
        b.extra = {"AXSubrole": name}
        buttons[name] = b
    win.extra = dict(buttons)
    m.app.extra = {"AXWindows": [win]}
    return win, buttons


def test_the_windows_close_minimize_and_zoom_buttons_are_not_pressed():
    """A model clicked TextEdit's zoom button after finishing a task (click (59, 20)), and TextEdit stopped
    answering. Like cmd+H and cmd+M, the title bar's buttons are refused, found through the window's own attributes."""
    e, m = env()
    win, buttons = _window_with_buttons(m)
    e.observe()
    for x, (name, shown) in zip((15, 35, 55), (("AXCloseButton", "close"), ("AXMinimizeButton", "minimize"),
                                               ("AXZoomButton", "zoom"))):
        m.hits = [(100, 50, 500, 350, buttons[name])]
        with pytest.raises(safety.Unavailable, match=f"window's {shown} button"):
            e.act(Action("click", x=x, y=14))
    m.hits = [(100, 50, 500, 350, win)]                     # the hit test answers with the window: frames decide
    with pytest.raises(safety.Unavailable, match="zoom button"):
        e.act(Action("double_click", x=59, y=12))
    assert m.performed == []


def test_a_close_button_inside_the_window_is_still_pressed():
    e, m = env()
    win, _ = _window_with_buttons(m)
    tab = El("AXGroup", win)
    close_tab = El("AXButton", tab, actions=["AXPress"], frame=(400.0, 60.0, 16.0, 16.0))
    close_tab.extra = {"AXSubrole": "AXCloseButton"}       # a tab's close button: not the window's
    m.hits = [(400, 60, 416, 76, close_tab)]
    e.observe()
    e.act(Action("click", x=305, y=15))
    assert m.performed == [(close_tab, "AXPress")]


# ------------------------------------------------------------ reading the window's text
def _page(m):
    """A browser window: its toolbar, then a page with a heading, a counter, a button, a field and a checkbox."""
    win, _ = _window_with_buttons(m)
    win.title = "Counter - Google Chrome"
    bar = El("AXToolbar", win)
    El("AXButton", bar, actions=["AXPress"], title="Back")
    holder = El("AXGroup", win)
    page = El("AXWebArea", holder, title="Counter")
    page.extra = {"AXURL": "file:///tmp/app/index.html"}
    h = El("AXHeading", page)
    El("AXStaticText", h, value="Counter")
    box = El("AXGroup", page)
    El("AXStaticText", box, value="Count: 1")
    plus = El("AXButton", page, actions=["AXPress"], title="+1")
    El("AXStaticText", plus, value="+1")                     # the button's own text: not repeated
    name = El("AXTextField", page, title="Name", value="Ada")
    name.extra = {"AXFocused": True}
    agree = El("AXCheckBox", page, title="agree", value=1)
    off = El("AXButton", page, actions=["AXPress"], title="Send")
    off.extra = {"AXEnabled": False}
    El("AXStaticText", page, value="  ")                     # nothing to say
    return win, page, (name, agree)


def test_read_outlines_the_page_and_leaves_out_the_browser_toolbar():
    e, m = env()
    _page(m)
    assert e.read() == "\n".join([
        'page "Counter" file:///tmp/app/index.html',
        '- heading "Counter"',
        '- text "Count: 1"',
        '- button "+1"',
        '- textbox "Name": "Ada" [focused]',
        '- checkbox "agree" [checked]',
        '- button "Send" [disabled]'])


def test_read_outlines_an_apps_window_and_nests_what_says_something():
    e, m = env()
    win, _ = _window_with_buttons(m)
    win.title = "Untitled"
    scroll = El("AXScrollArea", win)
    El("AXTextArea", scroll, value="passport\nphone charger")
    rows = El("AXList", win, title="Recent")
    row = El("AXRow", rows)
    row.extra = {"AXSelected": True}
    El("AXStaticText", row, value="note.txt")
    field = El("AXTextField", win, title="Search", value="")
    field.extra = {"AXPlaceholderValue": "Search notes"}
    out = e.read().splitlines()
    assert out[0] == 'window "Untitled"'
    assert '- textarea' not in out and '- textbox: "passport\\nphone charger"' in out
    assert '- list "Recent"' in out and '  - row "note.txt" [selected]' in out
    assert '- textbox "Search": "" [placeholder "Search notes"]' in out


def test_read_is_cut_at_max_chars_and_says_so():
    e, m = env()
    win, _ = _window_with_buttons(m)
    for i in range(200):
        El("AXStaticText", win, value=f"line {i}")
    out = e.read(max_chars=300)
    assert len(out) <= 300 and out.splitlines()[-1].startswith("[cut at 300 characters")
    assert '- text "line 0"' in out and '- text "line 199"' not in out
    out = e.read(max_chars=120)                             # small ones too: a tester asked for 120 and got 200
    assert len(out) <= 120 and out.endswith("[cut]") and '- text "line 0"' in out
    assert len(e.read(max_chars=5)) == 5


def test_read_leaves_out_textedits_ruler_and_a_headings_level():
    e, m = env()
    win, _ = _window_with_buttons(m)
    ruler = El("AXRuler", win)
    for v in (0, 0.5, 1):
        El("AXRulerMarker", ruler, value=v)
    El("AXTextArea", win, value="hi")
    h = El("AXHeading", win, value=1)
    El("AXStaticText", h, value="Title")
    assert e.read().splitlines()[1:] == ['- textbox: "hi"', '- heading "Title"']


def test_read_says_so_when_the_window_has_no_tree():
    e, m = env()
    _window_with_buttons(m)
    assert "Accessibility tree is empty" in e.read()


def test_mcp_desktop_read_reads_a_background_window_and_is_refused_elsewhere():
    pytest.importorskip("mcp")
    import asyncio

    import legato
    from legato.mcp_server import build_server
    m = FakeMac()
    _page(m)
    srv = build_server(lambda res: (None, legato.attach(screenshot=lambda: b"", execute=lambda a: None)),
                       background=lambda app: BackgroundEnv(app, mac=m, confirm=None))

    async def go():
        r = await srv.call_tool("desktop_open", {"app": "Google Chrome"})
        did = r.content[0].text.split("desktop_id: ")[1].split()[0]
        r = await srv.call_tool("desktop_read", {"desktop_id": did})
        assert r.content[0].text.splitlines()[:3] == ['page "Counter" file:///tmp/app/index.html',
                                                      '- heading "Counter"', '- text "Count: 1"']
        r = await srv.call_tool("desktop_read", {"desktop_id": did, "max_chars": 60})
        assert len(r.content[0].text) <= 60 and r.content[0].text.endswith("[cut]")
        with pytest.raises(Exception, match="no open desktop"):
            await srv.call_tool("desktop_read", {"desktop_id": "nope"})
    asyncio.run(go())

    from test_mcp import png
    srv = build_server(lambda res: (None, legato.attach(screenshot=lambda: png(*res), execute=lambda a: None,
                                                        fixed_wait_s=0.0)))

    async def elsewhere():
        r = await srv.call_tool("desktop_open", {"width": 64, "height": 40})
        did = r.content[0].text.split()[1]
        with pytest.raises(Exception, match="only in background mode"):
            await srv.call_tool("desktop_read", {"desktop_id": did})
    asyncio.run(elsewhere())


def test_an_app_that_does_not_exist_fails_at_once(monkeypatch):
    """`open -a NoSuchApp` exits 1 right away: say so, instead of waiting 30 s for it to appear."""
    import subprocess
    import legato.background as bg
    app = bg.MacApp.__new__(bg.MacApp)
    app.name, app.flag = "NoSuchApp123", "-a"
    monkeypatch.setattr(bg.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a, 1, "", "Unable to find application named 'NoSuchApp123'\n"))
    monkeypatch.setattr(app, "_find", lambda: pytest.fail("should not wait for an app that can't open"))
    with pytest.raises(RuntimeError, match="NoSuchApp123 could not be opened: Unable to find application"):
        app._launch(30)


def test_a_stall_is_remembered_by_the_next_process(monkeypatch, tmp_path):
    """Each `legato run` is a new process: without a shared note, every run would wait the API's 3 s again."""
    import legato.background as bg
    monkeypatch.setattr(bg, "_stall_file", lambda: tmp_path / "capture-stalled")
    assert bg._stall_left(300) == 0.0
    bg._note_stall()
    assert 290 < bg._stall_left(300) <= 300
    app = bg.MacApp.__new__(bg.MacApp)
    app.Q = object()                                   # the API must not be called at all
    monkeypatch.setattr(bg, "_screencapture", lambda *a, **k: b"png")
    monkeypatch.setattr(app, "windows", lambda: [], raising=False)
    assert app.capture([7], (0, 0, 100, 80)) == b"png"


def test_a_chrome_page_control_that_ignores_background_presses_says_so_and_suggests_safari():
    """Chrome 158's page elements offer no press from the back: say so instead of "the AXButton can't be pressed"."""
    m = FakeMac()
    e = BackgroundEnv("Google Chrome", mac=m, confirm=None)
    page = El("AXWebArea", frame=(100.0, 50.0, 400.0, 300.0))
    button = El("AXButton", page, actions=["AXShowMenu", "AXScrollToVisible"], title="+1", frame=(150, 100, 40, 20))
    m.hits = [(150, 100, 190, 120, button)]
    e.observe()
    with pytest.raises(safety.Unavailable, match="Chrome ignores background clicks on web pages.*Safari"):
        e.act(Action("click", x=60, y=60))
    e2 = BackgroundEnv("TextEdit", mac=m, confirm=None)
    e2.observe()
    with pytest.raises(safety.Unavailable, match="the AXButton at that point can't be pressed"):
        e2.act(Action("click", x=60, y=60))
    assert m.performed == []


# ------------------------------------------------------------ which window is "the window"
def _doc_and_bubble(m, listed_bubble=False):
    """TextEdit's document window with its autocorrect bubble (a window of its own, 120x46, in front of it)."""
    doc = El("AXWindow", m.app, frame=(100.0, 50.0, 400.0, 300.0))
    doc.extra = {"AXSubrole": "AXStandardWindow"}
    m.wins = [{"id": 9, "x": 180.0, "y": 120.0, "w": 120.0, "h": 46.0, "layer": 0}] + m.wins
    listed = [doc]
    if listed_bubble:                                      # Chrome lists its help bubble, as AXUnknown
        bubble = El("AXWindow", m.app, frame=(180.0, 120.0, 120.0, 46.0))
        bubble.extra = {"AXSubrole": "AXUnknown"}
        listed.insert(0, bubble)
    m.app.extra = {"AXWindows": listed}
    return doc


def test_a_bubble_in_front_is_not_taken_for_the_window_but_shows_over_it():
    """TextEdit's autocorrect bubble (120x46) became "the window": the run's last screen and the next desktop_open
    were 120x46."""
    for listed in (False, True):
        e, m = env()
        _doc_and_bubble(m, listed_bubble=listed)
        obs = e.observe()
        assert (obs.width, obs.height) == (400, 300) and e.win["id"] == 7
        assert m.captures[-1] == ([7, 9], (100.0, 50.0, 400.0, 300.0))      # the bubble drawn over the window


def test_a_sheet_in_front_is_still_the_window():
    e, m = env()
    doc = El("AXWindow", m.app, frame=(100.0, 50.0, 400.0, 300.0))
    doc.extra = {"AXSubrole": "AXStandardWindow"}
    El("AXSheet", doc, frame=(150.0, 72.0, 300.0, 150.0))
    m.app.extra = {"AXWindows": [doc]}
    m.wins = [{"id": 8, "x": 150.0, "y": 72.0, "w": 300.0, "h": 150.0, "layer": 0}] + m.wins
    e.observe()
    assert e.win["id"] == 8


def test_a_window_that_closes_before_its_capture_is_looked_for_again():
    """After Escape closed the bubble, the next capture failed and blamed Screen Recording: look again instead."""
    e, m = env()
    m.wins = [{"id": 9, "x": 180.0, "y": 120.0, "w": 120.0, "h": 46.0, "layer": 0}] + m.wins   # nothing listed
    taken = m.capture

    def capture(ids, rect):
        if ids[0] == 9:
            m.wins = m.wins[1:]                            # it closed just now
            raise RuntimeError("the window could not be captured: check Screen Recording")
        return taken(ids, rect)
    m.capture = capture
    obs = e.observe()
    assert (obs.width, obs.height) == (400, 300) and e.win["id"] == 7
    m.capture = lambda ids, rect: (_ for _ in ()).throw(RuntimeError("Screen Recording is off"))
    with pytest.raises(RuntimeError, match="Screen Recording"):           # still there: the error stands
        e.observe()


# ------------------------------------------------------------ fonts can't change from the back
def test_bold_and_italic_are_refused_since_the_text_would_not_change():
    """TextEdit in the back: cmd+B's menu item is off, and its format bar's Bold looks switched on while the text stays
    Helvetica (checked through AXAttributedStringForRange). A model reported "now bold" twice. Underline and
    alignment do apply, so they still run."""
    e, m = env()
    menu(m, ("Bold", "B", 0, False), ("Underline", "U", 0, False))
    with pytest.raises(safety.Unavailable, match="changing the font .* isn't possible in the background.*Don't report"):
        e.act(Action("key", keys="cmd+b"))
    with pytest.raises(safety.Unavailable, match="Underline.*turned off.*do it another way"):
        e.act(Action("key", keys="cmd+u"))
    win = El("AXWindow")
    bar = {}
    for i, name in enumerate(("bold", "italic", "underline", "align center")):
        bar[name] = El("AXCheckBox", win, actions=["AXPress"], frame=(110.0 + 30 * i, 70.0, 20.0, 20.0))
        bar[name].extra = {"AXDescription": name}
    size = El("AXPopUpButton", win, actions=["AXPress"], title="", frame=(300.0, 70.0, 40.0, 20.0))
    size.extra = {"AXDescription": "font size"}
    m.hits = [(f[0], f[1], f[0] + f[2], f[1] + f[3], el)
              for el in (*bar.values(), size) for f in [el.frame]]
    e.observe()
    for x in (20, 50, 210):                                  # bold, italic, font size
        with pytest.raises(safety.Unavailable, match="isn't possible in the background"):
            e.act(Action("click", x=x, y=30))
    e.act(Action("click", x=80, y=30))                       # underline
    e.act(Action("click", x=110, y=30))                      # align center
    assert m.performed == [(bar["underline"], "AXPress"), (bar["align center"], "AXPress")]


# ------------------------------------------------------------ desktop_open(url=...): a window of its own
class Browser(FakeMac):
    """A browser whose File > New Window (cmd+N) opens a window with an address field that loads what is typed."""

    def __init__(self, navigates=True):
        super().__init__()
        self.navigates = navigates
        person = El("AXWindow", frame=(100.0, 50.0, 400.0, 300.0))
        person.extra = {"AXSubrole": "AXStandardWindow"}
        self.new_window, = menu(self, ("New Window", "N", 0, True))
        self.app.extra["AXWindows"] = [person]
        self.closed = []

    def perform(self, el, action):
        super().perform(el, action)
        if el is self.new_window:
            win = El("AXWindow", frame=(120.0, 60.0, 400.0, 300.0))
            close = El("AXButton", win, actions=["AXPress"])
            win.extra = {"AXSubrole": "AXStandardWindow", "AXCloseButton": close}
            bar = El("AXToolbar", win)
            El("AXTextField", bar, actions=["AXConfirm"])
            self.app.extra["AXWindows"].insert(0, win)
            self.wins.insert(0, {"id": 20, "x": 120.0, "y": 60.0, "w": 400.0, "h": 300.0, "layer": 0})
        elif action == "AXConfirm" and self.navigates:
            page = El("AXWebArea", el.parent.parent)
            page.extra = {"AXURL": el.value, "AXLoaded": True}
        elif action == "AXPress" and el.role == "AXButton" and el.parent.role == "AXWindow":
            self.closed.append(el.parent)
        return True


def test_a_page_opens_in_a_window_of_its_own_ready_for_clicks_and_closes_with_the_desktop():
    """Safari put the page in the person's window as a tab (their other tabs' titles reached the model), dropped a
    query string, took 15.7 s, left the address field focused so the first click on the page was refused, and
    desktop_close left the tab."""
    m = Browser()
    e = BackgroundEnv("Safari", mac=m, confirm=None)
    m.open_url = lambda url: pytest.fail("no tab in the person's window")
    t = time.monotonic()
    e.open_url("file:///tmp/app/index.html?run=2")
    assert time.monotonic() - t < 1.0
    new = m.app.extra["AXWindows"][0]
    field = new.children[1].children[0]
    page = next(c for c in new.children if c.role == "AXWebArea")
    assert field.value == "file:///tmp/app/index.html?run=2" and page.focused is True     # the page has the keyboard
    assert e.window()["id"] == 20 and m.front == 999 and m.activated == []
    assert e.read().splitlines()[0] == 'page "Safari" file:///tmp/app/index.html?run=2'      # the query kept
    e.close()
    assert m.closed == [new] and e.opened == []
    e.close()                                               # once only
    assert m.closed == [new]


def test_a_browser_whose_new_window_does_not_load_gets_the_url_as_a_tab_as_before():
    m = Browser(navigates=False)
    e = BackgroundEnv("Safari", mac=m, confirm=None)
    m.open_url = lambda url: m.urls.append(url)
    e.open_url("http://example.test/", wait_s=0.2)
    assert m.urls == ["http://example.test/"] and len(e.opened) == 1     # the window is still closed afterwards


def test_mcp_desktop_close_closes_the_window_desktop_open_opened(monkeypatch):
    pytest.importorskip("mcp")
    import asyncio

    from legato.mcp_server import build_server
    m = Browser()
    srv = build_server(lambda res: pytest.fail("no full-screen desktop"),
                       background=lambda app: BackgroundEnv(app, mac=m, confirm=None))

    async def go():
        r = await srv.call_tool("desktop_open", {"app": "Safari", "url": "http://example.test/?q=1"})
        did = r.content[0].text.split("desktop_id: ")[1].split()[0]
        await srv.call_tool("desktop_close", {"desktop_id": did})
    asyncio.run(go())
    assert len(m.closed) == 1


def test_a_new_windows_own_page_is_not_taken_for_the_page_asked_for():
    from legato.background import _is_page
    assert not _is_page("chrome://newtab/", "https://example.com") and not _is_page("favorites://", "file:///a.html")
    assert not _is_page("", "https://example.com") and _is_page("https://example.com/", "https://example.com")
    assert _is_page("https://www.example.com/login", "http://example.com")         # where it led
    assert _is_page("about:blank", "about:blank")


def test_a_cold_start_waits_for_its_first_window_within_the_launch_time(monkeypatch):
    """An app Legato just opened can show its window seconds after its process: wait for it until the launch time
    is up, instead of the 5 s an app that was already running gets, and don't call it an error."""
    import legato.background as bg
    t = {"now": 0.0}
    monkeypatch.setattr(bg.time, "monotonic", lambda: t["now"])
    monkeypatch.setattr(bg.time, "sleep", lambda s: t.__setitem__("now", t["now"] + s))
    win = {"id": 1, "layer": 0, "x": 0, "y": 0, "w": 300, "h": 200}

    class Mac:
        window_until = 20.0                     # opened by Legato, with 20 s of launch time left
        def windows(self):
            return [win] if t["now"] >= 8.0 else []   # the window shows up 8 s in
        def reopen(self):
            pass
        def unhide(self):
            return False
    env = bg.BackgroundEnv.__new__(bg.BackgroundEnv)
    env.m, env.app, env._kinds, env._bubbles = Mac(), "Calculator", {}, set()
    monkeypatch.setattr(env, "_is_real", lambda w: True)
    assert env.window() == win and 8.0 <= t["now"] < 9.0
