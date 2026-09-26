"""A device-mode switch that fails is retried, and reported when it never works.

Switching a controller into direct-render mode is the step that decides whether
the frames this daemon renders are the frames the hardware shows, and it used
to fail silently. `apply_plan` was only reached when the state file's mtime
moved or Unhinged's rotation turned, so on a rig without Unhinged — which is to
say almost all of them — a single transient error at startup was permanent: the
zone was never re-prepared, `write_frame` went on succeeding into a board that
was ignoring every frame, `WriteFailureWatch` was fed by those successful
writes, and `ctl status` reported `lighting: running` while the LEDs showed
something nobody had asked for.

Three things are pinned here. The retry happens on a bounded cadence rather
than every frame, because switching modes is slow on this card and retrying at
30 fps would cost more than the lighting. A zone already in the right mode is
never touched again, so the cadence costs nothing in the normal case. And a
zone that cannot be switched for a full window is reported, so the daemon exits
into the panel's supervision — a fresh open — instead of rendering into a
controller it never got into the right mode.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mimarchy.lightd import (TRANSITION_RETRY, WRITE_FAILURE_WINDOW,  # noqa: E402
                             TransitionWatch, apply_transitions)

WINDOW = WRITE_FAILURE_WINDOW
RETRY = TRANSITION_RETRY


class FlakyRGB:
    """A controller whose mode switches fail a set number of times, then work.

    Every attempt is counted per zone, because the distinction the daemon has to
    make is "this zone's switch failed" and not "something raised somewhere".
    """

    def __init__(self, failures: dict[str, int] | None = None):
        self.failures = dict(failures or {})
        self.prepared: list[str] = []
        self.moded: list[str] = []

    def prepare_zone_for_direct_render(self, key: str) -> None:
        if self.failures.get(key, 0) > 0:
            self.failures[key] -= 1
            raise OSError(f"{key} will not enter direct render")
        self.prepared.append(key)

    def set_mode(self, key: str, effect: str, colour=None, speed=None) -> None:
        if self.failures.get(key, 0) > 0:
            self.failures[key] -= 1
            raise OSError(f"{key} will not take a firmware mode")
        self.moded.append(key)


class FakeGate:
    def __init__(self):
        self.forgotten: list[str] = []

    def forget(self, key: str) -> None:
        self.forgotten.append(key)


def rendered(*keys: str) -> dict:
    # `apply_transitions` only needs the keys; the target is the render loop's
    # business, not this function's.
    return {k: (None, 15) for k in keys}


def drive(rgb, zones, applied, transitions, *, times):
    """Run `apply_transitions` over a series of ticks with a fixed plan.

    The plan is deliberately identical on every tick — that is the case the old
    code got wrong, where nothing "changed" so nothing was re-applied.
    """
    gate = FakeGate()
    for t in times:
        apply_transitions(rgb, zones, {}, applied, transitions, gate, t)
    return gate


# --------------------------------------------------------------- the retry


def test_a_zone_with_no_pending_switch_is_due_immediately() -> None:
    w = TransitionWatch()
    assert w.due("cpu_fans", 0.0)


def test_a_failed_switch_is_not_retried_on_the_next_frame() -> None:
    """30 fps times a slow mode switch would spend all its time negotiating
    modes, so a failure has to wait for the cadence."""
    w = TransitionWatch()
    w.failed("cpu_fans", 10.0)
    assert not w.due("cpu_fans", 10.0)
    assert not w.due("cpu_fans", 10.0 + RETRY - 0.01)
    assert w.due("cpu_fans", 10.0 + RETRY)


def test_a_transient_failure_is_retried_until_it_lands() -> None:
    """The bug: two failures at startup used to be permanent."""
    rgb = FlakyRGB({"cpu_fans": 2})
    applied: dict[str, tuple] = {}
    w = TransitionWatch()

    drive(rgb, rendered("cpu_fans"), applied, w,
          times=[0.0, 1.0, 2.0, 3.0, 4.0, 5.0])

    assert rgb.prepared == ["cpu_fans"]              # landed on the third try
    assert applied["cpu_fans"] == ("render",)
    assert w.stuck(100.0) == []


def test_a_failure_leaves_the_zone_a_candidate() -> None:
    """`applied` must not record a mode that was never entered, or the zone
    would drop out of the retry loop entirely."""
    rgb = FlakyRGB({"gpu": 1})
    applied: dict[str, tuple] = {}
    w = TransitionWatch()

    apply_transitions(rgb, rendered("gpu"), {}, applied, w, FakeGate(), 0.0)
    assert "gpu" not in applied
    assert w.due("gpu", 0.0) is False               # pending, not lost
    assert w.due("gpu", RETRY) is True

    apply_transitions(rgb, rendered("gpu"), {}, applied, w, FakeGate(), RETRY)
    assert applied["gpu"] == ("render",)


def test_re_preparing_forgets_what_the_gate_thought_it_sent() -> None:
    """The switch can reset the controller's colours underneath us, so the
    gate's memory stops being true at exactly that moment."""
    rgb = FlakyRGB()
    applied: dict[str, tuple] = {}
    gate = drive(rgb, rendered("cpu_fans"), applied, TransitionWatch(), times=[0.0])
    assert gate.forgotten == ["cpu_fans"]


# ------------------------------------------------------- the normal case


def test_a_zone_already_in_the_right_mode_is_never_touched_again() -> None:
    """The whole cost of retrying every tick has to be zero once the switches
    have landed, or the fix trades a silent bug for a constant one."""
    rgb = FlakyRGB()
    applied: dict[str, tuple] = {}
    w = TransitionWatch()

    times = [i / 30.0 for i in range(300)]           # ten seconds at 30 fps
    drive(rgb, rendered("cpu_fans", "gpu"), applied, w, times=times)

    assert rgb.prepared == ["cpu_fans", "gpu"]       # one switch each, once
    assert w.stuck(10.0) == []


def test_firmware_modes_are_also_retried() -> None:
    """The card's single LED spends most of its life in a firmware mode rather
    than a rendered one, so this path is not the rarer case — it is the common
    one for that zone."""
    rgb = FlakyRGB({"gpu": 2})
    applied: dict[str, tuple] = {}
    w = TransitionWatch()
    firmware = {"gpu": ("rainbow", 50, (255, 0, 0))}

    for t in (0.0, 1.0, 2.0):
        apply_transitions(rgb, {}, firmware, applied, w, FakeGate(), t)

    assert rgb.moded == ["gpu"]
    assert applied["gpu"] == ("firmware", "rainbow", 50, (255, 0, 0))


def test_an_unchanged_firmware_mode_is_not_re_sent() -> None:
    """Re-sending a mode the zone is already in makes the GPU visibly stutter;
    that optimisation must survive the change."""
    rgb = FlakyRGB()
    applied: dict[str, tuple] = {}
    w = TransitionWatch()
    firmware = {"gpu": ("rainbow", 50, (255, 0, 0))}

    for t in (0.0, 1.0, 2.0, 3.0):
        apply_transitions(rgb, {}, firmware, applied, w, FakeGate(), t)

    assert rgb.moded == ["gpu"]


def test_a_new_firmware_spec_is_a_new_transition() -> None:
    rgb = FlakyRGB()
    applied: dict[str, tuple] = {}
    w = TransitionWatch()

    apply_transitions(rgb, {}, {"gpu": ("rainbow", 50, (255, 0, 0))},
                      applied, w, FakeGate(), 0.0)
    apply_transitions(rgb, {}, {"gpu": ("chase", 50, (255, 0, 0))},
                      applied, w, FakeGate(), 1.0)

    assert rgb.moded == ["gpu", "gpu"]


# ------------------------------------------------------------- the window


def test_a_short_failure_never_trips() -> None:
    """Retries that all land inside the window are a controller having a moment,
    not one that is gone — the daemon must ride them out rather than flap."""
    w = TransitionWatch()
    for i in range(int(WINDOW / RETRY)):
        w.failed("cpu_fans", i * RETRY)
        assert w.stuck(i * RETRY) == []


def test_tripping_needs_the_full_window_of_failure() -> None:
    w = TransitionWatch()
    w.failed("cpu_fans", 0.0)
    assert w.stuck(WINDOW - 0.01) == []
    assert w.stuck(WINDOW) == ["cpu_fans"]


def test_the_window_measures_duration_not_attempt_count() -> None:
    """Retrying on a cadence must not shorten the window: a zone that has been
    impossible for the full five seconds is the thing being caught, however many
    times it was asked."""
    w = TransitionWatch()
    for i in range(50):
        w.failed("cpu_fans", i * (WINDOW / 50))
    assert w.stuck(WINDOW) == ["cpu_fans"]


def test_success_resets_the_streak() -> None:
    w = TransitionWatch()
    w.failed("cpu_fans", 0.0)
    w.failed("cpu_fans", WINDOW - 0.01)
    w.ok("cpu_fans")                                # switch finally lands
    assert w.stuck(WINDOW) == []

    w.failed("cpu_fans", WINDOW)                    # a fresh failure, from here
    assert w.stuck(2 * WINDOW - 0.01) == []
    assert w.stuck(2 * WINDOW) == ["cpu_fans"]


def test_a_clean_run_reports_nothing() -> None:
    w = TransitionWatch()
    for i in range(1000):
        assert w.stuck(i / 30.0) == []


def test_zones_are_independent_and_reported_in_a_stable_order() -> None:
    """One zone refusing to switch is a zone-sized problem. The other must keep
    animating rather than taking the daemon down with it, and the note a caller
    writes from this has to read the same way twice."""
    rgb = FlakyRGB({"cpu_fans": 10_000})
    applied: dict[str, tuple] = {}
    w = TransitionWatch()

    times = [i * RETRY for i in range(int(WINDOW / RETRY) + 1)]
    drive(rgb, rendered("cpu_fans", "gpu"), applied, w, times=times)

    assert w.stuck(WINDOW) == ["cpu_fans"]           # only the one that fails
    assert applied["gpu"] == ("render",)             # the healthy one still lit
    assert w.stuck(WINDOW) == ["cpu_fans"]           # and reported the same way
