"""Lighting daemon: renders effects to every controller from one clock.

Why this exists rather than using the controllers' own effect modes:

  * The board exposes no speed control (HAS_SPEED is unset on all its modes),
    so its rainbow ran at whatever rate the firmware chose and could not be
    matched to the GPU's.
  * Each controller animates from its own free-running clock, so in spectrum or
    breathing they showed different colours at the same instant and drifted
    further apart over time.
  * Switching hardware modes is slow and occasionally drops a change — cycling
    modes quickly left the GPU a mode behind.

Driving both in software from a shared clock removes all three: the devices are
in phase by construction, speed is ours to pick, and changing effect is just a
different frame rather than a mode negotiation.

The cost is that lighting only animates while this runs — which is why it's a
service rather than something the panel does while open.
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
import zlib

from mimarchy import detection, lightstate
from mimarchy.config import Config, load_config
from mimarchy.effects import (COLOUR_EFFECTS, SPATIAL_EFFECTS,
                              firmware_period, render,
                              unhinged_firmware_phase)
from mimarchy.rgb import RGBController, RGBError

#: The board tops out around 60 fps for a 60-LED write (measured 15.9 ms/frame).
#: 30 leaves comfortable headroom and is smooth for these effects.
FPS = 30

#: How long to keep asking OpenRGB for a configured zone that is absent when
#: the daemon starts, before settling for the zones that are there.
#:
#: Two different absences look identical at startup. OpenRGB's SDK listener
#: opens before its detection pass has finished, so a daemon that connects
#: early can be handed a device list with the slow-to-detect controllers not
#: in it yet; that one resolves itself in seconds. A controller that has
#: genuinely gone — the graphics card in the September 2026 incident, whose
#: LED microcontroller dropped off its I2C bus and stayed off — never
#: resolves. Polling for a bounded window tells the two apart: the first is
#: caught by the second or third look, and the second costs one delayed start
#: and a plain line in the journal rather than a zone that is silently dark
#: for a week. After the window the daemon runs with whatever it found, since
#: the zones that *were* detected are still worth lighting.
MISSING_ZONE_GRACE = 20.0
MISSING_ZONE_POLL = 4.0

#: How long every render write may fail continuously before the daemon exits.
#: Long enough to ride out any transient — OpenRGB restarting under systemd
#: takes a couple of seconds — and short enough that a dead server is noticed
#: while someone is still looking at the frozen LEDs.
WRITE_FAILURE_WINDOW = 5.0


class WriteFailureWatch:
    """Decides when continuous write failure means the connection is dead.

    write_frame failures are swallowed per frame on purpose — one dropped
    frame is invisible, and exiting on it would flap the service. But the
    swallow hid the opposite case too: when the OpenRGB server goes away
    (crash, restart, an OOM kill), every write fails from then on, the client
    library never repairs its socket, and the daemon kept rendering into it
    forever — LEDs frozen, service green.

    This draws the line between the two cases: any successful write resets
    the watch, and only a full WRITE_FAILURE_WINDOW during which *no* write
    succeeded trips it. A frame that attempted nothing proves nothing — an
    all-firmware plan writes no frames — so it neither feeds nor resets the
    streak, and a dead server is still caught the moment rendering resumes.

    Tripping is not handled here. The caller exits, and systemd's Restart=
    brings the daemon back through its normal startup, which reconnects —
    the one recovery path that also re-prepares zones and re-applies state.
    """

    def __init__(self, window: float = WRITE_FAILURE_WINDOW):
        self._window = window
        self._failing_since: float | None = None

    def record(self, t: float, *, wrote: int, failed: int) -> bool:
        """Feed one frame's outcome; True when the dead-connection line is crossed."""
        if wrote:
            self._failing_since = None
            return False
        if not failed:
            return False
        if self._failing_since is None:
            self._failing_since = t
        return (t - self._failing_since) >= self._window


def missing_zones(config: Config, detected: dict[str, int]) -> list[str]:
    """Configured zone keys OpenRGB did not produce a device for, in config order."""
    return [key for key in config.zones if key not in detected]


def describe_missing(config: Config, missing: list[str]) -> str:
    """One journal line per absent zone, saying what was looked for and where
    to look next — the message this incident lacked."""
    lines = []
    for key in missing:
        zone = config.zones[key]
        lines.append(
            f"zone {key!r} not detected: no OpenRGB device matching "
            f"{zone.device!r} (zone {zone.zone}). Check `mimarchy-setup --list`; "
            "if the device is missing there too, OpenRGB did not detect it — "
            "for a GPU whose controller has dropped off its I2C bus, a cold "
            "boot (power off at the PSU) is the known fix."
        )
    return "\n".join(lines)


def settle_zones(config: Config, connect, *, grace: float = MISSING_ZONE_GRACE,
                 poll: float = MISSING_ZONE_POLL, clock=time.monotonic,
                 sleep=time.sleep):
    """Connect, and keep reconnecting for up to `grace` seconds while a
    configured zone is missing.

    Returns the controller and its zone map once every configured zone is
    present or the window closes. Each look is a fresh connection because the
    SDK client enumerates devices once, on connect; there is no cheaper way to
    ask OpenRGB again. `connect` is the controller factory — injected so the
    tests can hand back a sequence of device lists without a server.
    """
    deadline = clock() + grace
    while True:
        rgb = connect()
        zones = {z.key: z.led_count for z in rgb.list_zones()}
        missing = missing_zones(config, zones)
        if not missing or clock() >= deadline:
            return rgb, zones, missing
        sleep(poll)


def report_detection(rgb, config: Config, zones: dict[str, int]) -> detection.Detection:
    """What `mimarchy-ctl status` will show for each configured zone."""
    found = {z.key: z for z in rgb.list_zones()}
    report = detection.Detection()
    for key, cfg in config.zones.items():
        info = found.get(key)
        report.zones[key] = detection.ZoneDetection(
            configured_device=cfg.device,
            device_name=info.device_name if info else None,
            led_count=zones.get(key, 0),
        )
    return report


def _seed(zone_key: str) -> int:
    """A stable per-zone seed for effects that need to *not* be in phase.

    Every other effect here is driven purely by the shared clock, which is what
    keeps the controllers matching. Unhinged is the one that wants the opposite —
    two zones deliberately decorrelated — and it gets that from this seed rather
    than from a random generator, so the sequence is identical across daemon
    restarts and reproducible in a test.

    `crc32` and not `hash()`: string hashing is salted per process, so a zone's
    sequence would silently differ between runs.
    """
    return zlib.crc32(zone_key.encode())


def _linked_pair(state: lightstate.LightingState, key: str,
                  zones: dict[str, int]) -> bool:
    """Whether this zone is joined by the link on this daemon run.

    Linking is *every* configured zone, not a hardcoded pair — `zones` is
    whatever this rig actually has, so a board-plus-strip machine with no
    `gpu` at all links the zones it does have instead of linking nothing. The
    consequence is the intended one: a colour-carrying effect on every
    one-LED zone in the link stays rendered, matching the rest, for as long
    as the zones are linked. Which is the default. That is the cost of
    "make these match" — the user asked for it, and it now applies to
    whatever devices they actually own.
    """
    return state.linked and key in zones


def _source_target(state: lightstate.LightingState, key: str,
                    zones: dict[str, int]):
    """The state entry a zone follows — the shared one while linked."""
    source = next(iter(zones)) if _linked_pair(state, key, zones) else key
    return state.for_target(source)


def rotation(zones: dict[str, int], state: lightstate.LightingState,
             t: float) -> tuple:
    """Which firmware phase each Unhinged one-LED zone is in right now.

    The daemon re-plans when this changes. Unhinged is the one effect whose
    routing is a function of *time* rather than only of state, so without this the
    plan would be recomputed only when the user pressed something and the rotation
    would never advance.
    """
    return tuple(
        (key, unhinged_firmware_phase(t, _seed(key)))
        for key, count in sorted(zones.items())
        if count == 1 and _source_target(state, key, zones).effect == "unhinged"
    )


def plan(rgb: RGBController, zones: dict[str, int],
         state: lightstate.LightingState, t: float = 0.0) -> tuple[dict, dict]:
    """Split the zones into ones we render and ones the firmware runs.

    Rendering everything from one clock is what keeps the devices in phase, and
    it stays the default. But it can only ever send *one colour per controllable
    LED*, and the GPU exposes exactly one for a bar that physically has many —
    so a hue wave and a travelling head both arrive as a single flat colour,
    which is spectrum and static respectively. That is a real loss of effects
    the card can otherwise do: its own Rainbow Wave and Runway animate across
    the bar's internal segments, which no amount of SDK writing can reach.

    So a spatial effect on a single-LED zone goes to the firmware, and
    everything else is rendered. Phase locking is given up only where it buys
    nothing: static, breathing and spectrum put one colour on the whole device,
    which is exactly where drift between controllers is visible, and those stay
    rendered.

    Whether the link blocks that hand-off depends on whether the effect has a
    colour of its own, and the two cases genuinely differ:

    * **rainbow goes to firmware even while linked.** Every firmware effect on
      this card reports `color_mode=0` and accepts no colour, which is normally
      disqualifying — but rainbow has no chosen colour to honour. The card's
      Rainbow Wave *is* the full hue wheel, the same thing the strip is showing, so
      "the card picks its own colours" describes no actual difference. Rendering it
      instead put one flat hue on a bar with many segments, which reads as spectrum
      and was reported as rainbow being broken.
    * **chase stays rendered while linked.** It carries a chosen colour, and
      Runway ignores it — filmed running yellow while the strip ran red. That is a
      visible mismatch between two devices the user asked to look the same.

    What firmware cannot give is a shared clock, so its rate is matched by
    *period*: `firmware_speed_for_period` asks the card to complete a pass in the
    same time the renderer takes for a hue cycle. It matches at the fast end and
    clamps at the slow end, where the card cannot go slower than 10.63 s per pass.
    """
    rendered: dict[str, tuple[lightstate.TargetState, int]] = {}
    firmware: dict[str, tuple[str, int | None, tuple[int, int, int]]] = {}
    for key, count in zones.items():
        target = _source_target(state, key, zones)

        # Unhinged on a one-LED zone rotates: mostly rendered colour churn, with
        # occasional spells of the card's own Rainbow Wave and Runway so the bar
        # gets a hue sweep and a travelling dot. The linked-colour objection does
        # not apply here even when the phase is chase, because unhinged has no
        # chosen colour for the firmware to ignore.
        if count == 1 and target.effect == "unhinged":
            phase = unhinged_firmware_phase(t, _seed(key))
            if phase is not None and phase in rgb.available_modes(key):
                firmware[key] = (phase,
                                 rgb.firmware_speed_for_period(
                                     key, phase,
                                     firmware_period(phase, target.speed)),
                                 tuple(target.colour))
            else:
                rendered[key] = (target, count)
            continue

        colour_blocks_firmware = (_linked_pair(state, key, zones)
                                  and target.effect in COLOUR_EFFECTS)
        if (count == 1 and target.effect in SPATIAL_EFFECTS
                and not colour_blocks_firmware
                and target.effect in rgb.available_modes(key)):
            # Match the renderer's period where the card can. A mode with no
            # timing anchors gets None, which `set_mode` treats as "leave the
            # firmware's own rate alone" — a reasonable degradation for hardware
            # nobody has measured.
            speed = rgb.firmware_speed_for_period(
                key, target.effect,
                firmware_period(target.effect, target.speed))
            firmware[key] = (target.effect, speed, tuple(target.colour))
        else:
            rendered[key] = (target, count)
    return rendered, firmware


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fps", type=float, default=FPS)
    ap.add_argument("--once", action="store_true",
                    help="render a single frame and exit (for testing)")
    args = ap.parse_args()

    config = load_config()
    try:
        rgb, zones, missing = settle_zones(config, lambda: RGBController(config))
    except RGBError as exc:
        sys.exit(str(exc))

    # Written before the zone check below exits, so `status` can say *which*
    # zones were missing even when the answer was all of them.
    detection.save(report_detection(rgb, config, zones))
    if not zones:
        sys.exit("no controllable zones detected\n" + describe_missing(config, missing))
    if missing:
        print(describe_missing(config, missing), file=sys.stderr)
        print(f"rendering the zones that were found: {', '.join(zones)}",
              file=sys.stderr)

    state = lightstate.load()
    last_seen = lightstate.mtime()

    stopping = False

    def _stop(*_: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    start = time.monotonic()
    rendered, firmware = plan(rgb, zones, state, 0.0)
    last_rotation = rotation(zones, state, 0.0)
    applied: dict[str, tuple] = {}

    def apply_plan() -> None:
        """Put each device where its half of the plan needs it.

        Firmware modes are set only when they actually change: switching them is
        slow on this card and occasionally drops a packet, so re-sending one
        every state change would make the GPU visibly stutter.
        """
        for key, (target, _count) in rendered.items():
            if applied.get(key) != ("render",):
                try:
                    rgb.prepare_zone_for_direct_render(key)
                except Exception:  # noqa: BLE001 — retried on the next change
                    continue
                applied[key] = ("render",)
        for key, spec in firmware.items():
            if applied.get(key) == ("firmware", *spec):
                continue
            effect, speed, colour = spec
            try:
                rgb.set_mode(key, effect, colour=colour, speed=speed)
            except Exception:  # noqa: BLE001 — retried on the next change
                continue
            applied[key] = ("firmware", *spec)

    apply_plan()

    watch = WriteFailureWatch()
    period = 1.0 / max(args.fps, 1.0)

    while not stopping:
        frame_start = time.monotonic()
        t = frame_start - start

        # Re-read only when the file actually changed, but also re-plan when
        # Unhinged's firmware rotation moves on — that one is driven by the clock
        # rather than by the user.
        now = lightstate.mtime()
        turn = rotation(zones, state, t)
        if now != last_seen or turn != last_rotation:
            if now != last_seen:
                state = lightstate.load()
                last_seen = now
                turn = rotation(zones, state, t)
            last_rotation = turn
            rendered, firmware = plan(rgb, zones, state, t)
            apply_plan()

        wrote = failed = 0
        for key, (target, count) in rendered.items():
            frame = render(target.effect, t, count,
                           colour=tuple(target.colour), speed=target.speed,
                           seed=_seed(key))
            try:
                rgb.write_frame(key, frame)
                wrote += 1
            except Exception:  # noqa: BLE001 — a dropped frame is not fatal
                failed += 1

        if watch.record(t, wrote=wrote, failed=failed):
            sys.exit(f"no render write has succeeded for {WRITE_FAILURE_WINDOW:g}s"
                     " — treating the OpenRGB connection as dead and exiting,"
                     " so systemd restarts this daemon into a fresh one")

        if args.once:
            return

        elapsed = time.monotonic() - frame_start
        time.sleep(max(0.0, period - elapsed))


if __name__ == "__main__":
    main()
