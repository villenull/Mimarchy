"""Which zones the daemon renders, and which it hands to the card's firmware.

This is the decision the `[firmware]` tag in the TUI reports on, and it is easy to
get subtly wrong because the interesting cases are combinations: one-LED zone,
spatial effect, linked or not. A fake controller stands in for OpenRGB so the rule
can be exercised without hardware.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from mimarchy import lightstate  # noqa: E402
from mimarchy.lightd import _seed, plan  # noqa: E402

#: The real shapes: the cooler strip is addressable, the GPU exposes one LED for a
#: bar with many physical segments.
ZONES = {"cpu_fans": 60, "gpu": 1}


class FakeRGB:
    """Just the methods `plan` calls.

    `firmware_speed_for_period` mimics the real clamp, because the clamp is the
    interesting part: the card cannot run a rainbow slower than 10.63 s per pass,
    so our two slowest stops both land on its slowest setting.
    """

    SLOWEST_PASS = 10.63

    def __init__(self, modes=("rainbow", "chase"), anchored=True):
        self._modes = modes
        self._anchored = anchored

    def available_modes(self, key: str):
        return self._modes if key == "gpu" else ()

    def firmware_speed_for_period(self, key: str, effect: str, seconds: float):
        if not self._anchored:
            return None            # a mode with no measured timing anchors
        return 250 if seconds >= self.SLOWEST_PASS else max(
            10, round(10 * (seconds / 0.61) ** (1 / 0.888)))


def _state(effect: str, linked: bool) -> lightstate.LightingState:
    st = lightstate.LightingState(linked=linked)
    st.for_target("cpu_fans").effect = effect
    st.for_target("gpu").effect = effect
    return st


@pytest.mark.parametrize("effect", ["static", "spectrum", "chase", "breathing",
                                    "unhinged", "off"])
def test_linked_keeps_colour_carrying_effects_rendered(effect: str) -> None:
    """Anything with a colour of its own stays rendered while linked.

    A firmware effect reports `color_mode=0` and ignores the colour, so handing
    chase to the card put red on the strip and yellow on the bar. Rainbow is the
    documented exception and is covered separately.
    """
    rendered, firmware = plan(FakeRGB(), ZONES, _state(effect, linked=True))
    assert firmware == {}
    assert set(rendered) == {"cpu_fans", "gpu"}


def test_linked_rainbow_still_goes_to_firmware() -> None:
    """The exception, and the reason for it.

    Rainbow has no chosen colour, and the card's own wheel is the same wheel — so
    "the card picks its own colours" describes no visible difference. Rendering it
    on a one-LED zone put a single flat hue on a multi-segment bar, which reads as
    spectrum; that was reported as rainbow being broken.
    """
    rendered, firmware = plan(FakeRGB(), ZONES, _state("rainbow", linked=True))
    assert set(firmware) == {"gpu"}
    assert firmware["gpu"][0] == "rainbow"
    assert set(rendered) == {"cpu_fans"}


@pytest.mark.parametrize("speed", [0.2, 0.4, 0.6, 0.8, 1.0])
def test_firmware_rate_is_matched_by_period_not_by_ladder_position(
        speed: float) -> None:
    """The card is asked for the renderer's period, not for the same ladder rung.

    Mapping rung-to-rung lines the ends up and mismatches everywhere between —
    measured, it left the card at roughly twice our rate at every stop.

    No stop clamps any more. Rainbow's range is 5.0 down to 0.67 s per cycle and
    the card spans 10.63 to 0.61, so the whole ladder fits inside it — which it did
    not when rainbow's slowest stop was 25 s.
    """
    st = _state("rainbow", linked=True)
    for key in ("cpu_fans", "gpu"):
        st.for_target(key).speed = speed
    _rendered, firmware = plan(FakeRGB(), ZONES, st)
    field = firmware["gpu"][1]
    assert 10 < field < 250, f"{field} is against a range end, i.e. clamped"


def test_a_period_the_card_cannot_reach_still_clamps() -> None:
    """The clamp is not dead code — it is what a too-slow request lands on."""
    rgb = FakeRGB()
    assert rgb.firmware_speed_for_period("gpu", "rainbow", 60.0) == 250


def test_a_mode_without_timing_anchors_still_routes() -> None:
    """No anchors means no speed to send — the mode is still used, at the
    firmware's own default rate, rather than being dropped."""
    _rendered, firmware = plan(FakeRGB(anchored=False), ZONES,
                               _state("rainbow", linked=True))
    assert firmware["gpu"][0] == "rainbow"
    assert firmware["gpu"][1] is None


@pytest.mark.parametrize("effect", ["rainbow", "chase"])
def test_unlinked_spatial_effects_go_to_the_card(effect: str) -> None:
    """Unlinked is how you ask for the card's own motion across the bar."""
    rendered, firmware = plan(FakeRGB(), ZONES, _state(effect, linked=False))
    assert set(firmware) == {"gpu"}
    assert set(rendered) == {"cpu_fans"}
    assert firmware["gpu"][0] == effect


@pytest.mark.parametrize("effect", ["static", "spectrum", "breathing",
                                    "unhinged", "off"])
def test_unlinked_non_spatial_effects_stay_rendered(effect: str) -> None:
    """These put one colour on the whole device, which is exactly where drift
    between two controllers is visible — so they keep the shared clock."""
    rendered, firmware = plan(FakeRGB(), ZONES, _state(effect, linked=False))
    assert firmware == {}
    assert set(rendered) == {"cpu_fans", "gpu"}


def test_firmware_needs_the_card_to_offer_the_mode() -> None:
    rendered, firmware = plan(FakeRGB(modes=()), ZONES,
                              _state("rainbow", linked=False))
    assert firmware == {}
    assert set(rendered) == {"cpu_fans", "gpu"}


def test_multi_led_zones_are_never_handed_over() -> None:
    """The routing exists because a one-LED zone cannot express a spatial
    pattern. A 60-slot strip can, so it keeps the shared clock."""
    _rendered, firmware = plan(FakeRGB(), {"cpu_fans": 60},
                               _state("rainbow", linked=False))
    assert firmware == {}


def test_linked_zones_read_one_shared_entry() -> None:
    """Both devices must come from the same state, or "linked" means nothing."""
    st = lightstate.LightingState(linked=True)
    st.for_target("cpu_fans").effect = "breathing"
    st.for_target("gpu").effect = "spectrum"      # stale, must be ignored
    rendered, _firmware = plan(FakeRGB(), ZONES, st)
    assert {k: v[0].effect for k, v in rendered.items()} == {
        "cpu_fans": "breathing", "gpu": "breathing"}


def test_a_third_zone_is_planned_independently_of_the_linked_pair() -> None:
    """`cpu_fans` and `gpu` are two config keys, not the design.

    Linking is *defined* as that pair, so a third zone must keep its own effect
    while they share one — otherwise "add another strip" would silently mean
    "make everything match", and the extra `[rgb.zones.*]` block the README
    advertises would be a lie.
    """
    st = _state("breathing", linked=True)
    st.for_target("case").effect = "spectrum"

    rendered, firmware = plan(FakeRGB(), {**ZONES, "case": 30}, st)

    assert firmware == {}
    assert {k: v[0].effect for k, v in rendered.items()} == {
        "cpu_fans": "breathing", "gpu": "breathing", "case": "spectrum"}


@pytest.mark.xfail(reason="`plan` reads state.linked as a global rather than as "
                          "a property of the cpu_fans/gpu pair, so the link "
                          "blocks a third zone's firmware hand-off too",
                   strict=True)
def test_a_third_one_led_zone_reaches_firmware_on_its_own_terms() -> None:
    """The link's colour objection is about the linked pair. A zone outside it
    has no shared colour to mismatch, so it routes on its own effect.

    Currently it does not: `colour_blocks_firmware` is `state.linked and ...`,
    and `state.linked` is one flag for the whole file. So while CPU and GPU are
    linked — the default — a third one-LED device running chase is rendered
    instead of handed over, i.e. shows a flat colour where the hardware could
    show a travelling head. `_source_target` already knows that linking means
    *that pair*; this line needs to ask it the same question.
    """
    st = _state("static", linked=True)
    st.for_target("case").effect = "chase"

    rgb = FakeRGB()
    rgb.available_modes = lambda key: ("rainbow", "chase")
    _rendered, firmware = plan(rgb, {**ZONES, "case": 1}, st)

    assert firmware["case"][0] == "chase"
    assert "gpu" not in firmware


def test_zone_seeds_are_stable_and_distinct() -> None:
    """Unhinged decorrelates zones from these, so they must differ — and must not
    change between daemon restarts, which rules out `hash()`."""
    assert _seed("cpu_fans") != _seed("gpu")
    assert _seed("gpu") == _seed("gpu")


# ---- Unhinged's firmware rotation ---------------------------------------


def _phases_over(seconds: int, linked: bool = True) -> list:
    """The GPU's phase, sampled once a second."""
    st = _state("unhinged", linked=linked)
    out = []
    for i in range(seconds):
        rendered, firmware = plan(FakeRGB(), ZONES, st, float(i))
        out.append(firmware["gpu"][0] if "gpu" in firmware else None)
        # The strip is never handed over; only the one-LED zone rotates.
        assert "cpu_fans" in rendered
    return out


def test_unhinged_rotates_the_gpu_through_both_firmware_effects() -> None:
    """The point of the rotation: the bar gets a hue wave and a travelling dot,
    neither of which a single controllable LED can show."""
    seen = set(_phases_over(200))
    assert seen == {None, "rainbow", "chase"}


def test_unhinged_spends_most_of_its_time_rendered() -> None:
    """Rendered colour churn is the default state, not an interlude.

    Also the reason handovers are sparse: each one blocks on a mode bounce, which
    stalls the strip while it happens.
    """
    phases = _phases_over(300)
    assert phases.count(None) / len(phases) > 0.6


def test_unhinged_handovers_are_rare() -> None:
    """At most a handful of transitions a minute, or the strip visibly hitches."""
    phases = _phases_over(300)
    changes = sum(1 for a, b in zip(phases, phases[1:]) if a != b)
    per_minute = changes / 5.0
    assert per_minute <= 4, f"{per_minute:.1f} handovers/min is too many"


def test_unhinged_chase_phase_is_not_blocked_by_the_link() -> None:
    """Chase normally stays rendered while linked because the card ignores its
    colour. Unhinged has no chosen colour, so that objection does not apply."""
    st = _state("unhinged", linked=True)
    hit = [t for t in range(200)
           if plan(FakeRGB(), ZONES, st, float(t))[1].get("gpu", (None,))[0]
           == "chase"]
    assert hit, "the chase phase never ran while linked"


def test_unhinged_renders_when_the_card_offers_no_firmware_modes() -> None:
    rendered, firmware = plan(FakeRGB(modes=()), ZONES,
                              _state("unhinged", linked=True), 0.0)
    assert firmware == {}
    assert set(rendered) == {"cpu_fans", "gpu"}
