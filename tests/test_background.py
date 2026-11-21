"""Background mode (legato/background.py) against a fake Mac: what each action turns into, what is refused, and that
nothing reaches the person's pointer, front app or clipboard. Runs anywhere; tests/test_background_live.py runs the
same on a real Mac."""
import io

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


def test_mcp_background_desktop_needs_an_app_and_reports_the_window(monkeypatch):
    pytest.importorskip("mcp")
    import asyncio

    from legato.mcp_server import build_server
    m = FakeMac()
    srv = build_server(lambda res: (_ for _ in ()).throw(AssertionError("no full-screen desktop")),
                       background=lambda app: BackgroundEnv(app, mac=m, confirm=None))

    async def call(name, **args):
        return await srv.call_tool(name, args)
    with pytest.raises(Exception, match="pass app"):
        asyncio.run(call("desktop_open"))
    out = asyncio.run(call("desktop_open", app="TextEdit"))
    text = out.content[0].text
    assert "(400x300)" in text and "background" in text and out.content[1].type == "image"
    did = text.split("desktop_id: ")[1].split()[0]
    with pytest.raises(Exception, match="can't do that"):
        asyncio.run(call("desktop_act", desktop_id=did, action="drag", x=1, y=1, end_x=9, end_y=9))


def test_local_with_background_returns_the_background_desktop(monkeypatch):
    import legato.background as bg
    made = []
    monkeypatch.setattr(bg, "MacApp", lambda app: made.append(app) or FakeMac())
    monkeypatch.setattr(bg.sys, "platform", "darwin")
    import legato
    e = legato.local(background="Safari", confirm=None)
    assert isinstance(e, BackgroundEnv) and made == ["Safari"]


def test_background_mode_is_macos_only(monkeypatch):
    import legato.background as bg
    monkeypatch.setattr(bg.sys, "platform", "linux")
    with pytest.raises(RuntimeError, match="macOS only"):
        BackgroundEnv("TextEdit")
