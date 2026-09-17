"""LED control, driven directly from stdlib-only drivers.

One facade over two controllers that are genuinely separate hardware:

  * the motherboard's ASUS Aura USB controller (`mimarchy.aura`), and
  * the Sapphire RX 9070 XT's own Nitro Glow controller (`mimarchy.nitro`),
    reached over I2C on the card — the GPU's ARGB connector is a source, not
    a sink, so nothing on the motherboard headers can drive the card's LEDs.

Three non-obvious things this handles, all learned the hard way here:

1. Addressable (ARGB) strips have no length until told. A strip shows whatever
   frames arrive, so the render length comes from `config.toml`, and a
   zero-length zone is skipped rather than written to no effect.
2. Rendered colours only show while the Nitro card is in external control.
   In a firmware effect mode the controller runs its own animation and ignores
   colour writes, so entering and leaving firmware is explicit.
3. Only narrow buses are ever probed — see `mimarchy.nitro` for why a broad
   I2C scan is not on the table.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from mimarchy import aura as _aura
from mimarchy import hidraw, nitro as _nitro
from mimarchy import smbus
from mimarchy.config import Config

#: How long to let the GPU settle between the two halves of a mode transition.
#: Measured: 0.3 s was enough to make entering a firmware effect reliable, so
#: 0.4 s is the margin. Only paid on an actual transition, never per frame.
DIRECT_SETTLE = 0.4


@dataclass
class ZoneInfo:
    """A logical, user-facing target: one zone on one device."""

    key: str
    label: str
    device_name: str
    led_count: int


class RGBError(RuntimeError):
    """No lighting controller answered, or none matches the config."""


def _is_gpu_zone(key: str, cfg) -> bool:
    """Whether this config zone points at the card rather than the board.

    The key is the authority — `mimarchy-setup` suggests `gpu` for the card —
    and the device string is the fallback, so configs written against the old
    substring matching (`device = "Sapphire"`) keep routing to the card.
    """
    if key == "gpu":
        return True
    return any(s in cfg.device.lower()
               for s in ("sapphire", "nitro", "radeon", "gpu"))


class RGBController:
    """Drives every configured LED controller through its own driver."""

    #: Measured (speed field, seconds per pass) pairs — the single source is
    #: the Nitro driver's own anchors; see `firmware_speed_for_period`.
    _PERIOD_ANCHORS = dict(_nitro.SPEED_ANCHORS)

    def __init__(self, config: Config):
        self._config = config
        self._aura: _aura.AuraBoard | None = None
        self._nitro: _nitro.NitroCard | None = None
        try:
            self._aura = _aura.AuraBoard.open()
        except hidraw.HidError:
            pass
        try:
            self._nitro = _nitro.NitroCard.open()
        except smbus.I2cError:
            pass

        if self._aura is None and self._nitro is None:
            raise RGBError(
                "No lighting controller answered. For the board, the Aura "
                "hidraw node is root-only until udev/99-mimarchy.rules is "
                "installed (see README); for the card, a bar that went dark "
                "suddenly usually needs a cold boot (power off at the PSU). "
                "Run `mimarchy-setup --list` to see what was found."
            )

        self._zones: dict[str, tuple[str, _aura.Channel | None]] = {}
        for key, cfg in config.zones.items():
            if _is_gpu_zone(key, cfg):
                if self._nitro is not None:
                    self._zones[key] = ("nitro", None)
            elif (self._aura is not None
                    and 0 <= cfg.zone < len(self._aura.channels)):
                self._zones[key] = ("aura", self._aura.channels[cfg.zone])
        # What the card is showing, as far as this process told it: a firmware
        # effect name, or None while it is under external (rendered) control.
        # Unknown until the first call below sets it.
        self._nitro_firmware: str | None = None
        self._nitro_external: bool | None = None

    def close(self) -> None:
        for dev in (self._aura, self._nitro):
            if dev is not None:
                try:
                    dev.close()
                except Exception:  # noqa: BLE001 - close is best-effort
                    pass

    def __enter__(self) -> "RGBController":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def list_zones(self) -> list[ZoneInfo]:
        infos = []
        for key, (kind, channel) in self._zones.items():
            if kind == "nitro":
                infos.append(ZoneInfo(key=key, label=key.replace("_", " "),
                                      device_name="Sapphire Nitro Glow V3",
                                      led_count=1))
            else:
                assert channel is not None
                infos.append(ZoneInfo(key=key, label=key.replace("_", " "),
                                      device_name=channel.name,
                                      led_count=self._config.leds_for(key)))
        return infos

    def _resolve(self, logical_name: str):
        try:
            return self._zones[logical_name]
        except KeyError:
            raise KeyError(
                f"No detected hardware for zone {logical_name!r}. "
                f"Available: {list(self._zones)}"
            ) from None

    def available_modes(self, logical_name: str) -> list[str]:
        """Firmware effects this target can run itself, in menu order."""
        kind, _ = self._resolve(logical_name)
        if kind != "nitro":
            # The board only speaks Direct — every Aura frame is rendered.
            return []
        return [*_nitro.MODE_FOR_EFFECT, "off"]

    def mode_speed_range(self, logical_name: str,
                         logical_mode: str) -> tuple[int, int] | None:
        """(min, max) for a mode's speed, or None if it has no speed control.

        The bounds are whatever the controller documents and are *not*
        normalised: Spectrum Cycle runs min=30 max=1, i.e. reversed, where a
        lower number is faster. Callers should treat the pair as an interval
        and not assume min < max.
        """
        kind, _ = self._resolve(logical_name)
        if kind != "nitro":
            return None
        entry = _nitro.MODE_FOR_EFFECT.get(logical_mode)
        if entry is None:
            return None
        _, _, lo, hi = entry
        return (lo, hi)

    def set_mode(self, logical_name: str, logical_mode: str,
                 colour: tuple[int, int, int] | None = None,
                 speed: int | None = None) -> None:
        """Run a firmware effect on the card's own clock.

        Colour is accepted and ignored: every firmware effect on this card
        picks its own colours, which is exactly why the link keeps
        colour-carrying effects rendered instead of handing them over (see
        `lightd.plan`). Speed goes first, then the mode — the controller
        latches the rate register on entry, so the reverse order runs one
        pass at the old rate.

        Firmware effect -> firmware effect is dropped roughly half the time
        on this card. Passing through external control first fixes it. Filmed
        and counted — Rainbow Wave to Runway landed 2 of 5 times direct,
        5 of 5 with this bounce; 0.3 s was enough, so 0.4 s is the margin.
        """
        kind, _ = self._resolve(logical_name)
        if kind != "nitro":
            raise ValueError(
                f"{logical_name} has no mode {logical_mode!r}; "
                f"available: {self.available_modes(logical_name)}"
            )
        assert self._nitro is not None
        if logical_mode == "off":
            mode_val, reg = _nitro.MODE_OFF, None
        else:
            entry = _nitro.MODE_FOR_EFFECT.get(logical_mode)
            if entry is None:
                raise ValueError(
                    f"{logical_name} has no mode {logical_mode!r}; "
                    f"available: {self.available_modes(logical_name)}"
                )
            mode_val, reg, _, _ = entry

        if (self._nitro_firmware is not None
                and self._nitro_firmware != logical_mode):
            self._nitro.set_external(True)
            time.sleep(DIRECT_SETTLE)

        # Send the effect exactly ONCE.
        #
        # External control is stateless and safe to repeat, which is what makes
        # the settle-and-resend in `prepare_zone_for_direct_render` work.
        # Repeating an *effect* mode is not obviously safe — a second write
        # mid-transition could plausibly restart or abort it — so this does
        # not do it, matching the behaviour with evidence behind it.
        self._nitro.set_external(False)
        if speed is not None and reg is not None:
            self._nitro.set_speed(reg, speed)
        self._nitro.set_mode(mode_val)
        self._nitro_firmware = logical_mode
        self._nitro_external = False

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
        """Put just this zone's controller under external control.

        Per-zone rather than blanket, so that one device can run a firmware
        effect while another is rendered without being dragged out of it. For
        an Aura zone this re-sends the Direct-entry packet, since another tool
        (or a reboot) can leave the board in a firmware effect that ignores
        direct frames; the open path already sent it once (see `aura.py`).

        *Leaving* a firmware effect is far less reliable than entering one: a
        single request was dropped 5 times out of 5 on the card, which is what
        left it stuck animating rainbow while the strip had already moved on
        to the next effect. There is nothing to check — the fix is to send it,
        let the card settle, and send it again unconditionally.
        """
        kind, channel = self._resolve(logical_name)
        if kind != "nitro":
            assert self._aura is not None and channel is not None
            self._aura.enter_direct(channel)
            return
        assert self._nitro is not None
        self._nitro.set_external(True)
        time.sleep(DIRECT_SETTLE)
        self._nitro.set_external(True)
        self._nitro_external = True
        self._nitro_firmware = None

    def write_frame(self, logical_name: str,
                    frame: list[tuple[int, int, int]]) -> None:
        """Push one rendered frame to a zone."""
        kind, channel = self._resolve(logical_name)
        if kind == "nitro":
            assert self._nitro is not None
            if not frame:
                return
            # One controllable LED: the frame's head pixel is the whole zone.
            # External control is ensured, not assumed — a card left in a
            # firmware effect would otherwise ignore every colour sent.
            if self._nitro_external is not True:
                self._nitro.set_external(True)
                self._nitro_external = True
                self._nitro_firmware = None
            r, g, b = (int(c) for c in frame[0])
            self._nitro.set_colour((r, g, b))
            return
        assert self._aura is not None and channel is not None
        leds = self._config.leds_for(logical_name)
        if not leds:
            return
        if len(frame) < leds:
            frame = list(frame) + [frame[-1]] * (leds - len(frame))
        self._aura.set_channel(channel, list(frame[:leds]))
