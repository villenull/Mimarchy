"""The daemon notices a dead OpenRGB server instead of rendering into it forever.

`write_frame` failures are swallowed per frame — correctly, a dropped frame is
invisible — which meant a server that went away (crash, restart, OOM kill) left
the daemon streaming into a broken socket with the LEDs frozen and the service
green. `WriteFailureWatch` is the line between those two situations, and these
tests pin down exactly where it sits: transients and partial failures never
trip it, only a full window with no successful write at all does, and a frame
that attempted nothing neither feeds nor resets the streak.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mimarchy.lightd import WriteFailureWatch  # noqa: E402

WINDOW = 5.0


def watch() -> WriteFailureWatch:
    return WriteFailureWatch(window=WINDOW)


def test_a_dropped_frame_is_not_fatal() -> None:
    w = watch()
    assert not w.record(0.0, wrote=0, failed=2)
    assert not w.record(0.1, wrote=2, failed=0)


def test_partial_failure_is_a_device_problem_not_a_dead_server() -> None:
    """One zone erroring forever (say, a resized-away GPU zone) must not take
    the daemon down while the other zone is visibly animating."""
    w = watch()
    for i in range(int(WINDOW * 30) * 2):
        assert not w.record(i / 30.0, wrote=1, failed=1)


def test_total_failure_trips_only_after_the_full_window() -> None:
    w = watch()
    assert not w.record(0.0, wrote=0, failed=2)
    assert not w.record(WINDOW - 0.1, wrote=0, failed=2)
    assert w.record(WINDOW, wrote=0, failed=2)


def test_one_success_resets_the_streak() -> None:
    w = watch()
    assert not w.record(0.0, wrote=0, failed=2)
    assert not w.record(WINDOW - 0.1, wrote=1, failed=1)
    assert not w.record(WINDOW, wrote=0, failed=2)          # new streak from here
    assert not w.record(2 * WINDOW - 0.1, wrote=0, failed=2)
    assert w.record(2 * WINDOW, wrote=0, failed=2)


def test_frames_with_nothing_to_write_prove_nothing() -> None:
    """An all-firmware plan attempts no writes. That says nothing about the
    connection, so it must not reset the streak — a dead server is still
    caught the moment rendering resumes — and must not trip it either when
    nothing has ever failed."""
    w = watch()
    for i in range(10):
        assert not w.record(float(i), wrote=0, failed=0)

    w = watch()
    assert not w.record(0.0, wrote=0, failed=2)
    assert not w.record(1.0, wrote=0, failed=0)              # plan went all-firmware
    assert w.record(WINDOW, wrote=0, failed=2)               # resumed, still dead
