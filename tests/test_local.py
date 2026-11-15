"""legato.local() against a fake screen and mouse: no display needed."""
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


def test_text_is_pasted_line_by_line_on_mac():
    pasted = []
    e = LocalEnv(fixed_wait_s=0, gui=Gui(), grabber=Screen(), paste=pasted.append)
    e.mac = True
    e.act(Action("type", text="Groceries\neggs\n"))
    assert pasted == ["Groceries", "eggs"]                       # exactly as given: no autocorrect, no lost letters
    assert e.gui.calls == [("press", ("enter",)), ("press", ("enter",))]
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
