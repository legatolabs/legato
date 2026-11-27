"""legato.needs: an action followed by a key or text ends its wait when what that next action needs is true."""
import time

import legato
from legato import Action
from legato.needs import COMBO, EDIT, Before, Focus, act_then, app_words, need


class Reader:
    """A scripted computer: the window in front and the focus are whatever the test sets."""
    def __init__(self):
        self.hwnd, self.pid, self.title, self.cls = 10, 1, "Program Manager", "Progman"
        self.f = Focus(ident=(1,), kind=0, pid=1)
        self.target, self.taskbar = "", False

    def front(self):
        return self.hwnd, self.pid, self.title, self.cls

    def focus(self):
        return self.f

    def idle(self, pid):
        return self.is_idle

    is_idle = True

    def before(self, action):
        return Before(hwnd=self.hwnd, pid=self.pid, title=self.title, focus=self.f, target=self.target,
                      target_in_taskbar=self.taskbar)


CLICK, CTRL_L, URL, RETURN = (Action("click", x=443, y=748), Action("key", keys="ctrl+l"),
                              Action("type", text="https://example.com"), Action("key", keys="Return"))


def test_a_taskbar_button_names_its_program():
    assert app_words("Microsoft Edge - 1 running window") == ["microsoft", "edge"]
    assert app_words("Microsoft\u200b Edge pinned") == ["microsoft", "edge"]


def test_after_a_taskbar_click_a_key_needs_that_program_in_front_with_the_keyboard():
    r = Reader()
    r.target, r.taskbar = "Microsoft Edge", True
    rule, check = need(CLICK, CTRL_L, r.before(CLICK), r)
    assert rule == "program in front" and not check()               # nothing has happened yet
    r.hwnd, r.pid, r.title, r.cls = 20, 7, "Untitled - Notepad", "Notepad"
    r.f = Focus(ident=(9,), kind=EDIT, pid=7)
    assert not check()                                              # the wrong window took the front
    r.hwnd, r.pid, r.title, r.cls = 30, 8, "New tab - Personal - Microsoft\u200b Edge", "Chrome_WidgetWin_1"
    assert not check()                                              # Edge is in front, the keyboard is not there yet
    r.f = Focus(ident=(11,), kind=EDIT, pid=8)
    assert check()
    assert need(CLICK, Action("click", x=1, y=1), r.before(CLICK), r) is None       # a click next needs pixels
    r.taskbar = False
    assert need(CLICK, CTRL_L, r.before(CLICK), r) is None          # not a taskbar button: nothing is known


def test_after_a_shortcut_text_needs_a_field_in_the_same_window_empty_or_all_selected():
    r = Reader()
    r.hwnd, r.pid, r.f = 30, 8, Focus(ident=(11,), kind=DOC, pid=8)
    rule, check = need(CTRL_L, URL, r.before(CTRL_L), r)
    assert rule == "field focused" and not check()                  # the focus has not moved
    r.f = Focus(ident=(12,), kind=EDIT, pid=8, value="old.example", all_selected=False)
    assert not check()                                              # a caret in old text: typing would add to it
    r.f = Focus(ident=(12,), kind=EDIT, pid=8, value="old.example", all_selected=True)
    assert check()
    r.f = Focus(ident=(13,), kind=COMBO, pid=8, value="")
    assert check()                                                  # an empty field
    r.hwnd, r.pid = 20, 7
    r.f = Focus(ident=(14,), kind=EDIT, pid=7, value="")
    assert not check()                                              # another window's field
    assert need(Action("key", keys="Tab"), URL, r.before(CTRL_L), r) is None        # a caret key is not a shortcut


def test_after_text_a_key_needs_the_text_in_the_focused_field():
    r = Reader()
    r.pid, r.f = 8, Focus(ident=(12,), kind=EDIT, pid=8, value="")
    rule, check = need(URL, RETURN, r.before(URL), r)
    assert rule == "text entered" and not check()
    r.f = Focus(ident=(12,), kind=EDIT, pid=8, value="https://example.com")
    assert check()
    r.f = Focus(ident=(12,), kind=EDIT, pid=7, value="https://example.com")
    assert not check()                                              # in another program's field


DOC = 50030


def desk(log):
    from legato.rl.fake import tiny_png
    png = tiny_png(1, w=8, h=8)
    return legato.attach(screenshot=lambda: png, execute=log.append)


def test_the_wait_ends_when_the_need_is_met_and_the_input_is_sent_once():
    r, log = Reader(), []
    r.target, r.taskbar = "Microsoft Edge", True
    env = desk(log)

    def execute(a):
        log.append(a)
        r.hwnd, r.pid, r.title, r.cls, r.f = 30, 8, "New tab - Microsoft Edge", "Chrome", Focus(ident=(2,), kind=EDIT, pid=8)
    env._execute = execute
    frames = iter(range(10 ** 6))
    t = time.monotonic()
    obs, rule, how = act_then(env, CLICK, CTRL_L, r, digest=lambda: next(frames))      # a screen that never holds still
    assert (rule, how, log) == ("program in front", "ready", [CLICK]) and time.monotonic() - t < 1.0
    assert obs.screenshot and set(env.last) == {"act_s", "obs_s", "settle_s", "needs"}
    log_ = env.last["needs"]                          # the log: what was there before, and every read
    assert log_["rule"] == "program in front" and log_["how"] == "ready" and log_["before"]["taskbar"] is True
    assert log_["polls"][-1][1] == 1 and all(len(p) == 3 for p in log_["polls"])


def test_a_need_that_never_comes_true_waits_exactly_as_before():
    r, log = Reader(), []
    r.target, r.taskbar = "Microsoft Edge", True
    env = desk(log)
    obs, rule, how = act_then(env, CLICK, CTRL_L, r, digest=lambda: "still")           # a quiet screen
    assert (rule, how, log) == ("program in front", "settled", [CLICK])
    assert 0.15 <= env.last["settle_s"] < 1.5                                           # the click rule's quiet time
    obs, rule, how = act_then(env, CLICK, Action("click", x=5, y=5), r, digest=lambda: "still")
    assert (rule, how) == (None, None) and len(log) == 2                                # not raced: a click comes next
    obs, rule, how = act_then(env, URL, RETURN, r, fixed_s=0.05, digest=lambda: "still")
    assert (rule, how) == ("text entered", "fixed") and len(log) == 3                   # the short pause, kept
    obs, rule, how = act_then(env, CLICK, CTRL_L, None)
    assert (rule, how) == (None, None) and len(log) == 4                                # no reader: the ordinary wait


def test_a_field_already_ready_counts_only_after_it_has_held_with_the_program_idle(monkeypatch):
    import legato.needs as n
    clock = [100.0]
    monkeypatch.setattr(n.time, "monotonic", lambda: clock[0])
    r = Reader()
    r.hwnd, r.pid, r.f = 30, 8, Focus(ident=(12,), kind=EDIT, pid=8, value="")       # a new window: the bar has the focus
    rule, check = need(CTRL_L, URL, r.before(CTRL_L), r)
    assert rule == "field still ready" and not check()               # just sent: it has not held yet
    clock[0] += n.HOLD_S / 2
    assert not check()
    clock[0] += n.HOLD_S
    r.is_idle = False
    assert not check()                                               # the program still has input to handle
    r.is_idle = None
    assert not check()                                               # unknown is not idle
    r.is_idle = True
    assert check()
    # the focus leaves inside the hold (a shortcut handled late): the clock starts again, and it never counts
    rule, check = need(CTRL_L, URL, r.before(CTRL_L), r)
    assert not check()
    clock[0] += n.HOLD_S * 0.8
    r.f = Focus(ident=(40,), kind=50000, pid=8)
    assert not check()
    clock[0] += n.HOLD_S * 2
    assert not check()
    r.f = Focus(ident=(12,), kind=EDIT, pid=8, value="")
    assert not check()                                               # back, but it has to hold again from now
    clock[0] += n.HOLD_S
    assert check()
