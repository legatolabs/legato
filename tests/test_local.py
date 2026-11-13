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
        return lambda *a, **k: self.calls.append((name, a))


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
                           ("dragTo", (3, 4)), ("write", ("hi",))]


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
