"""Unchanged frames are not re-sent thirty times a second.

The cost of the SDK stream is the write to the card's I2C controller, not the
render: `openrgb` measured about 4 % of one core with the card driven at
30 fps and 0.3 % without it. A zone holding still — `static`, `off`, a slow
breathing at its turning point — was getting the identical frame on every
tick. `FrameGate` sends a frame only when it differs from the last one that
actually went out, with a periodic refresh so a dropped packet cannot leave a
zone on a stale colour for good.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mimarchy.lightd import FrameGate  # noqa: E402

RED = [(255, 0, 0)]
BLUE = [(0, 0, 255)]


def test_first_frame_always_goes() -> None:
    g = FrameGate(refresh=1.0)
    assert g.should_send("gpu", RED, 0.0)


def test_an_identical_frame_is_skipped_until_the_refresh() -> None:
    g = FrameGate(refresh=1.0)
    g.sent("gpu", RED, 0.0)
    assert not g.should_send("gpu", RED, 1 / 30)
    assert not g.should_send("gpu", RED, 0.99)
    assert g.should_send("gpu", RED, 1.0)


def test_a_changed_frame_goes_immediately() -> None:
    g = FrameGate(refresh=1.0)
    g.sent("gpu", RED, 0.0)
    assert g.should_send("gpu", BLUE, 1 / 30)


def test_only_a_successful_send_is_remembered() -> None:
    """A dropped frame must be retried on the next tick, not assumed
    delivered — so the caller records `sent` only after the write returned."""
    g = FrameGate(refresh=1.0)
    assert g.should_send("gpu", RED, 0.0)          # attempted, but not `sent`
    assert g.should_send("gpu", RED, 1 / 30)


def test_zones_are_independent() -> None:
    g = FrameGate(refresh=1.0)
    g.sent("gpu", RED, 0.0)
    assert g.should_send("cpu_fans", RED, 1 / 30)


def test_forget_forces_the_next_frame_out() -> None:
    """Re-preparing a zone for direct rendering switches the controller's
    mode, which can reset its colours; the next frame has to land."""
    g = FrameGate(refresh=1.0)
    g.sent("gpu", RED, 0.0)
    g.forget("gpu")
    assert g.should_send("gpu", RED, 1 / 30)


def test_frames_compare_by_value_not_identity() -> None:
    g = FrameGate(refresh=1.0)
    g.sent("gpu", [(255, 0, 0)], 0.0)
    assert not g.should_send("gpu", [(255, 0, 0)], 0.5)


def test_a_one_led_zone_is_rate_limited_even_when_the_colour_changes() -> None:
    """The card's LED is the expensive write (an I2C transaction per frame),
    and one flat colour stepping ten times a second looks the same as
    thirty. Changed frames inside the interval wait for a later tick."""
    g = FrameGate(refresh=1.0)
    g.sent("gpu", RED, 0.0)
    assert not g.should_send("gpu", BLUE, 1 / 30, min_interval=0.1)
    assert not g.should_send("gpu", BLUE, 0.09, min_interval=0.1)
    assert g.should_send("gpu", BLUE, 0.1, min_interval=0.1)


def test_the_rate_limit_does_not_apply_to_the_strip() -> None:
    g = FrameGate(refresh=1.0)
    g.sent("cpu_fans", RED, 0.0)
    assert g.should_send("cpu_fans", BLUE, 1 / 30, min_interval=0.0)


def test_the_first_frame_ignores_the_interval() -> None:
    g = FrameGate(refresh=1.0)
    assert g.should_send("gpu", RED, 0.0, min_interval=0.1)
