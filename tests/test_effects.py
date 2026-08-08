"""Tests for the effect renderers, and in particular Unhinged's safety limits.

Unhinged is specified with hard bounds — a brightness floor, no black frames, no
strobing, no cycling into `off` — and those bounds are the reason it is safe to
point at a pair of bright LED strips a metre from someone's face. They are easy
to erode by accident later, because every one of them looks like a tuning
constant. These tests are what makes eroding one show up as a failure rather than
as a slightly different animation.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from mimarchy.effects import (COLOUR_EFFECTS, EFFECTS, SPATIAL_EFFECTS,  # noqa: E402
                                SPEED_LEVELS, _UNHINGED_FLOOR, _UNHINGED_POOL,
                                _rand, render, speed_gain)

FPS = 30
#: Long enough to cover many colour segments and several effect segments at the
#: slowest per-zone period the generator can draw.
DURATION = 120.0

#: Both zone shapes this hardware actually has: the 60-slot CPU strip and the
#: GPU's single controllable LED. The 1-LED case is the one that has historically
#: broken effects, since every spatial pattern collapses there.
COUNTS = (60, 1)

SEEDS = (0, 1, 1234, 0xDEADBEEF)


def _run(seed: int, count: int, speed: float = 0.6):
    return [render("unhinged", i / FPS, count, speed=speed, seed=seed)
            for i in range(int(DURATION * FPS))]


@pytest.mark.parametrize("count", COUNTS)
@pytest.mark.parametrize("seed", SEEDS)
def test_unhinged_never_goes_black(seed: int, count: int) -> None:
    """No pixel is ever fully off. `off` must stay a user action, not a phase."""
    for frame in _run(seed, count):
        assert all(max(px) > 0 for px in frame)


@pytest.mark.parametrize("count", COUNTS)
@pytest.mark.parametrize("seed", SEEDS)
def test_unhinged_respects_the_brightness_floor(seed: int, count: int) -> None:
    """Every pixel clears the floor, including mid-crossfade.

    The floor is a fraction of the segment colour renormalised to full value, so
    the bound to check is against full scale — which is what caught the blend
    passing through the middle of the RGB cube and halving the floor.
    """
    limit = 255 * _UNHINGED_FLOOR
    worst = min(max(px) for frame in _run(seed, count) for px in frame)
    assert worst >= limit * 0.9, f"dipped to {worst}, floor is {limit:.0f}"


@pytest.mark.parametrize("count", COUNTS)
def test_unhinged_does_not_strobe(count: int) -> None:
    """No sustained fast flicker.

    Judged as a rate, not as a single jump: an isolated sharp edge is a hard cut
    (chase snapping its head back to the start does this once per cycle, by
    design), whereas a strobe is that edge repeating several times a second. The
    bound is on how often large swings happen, which is the property that matters
    for anyone photosensitive in the room.
    """
    frames = _run(0xC0FFEE, count, speed=max(SPEED_LEVELS))
    means = [sum(max(px) for px in f) / len(f) for f in frames]
    big = sum(1 for a, b in zip(means, means[1:]) if abs(b - a) > 0.4 * 255)
    rate = big / DURATION
    assert rate < 3.0, f"{rate:.1f} large swings/s reads as a strobe"


@pytest.mark.parametrize("count,limit", [(60, 3.0), (15, 3.0), (1, 6.0)])
def test_unhinged_flicker_rate_is_bounded(count: int, limit: float) -> None:
    """How *often* brightness peaks, which is the number that matters for anyone
    photosensitive — and the one `test_unhinged_does_not_strobe` does not measure.

    That test counts sharp per-frame edges, so a smooth oscillation slips past it
    however fast it runs: a 3.75 Hz swing between 18% and 100% moves only ~20% per
    frame at 30 fps and scores zero. This counts peaks instead.

    The limits differ by zone shape on purpose. A multi-LED strip averages its
    spatial effects and sits near 1.8 peaks/s at the top stop. A one-LED zone shows
    every modulation directly and measures ~4.8 — above the ~3 Hz that guidance
    treats as the start of the risk band, and a deliberate consequence of the top
    stop being raised on request. The bound is here so that number stays visible
    and cannot drift further without a test failing.
    """
    fps, dur = 60, 40.0
    levels = [
        sum(max(px) for px in render("unhinged", i / fps, count, speed=1.0,
                                     seed=0xC0FFEE)) / count / 255
        for i in range(int(dur * fps))
    ]
    peaks = sum(1 for i in range(1, len(levels) - 1)
                if levels[i] > levels[i - 1] and levels[i] >= levels[i + 1]
                and levels[i] > 0.35)
    rate = peaks / dur
    assert rate < limit, f"{rate:.2f} brightness peaks/s exceeds {limit}"


def test_unhinged_pool_excludes_off_and_itself() -> None:
    assert "off" not in _UNHINGED_POOL
    assert "unhinged" not in _UNHINGED_POOL
    assert set(_UNHINGED_POOL) <= set(EFFECTS)


@pytest.mark.parametrize("count", COUNTS)
def test_unhinged_zones_are_decorrelated(count: int) -> None:
    """Two zones on the same settings must not move together.

    This is what makes a pair of them read as chaotic rather than as one unified
    pulse, and it is the reason the renderer takes a seed at all.
    """
    a = _run(1234, count)
    b = _run(99, count)
    identical = sum(1 for x, y in zip(a, b) if x == y)
    assert identical == 0


def test_unhinged_is_reproducible() -> None:
    """Same seed and time, same frame — the renderer stays pure.

    `hash()` would pass this within one process and fail across two, since it is
    salted per interpreter; `_rand` is built on crc32 to avoid exactly that.
    """
    assert _rand(1, 2, 3) == _rand(1, 2, 3)
    one = render("unhinged", 12.5, 60, speed=0.6, seed=7)
    two = render("unhinged", 12.5, 60, speed=0.6, seed=7)
    assert one == two


@pytest.mark.parametrize("count", COUNTS)
def test_unhinged_actually_changes(count: int) -> None:
    """The point of it. A floor plus a crossfade could smother it into a wash."""
    frames = _run(1234, count)
    distinct = len({tuple(f) for f in frames})
    assert distinct > len(frames) * 0.5


# ---- the wider contract the TUI and daemon rely on -----------------------


def test_effect_order_is_the_key_order() -> None:
    """The TUI numbers these 1..6 and binds `off` to 0."""
    assert EFFECTS[:6] == ("static", "rainbow", "spectrum", "chase",
                           "breathing", "unhinged")
    assert EFFECTS[-1] == "off"


def test_unhinged_is_neither_a_colour_nor_a_spatial_effect() -> None:
    """It picks its own colours, so the palette does not apply to it; and it is
    not a firmware-routable pattern, so `lightd.plan` renders it in software."""
    assert "unhinged" not in COLOUR_EFFECTS
    assert "unhinged" not in SPATIAL_EFFECTS


def test_speed_ladder_is_five_even_stops_to_one() -> None:
    assert SPEED_LEVELS == (0.2, 0.4, 0.6, 0.8, 1.0)
    # The gain behind a label is monotonic along the ladder for every effect.
    for effect in (None, "rainbow", "chase", "unhinged"):
        gains = [speed_gain(s, effect) for s in SPEED_LEVELS]
        assert gains == sorted(gains)


def test_the_gain_behind_a_label_depends_on_the_effect() -> None:
    """A label is a dial position; the rate behind it is per-effect.

    Pinned as ratios rather than absolutes so the intent survives retuning: the
    spatial pair start where the general ladder's middle is, and rainbow's top is
    three times its own previous top.
    """
    from mimarchy.effects import cycle_seconds, speed_gain
    # Rainbow's slowest is the general ladder's middle stop.
    assert cycle_seconds(0.2, "rainbow") == pytest.approx(
        cycle_seconds(0.6, "spectrum"), rel=0.01)
    # Chase never reaches the general ladder's extremes.
    assert speed_gain(0.2, "chase") > speed_gain(0.2, "spectrum")
    assert speed_gain(1.0, "chase") < speed_gain(1.0, "spectrum")
    # Rainbow and unhinged tops were each raised 3x.
    assert cycle_seconds(1.0, "rainbow") == pytest.approx(2.0 / 3, rel=0.01)
    assert speed_gain(1.0, "unhinged") == pytest.approx(15.0, rel=0.01)


@pytest.mark.parametrize("effect", EFFECTS)
@pytest.mark.parametrize("count", COUNTS)
def test_every_effect_renders_one_colour_per_led(effect: str, count: int) -> None:
    frame = render(effect, 3.25, count, colour=(10, 200, 30), speed=0.6, seed=5)
    assert len(frame) == count
    assert all(len(px) == 3 and all(0 <= c <= 255 for c in px) for px in frame)
