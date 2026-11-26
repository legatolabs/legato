"""legato.local() against a fake screen and mouse: no display needed."""
import sys

from legato.actions import Action
from legato.env import png_size
from legato.local import LocalEnv


class Grab:
    def __init__(self, size, rgb):
        self.size, self.rgb = size, bytes(rgb) * size[0] * size[1]
        self.raw = bytes((*rgb, 255)) * size[0] * size[1]


class Screen:
    monitors = [{}, {"left": 0, "top": 0, "width": 200, "height": 100}]

    def grab(self, mon):
        return Grab((200, 100), (10, 20, 30))      # pixels: twice the logical size, as on a Retina Mac


class Gui:
    PAUSE = 0.1

    def __init__(self):
        self.calls = []

    def size(self):
        return (100, 50)

    def __getattr__(self, name):
        return lambda *a, **k: self.calls.append((name, a, k) if k else (name, a))


def env(mac=False):
    e = LocalEnv(fixed_wait_s=0, gui=Gui(), grabber=Screen())
    e.mac = mac
    return e


def test_screenshot_is_scaled_to_logical_size():
    e = env()
    assert png_size(e.observe().screenshot) == (100, 50)
    assert e.gui.PAUSE < 0.1


def test_actions_map_to_pyautogui():
    e = env()
    e.act(Action("click", x=10, y=20))
    e.act(Action("key", keys="ctrl+s"))
    e.act(Action("key", keys="Return"))
    e.act(Action("key", keys="Prior"))
    e.act(Action("scroll", x=5, y=6, direction="down", amount=2))
    e.act(Action("drag", x=1, y=2, x2=3, y2=4))
    e.act(Action("type", text="hi"))
    assert e.gui.calls == [("click", (10, 20)), ("hotkey", ("ctrl", "s")), ("press", ("enter",)),
                           ("press", ("pageup",)), ("moveTo", (5, 6)), ("scroll", (-2,)), ("moveTo", (1, 2)),
                           ("dragTo", (3, 4), {"duration": 0.3, "button": "left"}), ("write", ("hi",), {"interval": 0.005})]


def test_triple_click_is_three_quick_clicks():
    e = env()
    e.act(Action("triple_click", x=10, y=20))
    assert e.gui.calls == [("click", (10, 20), {"clicks": 3, "interval": 0.06})]


def test_ctrl_is_cmd_on_mac():
    e = env(mac=True)
    e.act(Action("key", keys="ctrl+c"))
    e.act(Action("key", keys="super"))
    assert e.gui.calls == [("hotkey", ("command", "c")), ("press", ("command",))]


def test_waits_for_a_still_screen_then_returns():
    e = LocalEnv(gui=Gui(), grabber=Screen())
    obs = e.act(Action("click", x=1, y=1))
    assert png_size(obs.screenshot) == (100, 50)
    assert e.last["settle_s"] < 1.0                # a still screen settles well before the 2 s cap


def test_text_is_pasted_in_one_piece_on_mac():
    pasted = []
    e = LocalEnv(fixed_wait_s=0, gui=Gui(), grabber=Screen(), paste=pasted.append)
    e.mac = True
    e.act(Action("type", text="Groceries\neggs\n"))
    assert pasted == ["Groceries\neggs"]                         # exactly as given: no autocorrect, no lost letters
    assert e.gui.calls == [("press", ("enter",))]                # the newline it ends with is a key press
    e2 = LocalEnv(fixed_wait_s=0, gui=Gui(), grabber=Screen(), paste=None)
    e2.mac = True
    e2.act(Action("type", text="hi"))
    assert e2.gui.calls == [("write", ("hi",), {"interval": 0.005})]


def test_mac_keys_carry_their_own_modifier_flags():
    from legato.local import MacKeys

    class Q:
        kCGEventFlagMaskCommand, kCGEventFlagMaskShift, kCGEventFlagMaskAlternate, kCGEventFlagMaskControl = 1, 2, 4, 8
        kCGHIDEventTap = 0

        def __init__(self):
            self.posted = []

        def CGEventCreateKeyboardEvent(self, src, code, down):
            return {"code": code, "down": down}

        def CGEventSetFlags(self, ev, flags):
            ev["flags"] = flags

        def CGEventPost(self, tap, ev):
            self.posted.append((ev["code"], ev["down"], ev["flags"]))

    q = Q()
    k = MacKeys(q, {"v": 9, "end": 119, "enter": 36, "?": 44})
    assert k.chord(["command", "v"]) and q.posted == [(9, True, 1), (9, False, 1)]
    assert k.chord(["enter"]) and q.posted[-1] == (36, False, 0)             # a plain key clears leftover modifiers
    assert k.chord(["command", "shift", "end"]) and q.posted[-1] == (119, False, 3)
    assert k.chord(["?"]) and q.posted[-1] == (44, False, 2)                 # a shifted character
    assert not k.chord(["command"]) and not k.chord(["command", "nokey"])    # the caller falls back to pyautogui
    e = env(mac=True)
    e.mackeys = k
    e.act(Action("key", keys="ctrl+End"))                                    # ctrl is cmd on a Mac
    assert q.posted[-1] == (119, False, 1) and e.gui.calls == []


def test_mac_double_click_carries_its_click_count():
    from legato.local import MacMouse

    class Q:
        kCGEventLeftMouseDown, kCGEventLeftMouseUp, kCGMouseButtonLeft, kCGMouseEventClickState = 1, 2, 0, 1
        kCGHIDEventTap = 0

        def __init__(self):
            self.posted = []

        def CGEventCreateMouseEvent(self, src, kind, pos, button):
            return {"kind": kind, "pos": pos}

        def CGEventSetIntegerValueField(self, ev, field, n):
            ev["n"] = n

        def CGEventPost(self, tap, ev):
            self.posted.append((ev["kind"], ev["pos"], ev["n"]))

    q = Q()
    e = env(mac=True)
    e.macmouse = MacMouse(q)
    e.act(Action("double_click", x=30, y=40))
    assert q.posted == [(1, (30, 40), 1), (2, (30, 40), 1), (1, (30, 40), 2), (2, (30, 40), 2)]
    assert [c[0] for c in e.gui.calls] == ["moveTo"]
    e.act(Action("triple_click", x=5, y=6))
    assert q.posted[-1] == (2, (5, 6), 3) and len(q.posted) == 10
    e2 = env(mac=True)                                                       # without Quartz: pyautogui's own
    e2.act(Action("double_click", x=1, y=2))
    assert e2.gui.calls[0][0] == "doubleClick"


def test_mac_types_numbers_and_single_letters_as_keys():
    e = env(mac=True)
    pasted = []
    e.paste = pasted.append
    e.act(Action("type", text="3"))
    e.act(Action("type", text="12/12/2026"))
    e.act(Action("type", text="PM"))
    e.act(Action("type", text="Call Maria"))
    e.act(Action("type", text="eggs"))
    assert pasted == ["Call Maria", "eggs"]
    assert [c[1][0] for c in e.gui.calls if c[0] == "write"] == ["3", "12/12/2026", "PM"]


def test_mac_types_a_sum_as_keys():
    e = env(mac=True)                     # Calculator ignores a pasted sum that ends in "="
    pasted = []
    e.paste = pasted.append
    for text in ("1269*5693=", "12+7=", "(2+3)*4=", "3.5*2", "1,234-5"):
        e.act(Action("type", text=text))
    for text in ("a+b=", "2 + 2", "--1", "x=1"):      # letters, spaces and "--" are still pasted
        e.act(Action("type", text=text))
    assert pasted == ["a+b=", "2 + 2", "--1", "x=1"]
    assert [c[1][0] for c in e.gui.calls if c[0] == "write"] == ["1269*5693=", "12+7=", "(2+3)*4=", "3.5*2", "1,234-5"]


def test_click_with_a_held_key():
    from legato.actions import parse
    from legato.env import xdotool_cmds
    a, why = parse({"action": "left_click", "coordinate": [500, 500], "keys": "cmd"}, 1000, 1000)
    assert (a.type, a.x, a.keys) == ("click", 500, "super") and a.describe() == "super+click (500, 500)"
    assert parse({"action": "left_click", "coordinate": [1, 1], "text": "Shift"}, 1000, 1000)[0].keys == "shift"
    assert parse({"action": "left_click", "coordinate": [1, 1], "text": "hello"}, 1000, 1000)[0].keys is None
    assert parse({"action": "double_click", "coordinate": [1, 1], "keys": "shift"}, 1000, 1000)[0].keys is None
    assert xdotool_cmds(a)[0] == [["xdotool", "keydown", "super", "mousemove", "500", "500", "click", "1",
                                   "keyup", "super"]]

    class Q:
        kCGHIDEventTap, kCGEventFlagMaskCommand, kCGEventFlagMaskShift = 0, 16, 32
        kCGEventFlagMaskAlternate, kCGEventFlagMaskControl = 64, 128

        def __init__(self):
            self.posted = []

        def CGEventCreateKeyboardEvent(self, src, code, down):
            return {"code": code, "down": down}

        def CGEventSetFlags(self, ev, flags):
            ev["flags"] = flags

        def CGEventPost(self, tap, ev):
            self.posted.append((ev["code"], ev["down"], ev["flags"]))

    from legato.local import MacKeys
    q = Q()
    e = env(mac=True)
    e.mackeys = MacKeys(q, {})
    e.act(a)                                             # the cmd key itself goes down, a plain click, and it goes up
    assert q.posted == [(55, True, 16), (55, False, 0)] and [c[0] for c in e.gui.calls] == ["click"]
    e2 = env(mac=True)                                   # without Quartz: the key is held around pyautogui's click
    e2.act(a)
    assert [c[0] for c in e2.gui.calls] == ["keyDown", "click", "keyUp"] and e2.gui.calls[0][1] == ("command",)


def test_actions_written_in_the_text_run_before_the_native_call():
    from legato.harness import _calls
    call = {"function": {"name": "computer", "arguments": '{"action": "type", "text": "3:00PM"}'}}
    rec = {"content": 'I will type the date.\n{"action": "type", "text": "12/12/2026"}\n{"action": "key", "keys": "Tab"}',
           "tool_calls": [call]}
    assert [a.get("text") or a.get("keys") for a in _calls(rec)] == ["12/12/2026", "Tab", "3:00PM"]
    same = {"content": '{"action": "type", "text": "3:00PM"}', "tool_calls": [call]}
    assert _calls(same) == [{"action": "type", "text": "3:00PM"}]            # said and called: run once
    assert _calls({"content": "Clicking save.", "tool_calls": [call]}) == [{"action": "type", "text": "3:00PM"}]
    assert _calls({"content": '{"action": "wait"}'}) == [{"action": "wait"}]


def test_the_driver_takes_itself_out_of_the_dock_on_a_mac(monkeypatch):
    import types
    import importlib
    L = importlib.import_module("legato.local")          # the module (legato.local is also the function)
    calls = []
    app = types.SimpleNamespace(setActivationPolicy_=lambda p: calls.append(p) or True)
    fake = types.SimpleNamespace(NSApplication=types.SimpleNamespace(sharedApplication=lambda: app),
                                 NSApplicationActivationPolicyProhibited=2)
    monkeypatch.setitem(sys.modules, "AppKit", fake)
    assert L.no_dock_icon() is True and calls == [2]
    monkeypatch.setitem(sys.modules, "AppKit", None)          # no AppKit: nothing to do, no error
    assert L.no_dock_icon() is False
    hidden = []
    monkeypatch.setattr(L, "no_dock_icon", lambda: hidden.append(1))
    monkeypatch.setattr(L.sys, "platform", "darwin")
    env(mac=True)                                             # a fake gui (tests, the app's own input): left alone
    assert hidden == []


# ------------------------------------------------------------ wait_scope="app": the wait watches the front app only
class FakeQuartz:
    """A window list and per-window pixels: the front app (pid 7) and another app (pid 9) that keeps animating."""
    kCGWindowListOptionOnScreenOnly, kCGNullWindowID, CGRectInfinite = 1, 0, "inf"
    kCGWindowImageBoundsIgnoreFraming, kCGWindowImageNominalResolution = 1, 16

    def __init__(self):
        self.front = [{"kCGWindowNumber": 1, "kCGWindowOwnerPID": 7, "kCGWindowLayer": 0, "kCGWindowAlpha": 1.0,
                       "kCGWindowBounds": {"X": 0, "Y": 0, "Width": 80, "Height": 40}}]
        self.other = [{"kCGWindowNumber": 2, "kCGWindowOwnerPID": 9, "kCGWindowLayer": 0, "kCGWindowAlpha": 1.0,
                       "kCGWindowBounds": {"X": 50, "Y": 10, "Width": 40, "Height": 40}}]
        self.pixels = {1: b"front", 2: b"other-0"}
        self.asked = []

    def CGWindowListCopyWindowInfo(self, opts, rel):
        return self.front + self.other

    def CGWindowListCreateImageFromArray(self, rect, ids, opts):
        self.asked.append(list(ids))
        return b"".join(self.pixels[i] for i in ids)

    def CGImageGetDataProvider(self, img):
        return img

    def CGDataProviderCopyData(self, p):
        return p


def app_env(q):
    e = LocalEnv(fixed_wait_s=0, gui=Gui(), grabber=Screen(), wait_scope="app", quartz=q)
    e.mac = True
    return e


def test_app_scope_ignores_another_app_animating():
    q = FakeQuartz()
    e = app_env(q)
    d = e._digest()
    q.pixels[2] = b"other-1"                       # the background app ticks
    assert e._digest() == d
    assert q.asked[-1] == [1]                      # only the front app's windows are captured


def test_app_scope_sees_the_front_app_change_move_or_lose_the_front():
    q = FakeQuartz()
    e = app_env(q)
    d = e._digest()
    q.pixels[1] = b"front-changed"
    assert e._digest() != d
    d = e._digest()
    q.front[0]["kCGWindowBounds"] = {"X": 5, "Y": 0, "Width": 80, "Height": 40}     # same pixels, window moved
    assert e._digest() != d
    d = e._digest()
    q.front, q.other = q.other, q.front            # another app comes to the front
    assert e._digest() != d


def test_app_scope_falls_back_to_the_screen_when_windows_cant_be_read():
    class Broken(FakeQuartz):
        def CGWindowListCopyWindowInfo(self, opts, rel):
            raise RuntimeError("no window server")
    e = app_env(Broken())
    screen = LocalEnv(fixed_wait_s=0, gui=Gui(), grabber=Screen())._digest()
    assert e._digest() == screen


def test_wait_scope_is_checked():
    import pytest
    with pytest.raises(ValueError):
        LocalEnv(gui=Gui(), grabber=Screen(), wait_scope="window")


def test_a_screen_that_cannot_be_captured_raises_a_desktop_error():
    import pytest
    import legato

    class Gone(Screen):
        def grab(self, mon):
            raise OSError("XGetImage() failed")
    e = LocalEnv(fixed_wait_s=0, gui=Gui(), grabber=Gone())
    with pytest.raises(legato.DesktopError, match="the screen could not be captured: XGetImage"):
        e.observe()


def test_a_chord_written_with_spaces_is_a_chord():
    """A pointer model sent {"action": "hotkey", "text": "ctrl s"}: it ran as ctrl, then s, and typed a stray s."""
    from legato.actions import norm_keys, parse
    assert parse({"action": "hotkey", "text": "ctrl s"}, 100, 100)[0].keys == "ctrl+s"
    assert norm_keys("ctrl shift s") == "ctrl+shift+s" and norm_keys("cmd Enter") == "super+Return"
    assert norm_keys("alt F4") == "alt+F4" and norm_keys("ctrl+s") == "ctrl+s"
    for same in ("space", "Down Down Return", "page down", "Tab Tab", "ctrl", "ctrl+a ctrl+c", "shift ctrl"):
        assert norm_keys(same) == same, same           # presses one after another, as before


def test_the_console_is_only_minimised_on_windows_with_the_real_screen(monkeypatch):
    L, hidden = sys.modules["legato.local"], []
    monkeypatch.setattr(L, "hide_console", lambda: hidden.append(1) or (lambda: None))
    e = LocalEnv(fixed_wait_s=0, gui=Gui(), grabber=Screen())
    e.show_terminal()                                  # always there to call
    assert hidden == [] and L.hide_console.__doc__ is None
    monkeypatch.undo()
    assert L.hide_console()() is None or sys.platform == "win32"      # elsewhere: nothing to do


def test_windows_captures_the_display_that_receives_pointer_coordinates(monkeypatch):
    import io
    from PIL import Image
    import importlib
    local_module = importlib.import_module("legato.local")

    class TwoDisplays(Screen):
        primary = {"left": 0, "top": 0, "width": 200, "height": 100}
        other = {"left": -200, "top": 0, "width": 200, "height": 100}
        monitors = [{}, other, primary]             # MSS 9 metadata has no "is_primary"

        def grab(self, monitor):
            color = (0, 90, 10) if monitor is self.primary else (190, 0, 10)
            return Grab((monitor["width"], monitor["height"]), color)

    monkeypatch.setattr(local_module.sys, "platform", "win32")
    gui = Gui()
    env = LocalEnv(gui=gui, grabber=TwoDisplays(), fixed_wait_s=0)
    obs = env.act(Action("click", x=25, y=10))
    assert gui.calls == [("click", (25, 10))]
    assert (obs.width, obs.height) == (100, 50)
    assert Image.open(io.BytesIO(obs.screenshot)).getpixel((25, 10)) == (0, 90, 10)


def test_windows_scroll_dispatches_wheel_notches_on_the_requested_axis(monkeypatch):
    import ctypes
    import importlib
    from types import SimpleNamespace
    local_module = importlib.import_module("legato.local")
    gui, wheel = Gui(), []
    monkeypatch.setattr(local_module.sys, "platform", "win32")
    monkeypatch.setattr(local_module, "_need", lambda name: gui)
    user32 = SimpleNamespace(mouse_event=lambda *event: wheel.append(event))
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=user32), raising=False)
    env = LocalEnv(grabber=Screen(), hide_terminal=False, confirm=None, fixed_wait_s=0)
    for direction in ("right", "left", "up", "down"):
        env.act(Action("scroll", direction=direction, amount=3, x=20, y=10))
    assert wheel == [(0x1000, 0, 0, 360, 0), (0x1000, 0, 0, -360, 0),
                     (0x0800, 0, 0, 360, 0), (0x0800, 0, 0, -360, 0)]
    assert gui.calls == [(name, args) for _ in range(4)
                         for name, args in (("moveTo", (20, 10)), ("failSafeCheck", ()))]
