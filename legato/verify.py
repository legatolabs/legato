"""Opt-in verified actions: send an input once, then return only when the application has proved the result.

    outcome = legato.verified_step(desktop, legato.Action("click", x=120, y=80), request_id,
                                   read_receipt=orders.read_receipt,
                                   matches_effect=lambda record: record == expected,
                                   return_observation=False)

`desktop.act(action)` waits for the screen to settle. That is the default and stays so. A caller who knows how its
application acknowledges an action can ask for stronger evidence instead, and pick what the next step needs:

  a committed write            `verified_step`: a receipt the application issued for this request
  an exact state               `checked_step`: a file, a field's value, a control's state, read by the caller
  a screenshot that shows it   either one, with `visible=` or `frame=`: the returned frame is checked itself

Both are built on `desktop.act(action, read=..., until=..., timeout_s=...)`, which can be used directly.

The callbacks come from the integration. A receipt must be the application's own: issued for one request, bound to
the target and the payload, and final once it says accepted or rejected. The input is never sent a second time.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any, Callable, Literal

from legato.actions import Action

Status = Literal["confirmed", "rejected", "unconfirmed", "preexisting", "unbound", "settled"]


@dataclass(frozen=True)
class Receipt:
    """What the application says about one request: `disposition` is "accepted", "rejected" or anything else for
    still pending, and `value` is the state it committed (compared by `matches_effect`)."""
    request_id: str
    disposition: str
    value: Any = None


@dataclass(frozen=True)
class VisibleResult:
    """The result as it must appear on the returned screenshot.

    `expected` is the text, and `box` its (left, top, right, bottom) in screenshot pixels. `read_control()` returns
    `(bound, text)`: whether the intended window, document and control are current and exposed, and the control's
    text now. `read_line(image)` reads the text from the crop of the frame being returned (OCR, or a pixel check)."""
    expected: str
    box: tuple[int, int, int, int]
    read_control: Callable[[], tuple[bool, str]]
    read_line: Callable[[Any], str]


@dataclass(frozen=True)
class VerifiedResult:
    """How a verified step ended.

    confirmed    the receipt is this request's, accepted, and matches (and the frame shows it, with `visible`)
    rejected     the application refused this request, finally
    unconfirmed  the input was sent and no proof arrived in time: reconcile before sending another change
    preexisting  this request already had a final receipt: nothing was sent
    unbound      the target was not current and exposed (`visible`, or `guard`): nothing was sent
    settled      `checked_step` only: the check was already true, so it could prove nothing; the action was sent
                 and the screen settled as usual"""
    status: Status
    receipt: Receipt | None = None
    observation: Any = None

    @property
    def confirmed(self) -> bool:
        return self.status == "confirmed"

    @property
    def sent(self) -> bool:
        """Whether the input was dispatched (once)."""
        return self.status in ("confirmed", "rejected", "unconfirmed", "settled")


def verified_step(env: Any, action: Action, request_id: str, read_receipt: Callable[[], Receipt | None],
                  matches_effect: Callable[[Any], bool], *, visible: VisibleResult | None = None,
                  return_observation: bool = True, timeout_s: float = 1.05,
                  wait_for_change: Callable[[], Any] | None = None) -> VerifiedResult:
    """Send `action` once on `env` and wait up to `timeout_s` for the application's receipt for `request_id`.

    `read_receipt()` returns the current `Receipt`, or None (an OSError or ValueError while reading counts as None).
    `matches_effect(receipt.value)` says whether the accepted state is the one this action asked for. With
    `visible`, the control must also read the expected text and the returned screenshot must show it; the frame in
    the result is the one that was checked. `return_observation=False` skips the screenshot when the next step
    needs only the committed state. `wait_for_change()`, if given, blocks until the application may have changed
    (an event, a file notification), instead of the next poll.

    A receipt for another request, a missing one, or a wrong payload never confirms. Nothing is resent: after
    "unconfirmed", find out what happened to `request_id` before issuing another change."""
    if not request_id or not callable(read_receipt) or not callable(matches_effect):
        raise ValueError("A request identity, receipt reader and effect check are required")
    if visible is not None and not return_observation:
        raise ValueError("A visible result requires returning its verified observation")

    def read() -> Receipt | None:
        try:
            return read_receipt()
        except (OSError, ValueError):
            return None

    def belongs(r: Any) -> bool:
        return isinstance(r, Receipt) and r.request_id == request_id

    prior = read()
    if belongs(prior) and prior.disposition in ("accepted", "rejected"):
        return VerifiedResult("preexisting", prior)
    if visible is not None and not visible.read_control()[0]:
        return VerifiedResult("unbound")

    def sample() -> VerifiedResult | None:
        current = read()
        if not belongs(current) and wait_for_change is not None:
            wait_for_change()
            current = read()
        if not belongs(current):
            return None
        if current.disposition == "rejected":
            return VerifiedResult("rejected", current)
        if current.disposition != "accepted" or not matches_effect(current.value):
            return None
        if visible is not None and visible.read_control() != (True, visible.expected):
            return None
        observation = env.observe() if return_observation else None
        if visible is not None:
            from PIL import Image
            with Image.open(io.BytesIO(observation.screenshot)) as image:
                if visible.read_line(image.crop(visible.box)) != visible.expected:
                    return None
            if visible.read_control() != (True, visible.expected):      # still bound after the capture
                return None
        if read() != current:                                           # revoked or replaced meanwhile
            return None
        return VerifiedResult("confirmed", current, observation)

    try:
        return env.act(action, read=sample, until=lambda result: result is not None, timeout_s=timeout_s)
    except TimeoutError:
        return VerifiedResult("unconfirmed", observation=env.observe() if return_observation else None)


def checked_step(env: Any, action: Action, check: Callable[[], bool], *, frame: Callable[[Any], bool] | None = None,
                 guard: Callable[[], bool] | None = None, timeout_s: float = 1.05) -> VerifiedResult:
    """Send `action` once on `env` and return as soon as `check()` says its exact effect is there.

    `check()` reads the state this action must produce: a saved file's contents, a field's value and selection, a
    checkbox, the active view. Make it specific to this action and scoped to the intended window or document. An
    OSError, ValueError or RuntimeError while reading counts as not yet.

    `guard()`, if given, must be true before anything is sent (the right window is in front, the field has the
    focus); otherwise the result is "unbound". `frame(obs)`, if given, must also accept the screenshot that is
    returned, for a next step that reads or clicks by pixels; the state is checked again after the capture.

    A check that is already true before the input proves nothing about it, so the action is then sent the ordinary
    way and the result is "settled". "unconfirmed" means it was sent and the state did not appear in `timeout_s`:
    nothing is resent."""
    if not callable(check) or (frame is not None and not callable(frame)) or (guard is not None and not callable(guard)):
        raise TypeError("check, frame and guard must be callable")

    def ok(fn: Callable[[], Any]) -> bool:
        try:
            return bool(fn())
        except (OSError, ValueError, RuntimeError):
            return False

    if guard is not None and not ok(guard):
        return VerifiedResult("unbound")
    if ok(check):
        return VerifiedResult("settled", observation=env.act(action))

    def sample() -> VerifiedResult | None:
        if not ok(check):
            return None
        if frame is None:
            return VerifiedResult("confirmed")
        observation = env.observe()
        if not frame(observation) or not ok(check):                    # the frame shows it, and it still holds
            return None
        return VerifiedResult("confirmed", observation=observation)

    try:
        return env.act(action, read=sample, until=lambda result: result is not None, timeout_s=timeout_s)
    except TimeoutError:
        return VerifiedResult("unconfirmed")
