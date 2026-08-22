"""LED control via the OpenRGB SDK.

Drives whatever zones `config.toml` names, on however many controllers they sit
across — `mimarchy-setup` fills that file in from what OpenRGB detects. The
development rig has two, and they are worth describing because everything below
was learned on them:

  * the motherboard's ASUS Aura USB controller (CPU cooler fan LEDs), and
  * the Sapphire RX 9070 XT's own controller, reached over I2C on the card.

They are genuinely separate hardware. The GPU's ARGB connector is a source, not
a sink, so nothing on the motherboard headers can drive the card's LEDs — I2C
is the only route to them.

Three non-obvious things this handles, all learned the hard way here:

1. Addressable (ARGB) zones come up reporting `leds=0`. OpenRGB cannot know how
   long a strip is, and writing a colour to a zero-length zone silently does
   nothing — no error, no effect. Zones must be resized before any write will
   reach hardware. This alone looked exactly like "unsupported board".
2. Colour writes only take effect in a direct-drive mode. In an effect mode
   (Rainbow, Breathing, ...) the controller keeps running its own animation and
   ignores SDK colours. The two controllers name that mode differently:
   the board calls it `Direct`, the GPU calls it `Static`.
3. Only a narrow detector set may be enabled — see the note in README about
   OpenRGB issue #4888. Broad GPU/I2C probing has been reported to hard-freeze
   this card; the single matching Sapphire detector is safe on kernel 7.1.4.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from openrgb import OpenRGBClient
from openrgb.utils import RGBColor

from mimarchy.config import Config

DIRECT_MODES = ("Direct", "Static")

#: How long to let the GPU settle between the two halves of a mode transition.
#: Measured: 0.3 s was enough to make entering a firmware effect reliable, so
#: 0.4 s is the margin. Only paid on an actual transition, never per frame.
DIRECT_SETTLE = 0.4

#: Logical mode -> the name each controller uses for it.
#:
#: The two controllers expose different sets, so only these five exist on both
#: and can be driven while CPU and GPU are linked:
#:
#:     motherboard  Direct, Off, Static, Breathing, Flashing,
#:                  Spectrum Cycle, Rainbow, Chase Fade, Chase
#:     GPU          Static, Rainbow Wave, Runway, Spectrum Cycle,
#:                  Serial, External Control, Off
#:
#: `static` maps to the board's *Direct*, not its *Static*: Direct is the
#: per-LED SDK path that actually works here, while the board's Static takes a
#: single mode-colour. The GPU has no Direct and its Static does accept SDK
#: colours, so the two differ by name but behave the same.
LOGICAL_MODES: dict[str, dict[str, str]] = {
    "static":   {"motherboard": "Direct",         "gpu": "Static"},
    "rainbow":  {"motherboard": "Rainbow",        "gpu": "Rainbow Wave"},
    "spectrum": {"motherboard": "Spectrum Cycle", "gpu": "Spectrum Cycle"},
    "chase":    {"motherboard": "Chase",          "gpu": "Runway"},
    "off":      {"motherboard": "Off",            "gpu": "Off"},
}


@dataclass
class ZoneInfo:
    """A logical, user-facing target: one zone on one device."""

    key: str
    label: str
    device_name: str
    led_count: int


class RGBError(RuntimeError):
    """OpenRGB is unreachable or has no controllable device."""


def connect(host: str = "127.0.0.1", port: int = 6742,
            name: str = "mimarchy") -> OpenRGBClient:
    """An SDK client, or `RGBError` with the command that fixes it.

    Shared with `mimarchy-setup`, which connects to the same server to list
    devices but wants none of the zone preparation below. One copy so the two
    cannot end up telling the user to start different things.
    """
    try:
        return OpenRGBClient(address=host, port=port, name=name)
    except Exception as exc:  # noqa: BLE001 - surfaced to the user as a clear error
        raise RGBError(
            f"Can't reach the OpenRGB server on {host}:{port} — is it running? "
            "Try: systemctl --user start openrgb.service"
        ) from exc


class RGBController:
    """Drives every detected LED controller via a running `openrgb --server`."""

    def __init__(self, config: Config, host: str = "127.0.0.1", port: int = 6742):
        self._config = config
        self._client = connect(host, port)

        if not self._client.devices:
            raise RGBError("OpenRGB is running but detected no devices.")

        self._prepare_devices()

    def _prepare_devices(self) -> None:
        """Give each configured zone its length. Deliberately does *not* touch
        the mode.

        Zone sizing has to happen on connect — addressable zones come up at
        `leds=0` and silently swallow colour writes (see the module docstring).
        Mode is a different matter: forcing a direct-drive mode here would reset
        the user's chosen effect every time a client connected. The daemon drives
        modes explicitly instead.

        Only the configured zones, and each to its own length. Resizing every
        zone on every device to one global number was harmless on a rig where
        the only two zones were both ours, but OpenRGB drives keyboards, RAM and
        case fans on the same server — and reshaping a stranger's keyboard
        because it happens to be plugged in is not this program's business.
        """
        resized = False
        for key, (_device, zone) in self._targets().items():
            wanted = self._config.leds_for(key)
            # Fixed-length zones (e.g. the GPU's single LED) reject resizing;
            # only addressable strips need — or accept — it.
            #
            # `!=` rather than `<`: growing was all that was ever needed while
            # the configured length overshot the strip, but shrinking has to
            # work too. With `<`, lowering the length to the strip's real value
            # silently did nothing, and a rainbow kept spanning slots that drive
            # no LED — which is what made rainbow indistinguishable from
            # spectrum.
            if len(zone.leds) != wanted:
                try:
                    zone.resize(wanted)
                    resized = True
                except Exception:  # noqa: BLE001 - not an addressable zone
                    pass

        if resized:
            # Zone objects hold stale LED lists after a resize; reconnect so the
            # client re-reads the device layout.
            self._client.clear()
            self._client.update()

    def _targets(self) -> dict[str, tuple[object, object]]:
        """Map config zone name -> (device, zone), matching on device substring."""
        found: dict[str, tuple[object, object]] = {}
        for key, cfg in self._config.zones.items():
            for device in self._client.devices:
                if cfg.device.lower() not in device.name.lower():
                    continue
                if cfg.zone < len(device.zones):
                    found[key] = (device, device.zones[cfg.zone])
                break
        return found

    def list_zones(self) -> list[ZoneInfo]:
        return [
            ZoneInfo(key=key, label=key.replace("_", " "),
                     device_name=device.name, led_count=len(zone.leds))
            for key, (device, zone) in self._targets().items()
        ]

    def _resolve(self, logical_name: str):
        targets = self._targets()
        if logical_name not in targets:
            raise KeyError(
                f"No detected hardware for zone {logical_name!r}. "
                f"Available: {list(targets)}"
            )
        return targets[logical_name]

    def _kind(self, device) -> str:
        """Which side of the LOGICAL_MODES table this device sits on.

        Decided by which dialect's mode names the device actually exposes, not
        by its own name. `radeon` in the name was true of the one card here and
        is not a property of anything: a GeForce or an Intel card speaking the
        same `Rainbow Wave` / `Runway` vocabulary would have been read as a
        motherboard, and every spatial effect it can do would have been quietly
        unavailable — no error, just a card that never gets handed a firmware
        mode.

        Counting matches gets both of this rig's controllers right for the same
        reason it did before (the board offers 5 of the motherboard names and 3
        of the GPU ones; the card 5 and 2), so the name check survives only as
        the tie-break, which is also what answers a device reporting no modes at
        all.
        """
        names = {mode.name.lower() for mode in device.modes}
        score = {
            kind: sum(1 for m in LOGICAL_MODES.values() if m[kind].lower() in names)
            for kind in ("motherboard", "gpu")
        }
        if score["gpu"] != score["motherboard"]:
            return max(score, key=score.__getitem__)
        return "gpu" if "radeon" in device.name.lower() else "motherboard"

    def _ensure_direct(self, device, force: bool = False) -> None:
        """Park `device` in whichever direct-drive mode it has.

        `force` re-sends even when the client already believes the device is
        there — which is necessary, because that belief is not reliable on the
        GPU (see `prepare_zone_for_direct_render`).
        """
        mode_names = [m.name for m in device.modes]
        for candidate in DIRECT_MODES:
            if candidate in mode_names:
                if force or device.modes[device.active_mode].name != candidate:
                    device.set_mode(candidate)
                return

    def available_modes(self, logical_name: str) -> list[str]:
        """Logical modes this target can do, in menu order."""
        device, _ = self._resolve(logical_name)
        kind = self._kind(device)
        names = {m.name for m in device.modes}
        return [k for k, m in LOGICAL_MODES.items() if m[kind] in names]

    def _mode_object(self, device, name: str):
        for m in device.modes:
            if m.name.lower() == name.lower():
                return m
        raise ValueError(f"{device.name} has no mode {name!r}")

    def mode_speed_range(self, logical_name: str,
                         logical_mode: str) -> tuple[int, int] | None:
        """(min, max) for a mode's speed, or None if it has no speed control.

        The bounds are whatever the device reports and are *not* normalised:
        the GPU describes Spectrum Cycle as min=30 max=1, i.e. reversed, where
        a lower number is faster. Callers should treat the pair as an interval
        and not assume min < max.
        """
        device, _ = self._resolve(logical_name)
        name = self._device_mode_name(device, logical_mode)
        if name is None:
            return None
        mode = self._mode_object(device, name)
        lo, hi = getattr(mode, "speed_min", None), getattr(mode, "speed_max", None)
        if lo is None or hi is None or getattr(mode, "speed", None) is None:
            return None
        return (lo, hi)

    def _device_mode_name(self, device, logical_mode: str) -> str | None:
        kind = self._kind(device)
        return LOGICAL_MODES.get(logical_mode, {}).get(kind)

    def set_mode(self, logical_name: str, logical_mode: str,
                 colour: tuple[int, int, int] | None = None,
                 speed: int | None = None) -> None:
        """Apply a mode, sending its parameters rather than just its name.

        `set_mode` accepts a ModeData object and packs its fields, so mutating
        the mode before passing it is how colour and speed actually reach the
        device. Passing only a name sends whatever the mode already held —
        which is why Chase appeared broken: the board reports Chase (and
        Breathing, Flashing, Chase Fade) with `colors=[black]`, so it ran
        correctly and chased black.
        """
        device, _ = self._resolve(logical_name)
        name = self._device_mode_name(device, logical_mode)
        if name is None:
            raise ValueError(
                f"{logical_name} has no mode {logical_mode!r}; "
                f"available: {self.available_modes(logical_name)}"
            )

        def build():
            mode = self._mode_object(device, name)
            if colour is not None and getattr(mode, "colors", None):
                mode.colors = [RGBColor(*colour)] * len(mode.colors)
            if speed is not None and getattr(mode, "speed", None) is not None:
                mode.speed = speed
            return mode

        # Firmware effect -> firmware effect is dropped roughly half the time on
        # the GPU, and the retry below cannot catch it: OpenRGB reports the new
        # mode as active while the card visibly carries on running the old one.
        # Passing through a direct-drive mode first fixes it. Filmed and counted
        # -- Rainbow Wave to Runway landed 2 of 5 times direct, 5 of 5 with this
        # bounce; 0.3s was enough, so 0.4s is the margin.
        current = device.modes[device.active_mode].name
        if (name not in DIRECT_MODES and current not in DIRECT_MODES
                and current.lower() != name.lower()):
            self._ensure_direct(device)
            time.sleep(DIRECT_SETTLE)

        # Apply, then confirm and re-send once if it didn't take.
        #
        # The motherboard intermittently ignores the first mode packet, which is
        # what made it look like every mode needed pressing twice. Retrying here
        # means the user doesn't have to. `update()` first: the client caches
        # active_mode, so without a resync the check would pass against stale
        # state — and note a *newly connected* client reports active_mode=0,
        # which on this board is "Direct", so a bare readback can invent a mode
        # change that never happened.
        # Send an effect mode exactly ONCE.
        #
        # A direct mode is stateless and safe to repeat, which is what makes the
        # settle-and-resend in `prepare_zone_for_direct_render` work. Repeating
        # an *effect* mode is not obviously safe — a second packet mid-transition
        # could plausibly restart or abort it — so this does not do it, matching
        # the behaviour that measured about 14 successes in 15 attempts.
        #
        # Not a proven hazard: an attempt at re-sending here coincided with a run
        # of total entry failure, but the card had already dropped off the I2C
        # bus by then, so the two cannot be separated. Left as single-send
        # because that is the version with evidence behind it, not because the
        # alternative was shown to be worse.
        device.set_mode(build())

        for _ in range(2):
            try:
                device.update()
            except Exception:  # noqa: BLE001 - resync is best-effort
                return
            if device.modes[device.active_mode].name.lower() == name.lower():
                return
            device.set_mode(build())

    #: Measured (speed field, seconds per pass) pairs, from filming the bar and
    #: timing a full traversal. Two anchors per mode is enough because rate goes as
    #: a power of the field, so two points fix the exponent.
    #:
    #: Measured on the RX 9070 XT and applied to whatever else turns up, because
    #: the alternative is not asking the firmware for a rate at all. The result
    #: on unmeasured hardware is an approximation clamped to that device's own
    #: reported range — a firmware effect running at the wrong speed rather than
    #: a firmware effect that never runs.
    _PERIOD_ANCHORS = {
        "rainbow": ((10, 0.61), (250, 10.63)),
        "chase": ((5, 1.26), (50, 10.83)),
    }

    def firmware_speed_for_period(self, logical_name: str, logical_mode: str,
                                  seconds: float) -> int | None:
        """The speed field whose pass takes closest to `seconds`.

        By *period*, not by ladder position. Mapping a position on our ladder
        onto a position in the firmware's range lines the ends up and guarantees
        a mismatch everywhere between — measured, it left the card running about
        twice our rate at every stop. Asking for the field value whose pass takes
        as long as the renderer's is the only sense in which a device on its own
        clock can be said to match.

        Fitted as `period = p0 * (field / f0) ** k` with `k` from the two measured
        anchors, then inverted. Clamped to the mode's reported range, and the clamp
        is real rather than theoretical: Rainbow Wave bottoms out at 10.63 s per
        pass, so the two slowest stops on our ladder (25 s and 11.2 s) are slower
        than the card can go and both land on its slowest setting.
        """
        anchors = self._PERIOD_ANCHORS.get(logical_mode)
        span = self.mode_speed_range(logical_name, logical_mode)
        if anchors is None or span is None or seconds <= 0:
            return None
        (f0, p0), (f1, p1) = anchors
        if f0 <= 0 or f1 <= 0 or p0 <= 0 or p1 <= 0 or f1 == f0:
            return None
        k = math.log(p1 / p0) / math.log(f1 / f0)
        if k == 0:
            return None
        field = f0 * (seconds / p0) ** (1.0 / k)
        lo, hi = min(span), max(span)
        return int(round(max(lo, min(hi, field))))

    def prepare_zone_for_direct_render(self, logical_name: str) -> None:
        """Direct-drive just this zone's device, leaving the others alone.

        Per-zone rather than blanket, so that one device can run a firmware
        effect while another is rendered without being dragged out of it.

        *Leaving* a firmware effect is far less reliable than entering one: a
        single request was dropped 5 times out of 5 on the GPU, which is what
        left it stuck animating rainbow while the strip had already moved on to
        the next effect. The client reports Static as active either way, so
        there is nothing to check — the fix is to send it, let the card settle,
        and send it again unconditionally.
        """
        device, _ = self._resolve(logical_name)
        leaving_effect = device.modes[device.active_mode].name not in DIRECT_MODES
        self._ensure_direct(device)
        if leaving_effect:
            time.sleep(DIRECT_SETTLE)
            self._ensure_direct(device, force=True)

    def write_frame(self, logical_name: str,
                    frame: list[tuple[int, int, int]]) -> None:
        """Push one rendered frame to a zone.

        `fast=True` skips waiting for a reply. At 30 fps the round-trip would
        otherwise dominate the frame budget, and a dropped frame is invisible.
        """
        _, zone = self._resolve(logical_name)
        leds = len(zone.leds)
        if not leds:
            return
        if len(frame) < leds:
            frame = list(frame) + [frame[-1]] * (leds - len(frame))
        zone.set_colors([RGBColor(*c) for c in frame[:leds]], fast=True)

