"""Software-rendered lighting effects.

The controllers' own hardware effects can't be made to agree. The board exposes
no speed parameter at all (HAS_SPEED is unset on every one of its modes) while
the GPU's runs 10-250, and the two animate from independent free-running
clocks — so in spectrum or breathing they drift apart and show different
colours at the same instant, with no way to correct it.

Rendering here instead fixes both: one clock drives every device, so they are
in phase by construction, and speed is whatever we choose rather than whatever
the firmware offers.

Every function is pure — `(t, index, count, ...) -> (r, g, b)` — so the same
frame can be rendered for a 60-LED strip and a 1-LED zone and stay consistent.
"""

from __future__ import annotations

import colorsys
import math
import struct
import zlib

#: Effects that use the chosen colour rather than sweeping hue themselves.
COLOUR_EFFECTS = frozenset({"static", "breathing", "chase"})

#: Keep in sync with the TUI legend. Order is load-bearing: the TUI numbers these
#: 1..6 in sequence and `off` is bound to `0`, so inserting one in the middle
#: renumbers every key after it.
EFFECTS = ("static", "rainbow", "spectrum", "chase", "breathing", "unhinged",
           "off")

#: Effects that are a pattern in *space* as well as in time. These are the ones
#: a zone with a single controllable LED cannot express at all: a hue wave and a
#: travelling head both collapse to "one colour, changing", which is spectrum
#: and static respectively. Where the device has its own firmware version, the
#: daemon hands these to it rather than rendering them — with one exception that
#: turns on whether the effect carries a colour. `chase` does, and the card ignores
#: it, so while linked chase stays rendered rather than showing red on the strip
#: and yellow on the bar. `rainbow` does not: the card's own wheel is the same
#: wheel, so it goes to firmware either way. See `lightd.plan`.
SPATIAL_EFFECTS = frozenset({"rainbow", "chase"})

#: The speed *labels* — what the TUI shows and what lightstate stores. Five even
#: stops, which is what makes the ladder easy to read.
SPEED_LEVELS = (0.2, 0.4, 0.6, 0.8, 1.0)

#: What each label actually multiplies the animation rate by.
#:
#: The labels are not the multiplier, and that separation is the point. The top of
#: the ladder needed to be five times faster while the bottom stayed exactly where
#: it was, and no single coefficient can do both — so the label stays a position on
#: a dial and this is the rate behind it.
#:
#: Geometric, spanning 0.2 to 5.0. Geometric because speed is judged
#: multiplicatively: an even spread would put three of the five stops within a
#: factor of 1.5 of each other at the top and waste them. The bottom stop is 0.2,
#: identical to what it has always been, and the middle stop is 1.0 — the old
#: maximum — so every previously reachable speed is still on the dial.
_GAIN_SLOWEST, _GAIN_FASTEST = 0.2, 5.0

#: Effects whose usable range is narrower than the default, because the same
#: multiplier does not read the same way on every effect. Rainbow and chase are the
#: spatial pair: a hue wave crawling round a 15-LED strip over 25 s does not read as
#: motion at all, and a travelling head at 2.5 passes a second reads as a flicker.
#: Their whole ladder therefore lives inside what used to be its middle, from the
#: old 0.6 stop up to half the old top.
_GAIN_RANGES = {
    "rainbow": (1.0, 7.5),
    "chase": (1.0, 2.5),
    "unhinged": (0.2, 15.0),
}


def _gain_ladder(effect: str | None) -> tuple[float, ...]:
    lo, hi = _GAIN_RANGES.get(effect or "", (_GAIN_SLOWEST, _GAIN_FASTEST))
    last = len(SPEED_LEVELS) - 1
    return tuple(lo * (hi / lo) ** (i / last) for i in range(last + 1))


def nearest_speed(speed: float) -> float:
    """Snap a stored speed onto the ladder.

    State written under the old six-stop ladder holds values above the current
    maximum, and an unsnapped one lights no stop at all. Public because both the
    TUI and `mimarchy-ctl` step the ladder and must agree on where a given
    stored value sits — two different roundings would make the bar and the TUI
    disagree about the current speed.
    """
    return min(SPEED_LEVELS, key=lambda s: abs(s - speed))


def _stop_index(speed: float) -> int:
    """Which stop a stored speed sits on. State can hold anything."""
    return SPEED_LEVELS.index(nearest_speed(speed))


def speed_gain(speed: float, effect: str | None = None) -> float:
    """The rate multiplier behind a label, for a given effect.

    Per-effect because a label is a position on a dial rather than a rate, and
    what counts as "slowest useful" differs by effect — see `_GAIN_RANGES`.
    """
    return _gain_ladder(effect)[_stop_index(speed)]


#: Hue cycles per second per unit of gain — the coefficient rainbow and spectrum
#: sweep at. Named because the daemon needs it to work out what period a firmware
#: mode has to run at to match.
HUE_CYCLES_PER_GAIN = 0.2

#: Strip-lengths per second per unit of gain, i.e. how fast a chase head travels.
CHASE_PASSES_PER_GAIN = 0.5


#: While Unhinged runs, the GPU hands its bar to the card's own firmware every so
#: often, which buys two things a single controllable LED cannot do: a hue wave
#: across the bar's internal segments, and a lone dot travelling along it.
#:
#: `(mode, seconds)`. `None` is the software-rendered colour churn and is the
#: default state, so it gets the long slots. The firmware slots are deliberately
#: sparse. Every handover costs a blocking bounce through a direct mode — 0.4 s of
#: settle entering, and leaving needs a send, a settle and a second send, because a
#: single exit request is dropped 5 times out of 5 on this card — and that stalls
#: the strip's rendering while it happens. One handover roughly every 40 s keeps
#: the pause rare enough to read as nothing at all rather than as a stutter.
_UNHINGED_GPU_SCHEDULE = (
    (None, 34.0),
    ("rainbow", 12.0),
    (None, 26.0),
    ("chase", 10.0),
)


def unhinged_firmware_phase(t: float, seed: int = 0) -> str | None:
    """Which firmware mode Unhinged wants on a one-LED zone now, or None to render.

    Seeded offset so the rotation does not start at the same point on every daemon
    restart, and so two such zones would not move together.
    """
    total = sum(seconds for _mode, seconds in _UNHINGED_GPU_SCHEDULE)
    pos = (t + _rand(seed, 9) * total) % total
    for mode, seconds in _UNHINGED_GPU_SCHEDULE:
        if pos < seconds:
            return mode
        pos -= seconds
    return None


def firmware_period(effect: str, speed: float) -> float:
    """Seconds per pass of `effect` as the software renderer runs it.

    What a firmware mode has to be matched against, and it is not the same
    quantity for every effect: rainbow sweeps hue, chase travels a head.
    """
    gain = speed_gain(speed, effect)
    if effect == "chase":
        return 1.0 / (gain * CHASE_PASSES_PER_GAIN)
    return cycle_seconds(speed, effect)


def cycle_seconds(speed: float, effect: str | None = None) -> float:
    """Seconds for a full hue cycle at a given label, as rendered in software.

    This is the number a firmware mode has to be matched against: the card cannot
    be phase-locked, but it can be asked to complete a pass in the same time. Takes
    the effect because the gain ladder depends on it.
    """
    return 1.0 / (speed_gain(speed, effect) * HUE_CYCLES_PER_GAIN)

#: Rec. 709 luminance weights — how bright a colour *looks*, not how big its
#: channel values are. At full saturation this spans 0.07 (blue) to 0.93
#: (yellow), a 13x swing that a single LED shows as a pulse. See `_even`.
_LUMA = (0.2126, 0.7152, 0.0722)

#: How hard `_even` pulls towards a constant brightness (0 = off, 1 = flat) and
#: what level it pulls towards. 0.75 takes the hue sweep's brightness ratio from
#: ~13x down to ~1.9x, which stops reading as breathing while leaving the
#: colours obviously saturated; going to 1.0 would wash blues out to pale.
_EVEN_STRENGTH, _EVEN_TARGET = 0.75, 0.5


def _hsv(h: float, s: float, v: float) -> tuple[int, int, int]:
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return int(r * 255), int(g * 255), int(b * 255)


def _even(colour: tuple[int, int, int]) -> tuple[int, int, int]:
    """Pull a colour's *perceived* brightness towards a constant, keeping hue.

    Only needed where an effect's spatial dimension has collapsed. A 60-LED
    strip running rainbow shows every hue at once, so the dark blues and bright
    yellows sum to a steady output; a single-LED zone shows them one after
    another, and that is a brightness pulse — the "GPU is breathing" report.

    Two ways to move luminance, picked by direction: dimming just scales the
    channels (saturation untouched), while brightening a hue already at full
    value can only be done by tinting towards white. Luminance is affine in RGB,
    so the tint fraction is exact rather than iterated.
    """
    c = [x / 255 for x in colour]
    lum = sum(x * k for x, k in zip(c, _LUMA))
    if lum <= 0.0:
        return colour
    # Geometric blend: at strength 1 every hue lands on _EVEN_TARGET exactly.
    want = _EVEN_TARGET ** _EVEN_STRENGTH * lum ** (1 - _EVEN_STRENGTH)
    if want <= lum:
        c = [x * want / lum for x in c]
    else:
        w = (want - lum) / (1 - lum)
        c = [x + w * (1 - x) for x in c]
    return tuple(round(min(1.0, max(0.0, x)) * 255) for x in c)  # type: ignore[return-value]


#: What Unhinged is allowed to cycle through. `off` and `unhinged` are both
#: absent, and for different reasons: recursing into itself would be a bug, while
#: cycling into `off` would make the effect blank the strip on its own. Turning
#: lights off stays a deliberate act by the user (`0`), never a phase some
#: animation wanders into.
_UNHINGED_POOL = ("static", "rainbow", "spectrum", "chase", "breathing")

#: Floor on every channel, as a fraction of the segment's own colour. Nothing
#: Unhinged renders is ever darker than this, so there are no black frames and no
#: hard on/off edges — the effect stays a colour wash rather than becoming a
#: strobe. Set inside the 15-20% band the design calls for.
_UNHINGED_FLOOR = 0.18

#: Crossfade between consecutive segments. Every transition — colour or effect —
#: is smoothed by this, which is the other half of not strobing.
_UNHINGED_FADE = 0.2

#: Seconds per colour segment, and seconds per effect segment, each drawn once
#: per zone from its seed. Two zones therefore run on different periods and never
#: line up, which is what makes a pair of them read as chaos rather than as one
#: synchronised pulse.
#:
#: Both are deliberately independent of the user's speed setting. Speed scales the
#: animation *within* a segment; letting it scale the switching rate as well would
#: put a strobe one keypress away, which is precisely what the floor and the
#: crossfade exist to prevent.
_UNHINGED_COLOUR_PERIOD = (0.4, 0.8)
_UNHINGED_EFFECT_PERIOD = (2.5, 5.0)


def _rand(*parts: int) -> float:
    """Deterministic [0, 1) from a tuple of integers.

    Not `random`: this has to be callable from a pure renderer, so there is no
    generator to hold state in, and the same (seed, segment) must give the same
    answer on every frame it is asked about — including the two frames either
    side of a crossfade, which are computed independently.

    `hash()` is unusable here for a subtler reason: it is salted per process, so a
    zone's sequence would differ between daemon restarts and, worse, between the
    daemon and any test that reproduces it.
    """
    return zlib.crc32(struct.pack(f"<{len(parts)}q", *parts)) / 2 ** 32


def _pick(span: tuple[float, float], r: float) -> float:
    return span[0] + (span[1] - span[0]) * r


def _blend(a: list[tuple[int, int, int]], b: list[tuple[int, int, int]],
           mix: float) -> list[tuple[int, int, int]]:
    return [tuple(int(x + (y - x) * mix) for x, y in zip(pa, pb))  # type: ignore[misc]
            for pa, pb in zip(a, b)]


def _unhinged(t: float, count: int, gain: float,
              seed: int) -> list[tuple[int, int, int]]:
    """Randomised colour and effect churn, bounded so it cannot strobe.

    Built on a single timeline of short *segments* rather than two independent
    clocks for colour and effect. An effect change is simply a segment boundary
    that also happens to change the effect — arranged by making the effect period
    a whole number of colour segments — so every transition in the effect is the
    same one crossfade, and at most two sub-frames are ever rendered per frame.
    Two overlapping fades would otherwise need four.

    Each segment carries a random hue, a random phase offset, and (every `n`
    segments) a random sub-effect. The phase offset matters more than it looks:
    without it a re-picked effect would resume mid-stride from wherever the
    shared clock happens to be, and the sequence would read as one long
    animation being interrupted rather than as a new one starting.
    """
    period = _pick(_UNHINGED_COLOUR_PERIOD, _rand(seed, 1))
    # Quantise the effect period to whole colour segments so the two kinds of
    # boundary coincide. `max(1, ...)` guards the degenerate case where a long
    # colour period would otherwise round the effect period down to zero.
    per_effect = max(1, round(_pick(_UNHINGED_EFFECT_PERIOD, _rand(seed, 2))
                              / period))

    k = int(t // period)
    into = t - k * period
    # Ramps 0 -> 1 across the final `_UNHINGED_FADE` of the segment.
    mix = max(0.0, (into - (period - _UNHINGED_FADE)) / _UNHINGED_FADE)

    def segment(i: int) -> list[tuple[int, int, int]]:
        effect = _UNHINGED_POOL[int(_rand(seed, 3, i // per_effect)
                                    * len(_UNHINGED_POOL)) % len(_UNHINGED_POOL)]
        hue = _rand(seed, 4, i)
        colour = _hsv(hue, 1.0, 1.0)
        offset = _rand(seed, 5, i // per_effect) * 10.0
        # The hue-sweeping effects ignore `colour` and derive their own from the
        # clock, so the segment's hue is applied to them as a time shift instead:
        # both use `t * speed * 0.2` as their hue term, making a shift of
        # hue/(speed*0.2) an exact hue rotation rather than an approximation.
        if effect in ("rainbow", "spectrum") and gain > 0:
            offset += hue / (gain * HUE_CYCLES_PER_GAIN)
        return _render(effect, t + offset, count, colour, gain, seed)

    frame = segment(k) if mix <= 0 else _blend(segment(k), segment(k + 1), mix)

    # Lift towards the segment's colour rather than towards white or grey: a
    # channel floor applied in isolation would desaturate the dark parts of a
    # chase into pastel, and lifting to a fixed grey would do it worse.
    #
    # The blend has to be renormalised to full value before it becomes a floor.
    # Interpolating two saturated hues in RGB passes through the middle of the
    # cube — red to cyan crosses grey — so a raw blend can have a peak channel of
    # 127 rather than 255, and a floor taken from that is half the height it is
    # supposed to be. Measured: it let the strip reach 9% of full scale mid-fade
    # against a 15-20% requirement. Scaling the blend back up keeps its hue and
    # its desaturation while restoring the height the floor is defined against.
    base = _hsv(_rand(seed, 4, k), 1.0, 1.0)
    if mix > 0:
        nxt = _hsv(_rand(seed, 4, k + 1), 1.0, 1.0)
        base = tuple(x + (y - x) * mix for x, y in zip(base, nxt))  # type: ignore[assignment]
    peak = max(base) or 1
    floor = [int(c * 255 / peak * _UNHINGED_FLOOR) for c in base]
    return [tuple(max(c, f) for c, f in zip(px, floor))  # type: ignore[misc]
            for px in frame]


def render(effect: str, t: float, count: int,
           colour: tuple[int, int, int] = (255, 255, 255),
           speed: float = 1.0, seed: int = 0) -> list[tuple[int, int, int]]:
    """One frame for a strip of `count` LEDs at time `t` seconds.

    `speed` is a ladder *label* (see `SPEED_LEVELS`), not a rate; it is converted
    to a multiplier here, exactly once. It rises with perceived speed — unlike the
    devices' own speed fields, where a smaller number is faster.

    `seed` decorrelates zones that are running the same effect. Only `unhinged`
    reads it; every other effect here is a function of the shared clock alone,
    which is exactly what keeps the two controllers in phase.
    """
    return _render(effect, t, count, colour, speed_gain(speed, effect), seed)


def _render(effect: str, t: float, count: int,
            colour: tuple[int, int, int], gain: float,
            seed: int) -> list[tuple[int, int, int]]:
    """The renderer proper, working in gain rather than in labels.

    Split out so `_unhinged` can recurse into it. Recursing through `render`
    instead would convert the label to a gain a second time, which silently ran
    every sub-effect at the wrong speed.
    """
    if effect == "off":
        return [(0, 0, 0)] * count
    if effect == "unhinged":
        return _unhinged(t, count, gain, seed)
    if effect == "static":
        return [colour] * count

    if effect == "spectrum":
        # Whole device on one hue. This is the effect that most obviously
        # exposed the drift between controllers, and the one a shared clock
        # most obviously fixes: every device shows the same hue at the same
        # instant, regardless of strip length.
        return [_hsv(t * gain * HUE_CYCLES_PER_GAIN, 1.0, 1.0)] * count

    if effect == "rainbow":
        # A hue wave along the strip. A 1-LED zone samples position 0, which
        # keeps it in phase with the head of the strip rather than drifting —
        # but with nothing beside it to average against, the hue's own
        # brightness curve becomes the effect. Even it out there and only there:
        # the strip looks right as-is, and flattening it too would cost
        # saturation for nothing.
        if count == 1:
            return [_even(_hsv(t * gain * HUE_CYCLES_PER_GAIN, 1.0, 1.0))]
        return [_hsv(t * gain * HUE_CYCLES_PER_GAIN + (i / max(count, 1)),
                     1.0, 1.0)
                for i in range(count)]

    if effect == "breathing":
        # Sine on brightness, floored so it dips rather than blacking out.
        v = 0.15 + 0.85 * (0.5 + 0.5 * math.sin(t * gain * 2 * math.pi * 0.25))
        return [tuple(int(c * v) for c in colour)] * count  # type: ignore[misc]

    if effect == "chase":
        # A lit head with an exponential tail. A 1-LED zone cannot show travel,
        # so it renders as *one pixel of the strip*: dark, then a flash as the
        # head sweeps past, on the same clock and in exactly the same colour.
        #
        # Two earlier attempts at this were both rejected, and the reasons matter
        # for anyone thinking of changing it back. Holding it lit is not an
        # effect at all — it is indistinguishable from static. Using a tail of
        # 1.0 (what `max(count // 4, 1)` degrades to at count 1) spans the whole
        # cycle and only swings 0.37..1.0, which reads as breathing rather than
        # as a chase.
        #
        # Scaling the tail to the fraction of the strip it actually covers —
        # count // 4, i.e. a quarter — gives 0.018..1.0 over a pass, a clean
        # beat. The travelling block the card's own Runway mode can do is not an
        # option here: every GPU firmware mode reports color_mode=0 and the SDK
        # refuses a colour outright ("Mode validation failed"), so it cannot be
        # made to match the strip.
        if count == 1:
            tail = 0.12
            phase = (t * gain * CHASE_PASSES_PER_GAIN) % 1.0
            level = math.exp(-phase / tail)
            return [tuple(int(c * level) for c in colour)]  # type: ignore[misc]
        pos = (t * gain * CHASE_PASSES_PER_GAIN
               * max(count, 1)) % max(count, 1)
        out = []
        for i in range(count):
            d = (i - pos) % max(count, 1)
            tail = max(count // 4, 1)
            level = math.exp(-d / tail) if d < tail * 3 else 0.0
            out.append(tuple(int(c * level) for c in colour))
        return out  # type: ignore[return-value]

    return [colour] * count
