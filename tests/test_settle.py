"""legato.settle.Waiter(background=True): the wait leaves out the cells that move on their own."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from legato.settle import LOCAL_RULES, Waiter  # noqa: E402

N = 100          # cells in the grid


class Screen:
    """A grid of N cells on a fake clock. `spin` cells change every frame (a spinner); the action changes `hit`
    cells until `busy_until`."""

    def __init__(self, spin=(0,), hit=(50,)):
        self.t, self.frame, self.spin, self.hit, self.busy_until = 0.0, 0, set(spin), set(hit), -1.0

    def clock(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s

    def act(self, busy_s: float) -> None:
        self.busy_until = self.t + busy_s

    def shot(self) -> tuple:
        self.frame += 1
        busy = self.t < self.busy_until
        return tuple(f"s{self.frame}" if i in self.spin else (f"h{self.frame}" if busy and i in self.hit else "x")
                     for i in range(N))


def waiter(scr: Screen, **kw) -> Waiter:
    return Waiter(scr.shot, rules=LOCAL_RULES, clock=scr.clock, sleep=scr.sleep, **kw)


def test_a_spinner_no_longer_holds_the_wait_to_its_cap():
    scr = Screen()
    w = waiter(scr, background=True)
    w.prime()
    assert w.moving == frozenset({0})
    for _ in range(3):
        scr.act(0.3)                                  # the click's own change lasts 0.3 s
        t = scr.t
        w.settle("click")
        assert scr.t - t < 1.0                        # 0.3 s of change + 0.4 s quiet, not the 2 s cap
    assert w.capped == 0 and w.left_out == 3 and w.moving == frozenset({0})


def test_without_background_the_same_spinner_runs_every_wait_to_the_cap():
    scr = Screen()
    w = waiter(scr)
    w.prime()                                         # a no-op without background
    scr.act(0.3)
    t = scr.t
    w.settle("click")
    assert scr.t - t >= 2.0 and w.capped == 1


def test_a_change_outside_the_moving_cells_is_still_waited_for():
    scr = Screen()
    w = waiter(scr, background=True)
    w.prime()
    scr.act(1.2)                                      # a slow redraw elsewhere
    t = scr.t
    w.settle("click")
    assert scr.t - t >= 1.2 + 0.4 - 0.05


def test_too_much_motion_leaves_nothing_out():
    scr = Screen(spin=range(20))                     # 20% of the screen moving: a video
    w = waiter(scr, background=True)
    w.prime()
    assert w.moving == frozenset()
    scr.act(0.1)
    w.settle("click")
    assert w.capped == 1


def test_cells_that_stop_moving_are_watched_again():
    scr = Screen()
    w = waiter(scr, background=True)
    w.prime()
    scr.spin = set()                                  # the spinner stops
    scr.act(0.1)
    w.settle("click")
    assert w.moving == frozenset()


def test_the_first_wait_learns_when_there_was_no_prime():
    scr = Screen()
    w = waiter(scr, background=True)
    scr.act(0.1)
    w.settle("click")                                 # nothing known yet: exact, so the spinner holds it to the cap
    assert w.capped == 1 and w.moving == frozenset({0})
    scr.act(0.1)
    t = scr.t
    w.settle("click")
    assert scr.t - t < 1.0 and w.capped == 1


def test_a_plain_digest_turns_background_off():
    t = [0.0]
    w = Waiter(lambda: "same", rules=LOCAL_RULES, clock=lambda: t[0],
               sleep=lambda s: t.__setitem__(0, t[0] + s), background=True)
    w.prime()
    assert w.background is False
    w.settle("click")
    assert w.capped == 0 and w.left_out == 0


def test_a_burst_of_drawing_is_waited_for_while_an_animation_beside_it_is_not():
    # a music player on a real Mac: its bottom bar moved in every frame (3.4% of the screen) while a page drew in after a click
    scr = Screen(spin=range(5), hit=range(10, 30))
    w = waiter(scr, background=True, max_moving=0.1)
    w.prime()
    assert w.moving == frozenset(range(5))
    scr.act(0.25)                                     # 20% of the screen redraws for 0.25 s
    t = scr.t
    w.settle("click")
    assert 0.25 + 0.4 - 0.05 <= scr.t - t < 1.0 and w.moving == frozenset(range(5))
    assert w.last["capped"] is False and w.last["left_out"] == 5 and w.last["moved"] == 20


def test_the_wait_records_what_it_saw():
    scr = Screen()
    w = waiter(scr, background=True)
    scr.act(0.1)
    w.settle("click")
    assert w.last["capped"] is True and w.last["left_out"] == 0 and w.last["still_moving"] == 1
