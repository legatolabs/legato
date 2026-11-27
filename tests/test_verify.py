"""Verified actions: one dispatch, proof from the application, and a deadline that late proof cannot pass."""
import io
from types import SimpleNamespace

import pytest

import legato
from legato import Action, Receipt, VisibleResult, checked_step, verified_step
from legato import env as env_module


def _png(size=(10, 10)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, "white").save(buf, "PNG")
    return buf.getvalue()


class FakeEnv:
    """The contract `verified_step` needs from a desktop: observe(), and act(read=, until=, timeout_s=)."""
    def __init__(self):
        self.dispatched = 0
        self.observations = 0
        self.ordinary = 0

    def observe(self):
        self.observations += 1
        return SimpleNamespace(screenshot=_png())

    def act(self, action, *, read=None, until=None, timeout_s=2.0):
        self.dispatched += 1
        if until is None:
            self.ordinary += 1
            return self.observe()
        for _ in range(3):
            value = read()
            if until(value):
                return value
        raise TimeoutError


TARGET = Receipt("r", "accepted", "value")


def step(env, values, **kwargs):
    seq = iter(values)
    return verified_step(env, Action("click", x=1, y=1), "r", lambda: next(seq, None), lambda v: v == "value", **kwargs)


def test_receipt_is_bound_to_the_request_and_read_again_before_confirming():
    env = FakeEnv()
    result = step(env, [None, Receipt("different", "rejected", None), TARGET, TARGET], return_observation=False)
    assert (result.status, env.dispatched, env.observations) == ("confirmed", 1, 0)
    assert result.confirmed and result.sent and result.receipt == TARGET


def test_a_request_that_already_has_its_receipt_is_not_sent_again():
    env = FakeEnv()
    result = step(env, [TARGET])
    assert (result.status, env.dispatched, result.sent) == ("preexisting", 0, False)


def test_a_receipt_revoked_between_reads_does_not_confirm():
    env = FakeEnv()
    result = step(env, [None, TARGET, None, None, None])
    assert (result.status, env.dispatched) == ("unconfirmed", 1)


def test_an_accepted_receipt_with_the_wrong_payload_does_not_confirm():
    env = FakeEnv()
    result = step(env, [None, Receipt("r", "accepted", "wrong")])
    assert (result.status, env.dispatched) == ("unconfirmed", 1)


def test_another_requests_rejection_is_not_this_ones():
    env = FakeEnv()
    result = step(env, [None, Receipt("other", "rejected", None)])
    assert (result.status, env.dispatched) == ("unconfirmed", 1)


def test_this_requests_rejection_is_final():
    env = FakeEnv()
    result = step(env, [None, Receipt("r", "rejected", None)], return_observation=False)
    assert (result.status, env.dispatched, env.observations) == ("rejected", 1, 0)


def test_a_reader_that_fails_counts_as_no_receipt():
    env = FakeEnv()
    def read():
        raise OSError("locked")
    result = verified_step(env, Action("click", x=1, y=1), "r", read, lambda v: True, return_observation=False)
    assert (result.status, env.dispatched) == ("unconfirmed", 1)


def test_a_visible_result_needs_its_observation():
    env = FakeEnv()
    visible = VisibleResult("OK", (0, 0, 10, 10), lambda: (True, "OK"), lambda image: "OK")
    with pytest.raises(ValueError):
        step(env, [], visible=visible, return_observation=False)
    with pytest.raises(ValueError):
        verified_step(env, Action("click", x=1, y=1), "", lambda: None, lambda v: True)
    assert env.dispatched == 0


def test_a_target_that_is_not_exposed_sends_nothing():
    env = FakeEnv()
    visible = VisibleResult("OK", (0, 0, 10, 10), lambda: (False, ""), lambda image: "OK")
    result = step(env, [None, TARGET], visible=visible)
    assert (result.status, env.dispatched, result.sent) == ("unbound", 0, False)


def test_a_visible_result_returns_the_frame_it_checked():
    env = FakeEnv()
    crops = []
    def read_line(image):
        crops.append(image.size)
        return "OK"
    visible = VisibleResult("OK", (2, 3, 8, 7), lambda: (True, "OK"), read_line)
    result = step(env, [None, TARGET, TARGET], visible=visible)
    assert (result.status, env.dispatched, env.observations, crops) == ("confirmed", 1, 1, [(6, 4)])
    assert result.observation.screenshot == _png()


def test_a_window_switched_during_the_capture_does_not_confirm():
    env = FakeEnv()
    states = iter([(True, "old"), (True, "OK"), (False, "OK")])
    visible = VisibleResult("OK", (0, 0, 10, 10), lambda: next(states, (False, "")), lambda image: "OK")
    result = step(env, [None, TARGET, TARGET, TARGET], visible=visible)
    assert (result.status, env.dispatched) == ("unconfirmed", 1)


def test_stale_pixels_do_not_confirm_a_committed_write():
    env = FakeEnv()
    visible = VisibleResult("Filed", (0, 0, 10, 10), lambda: (True, "Filed"), lambda image: "Awaiting save")
    result = step(env, [None, TARGET, TARGET, TARGET, TARGET, TARGET, TARGET], visible=visible)
    assert (result.status, env.dispatched) == ("unconfirmed", 1)


# ---------------------------------------------------------------- checked_step: an exact state
def test_checked_step_returns_when_the_state_is_there_without_a_screenshot():
    env = FakeEnv()
    states = iter([False, False, True])
    result = checked_step(env, Action("key", keys="ctrl+s"), lambda: next(states))
    assert (result.status, env.dispatched, env.observations, env.ordinary) == ("confirmed", 1, 0, 0)


def test_checked_step_with_a_frame_returns_that_frame_and_checks_the_state_again():
    env = FakeEnv()
    states = iter([False, True, True])
    result = checked_step(env, Action("click", x=1, y=1), lambda: next(states), frame=lambda obs: True)
    assert (result.status, env.observations) == ("confirmed", 1) and result.observation is not None
    env, states = FakeEnv(), iter([False, True, False, True, False, False])       # the state went away after the capture
    assert checked_step(env, Action("click", x=1, y=1), lambda: next(states, False),
                        frame=lambda obs: True).status == "unconfirmed"
    assert env.dispatched == 1


def test_checked_step_guard_sends_nothing_when_the_target_is_not_there():
    env = FakeEnv()
    result = checked_step(env, Action("type", text="x"), lambda: True, guard=lambda: False)
    assert (result.status, env.dispatched, result.sent) == ("unbound", 0, False)
    def broken():
        raise RuntimeError("no such window")
    assert checked_step(env, Action("type", text="x"), lambda: True, guard=broken).status == "unbound"


def test_a_check_already_true_proves_nothing_so_the_action_settles_the_ordinary_way():
    env = FakeEnv()
    result = checked_step(env, Action("key", keys="ctrl+a"), lambda: True)
    assert (result.status, env.dispatched, env.ordinary, result.sent) == ("settled", 1, 1, True)
    assert not result.confirmed and result.observation is not None


def test_checked_step_is_unconfirmed_after_one_dispatch_when_the_state_never_appears():
    env = FakeEnv()
    result = checked_step(env, Action("key", keys="ctrl+s"), lambda: False)
    assert (result.status, env.dispatched) == ("unconfirmed", 1)
    with pytest.raises(TypeError):
        checked_step(env, Action("key", keys="ctrl+s"), None)


# ---------------------------------------------------------------- the deadline, on a real Env
class Clock:
    def __init__(self):
        self.now = 100.0

    def monotonic(self):
        return self.now

    def sleep(self, s):
        self.now += s


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(env_module.time, "monotonic", c.monotonic)
    monkeypatch.setattr(env_module.time, "sleep", c.sleep)
    return c


def test_proof_that_arrives_after_the_deadline_does_not_count(clock):
    calls = []
    desk = legato.attach(screenshot=_png, execute=calls.append)
    def slow_read():
        clock.now += 4.0                       # one synchronous read that outlasts the whole timeout
        return "exact evidence"
    click = Action("click", x=2, y=1)
    with pytest.raises(TimeoutError, match="after the 1.05 s deadline"):
        desk.act(click, read=slow_read, until=lambda value: value == "exact evidence", timeout_s=1.05)
    assert calls == [click]                    # sent once, never resent


def test_proof_inside_the_deadline_counts_and_no_read_starts_after_it(clock):
    calls, reads = [], []
    desk = legato.attach(screenshot=_png, execute=calls.append)
    def read():
        reads.append(clock.now)
        clock.now += 0.4
        return len(reads)
    assert desk.act(Action("click", x=2, y=1), read=read, until=lambda n: n == 2, timeout_s=1.05) == 2
    reads.clear()
    with pytest.raises(TimeoutError):
        desk.act(Action("click", x=2, y=1), read=read, until=lambda n: n == 4, timeout_s=1.05)
    assert len(reads) == 3 and len(calls) == 2  # the third read ended past the deadline: no fourth, one dispatch each


def test_a_late_receipt_makes_a_verified_step_unconfirmed_not_confirmed(clock):
    calls = []
    desk = legato.attach(screenshot=_png, execute=calls.append)
    reads = []
    def read_receipt():
        reads.append(1)
        if len(reads) == 1:
            return None                        # before the input: no receipt yet
        clock.now += 4.0
        return TARGET
    result = verified_step(desk, Action("click", x=2, y=1), "r", read_receipt, lambda v: v == "value",
                           return_observation=False, timeout_s=1.05)
    assert (result.status, len(calls)) == ("unconfirmed", 1)


def test_verified_steps_run_on_a_real_callback_desktop():
    calls, state = [], {"saved": False}
    def execute(action):
        calls.append(action)
        state["saved"] = True
    desk = legato.attach(screenshot=_png, execute=execute)
    assert checked_step(desk, Action("key", keys="ctrl+s"), lambda: state["saved"]).status == "confirmed"
    receipts = iter([None, TARGET, TARGET])
    result = verified_step(desk, Action("click", x=2, y=1), "r", lambda: next(receipts, TARGET), lambda v: v == "value")
    assert result.confirmed and result.observation.screenshot == _png() and len(calls) == 2
