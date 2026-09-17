"""ASUS Aura USB motherboard controller (0b05:19af), driven over raw hidraw.

Ports the packet layout from OpenRGB's `AsusAuraUSBController` tree
(`AsusAuraUSBController.cpp`, `AsusAuraMainboardController.cpp`,
`AsusAuraAddressableController.cpp`, `RGBController_AsusAuraUSB.cpp` —
GPL-2.0-or-later, by Adam Honse and Martin Hartl), minus everything OpenRGB
needs that a single-board tool does not: no firmware queries at import, no
multi-device dispatch, no effect modes beyond Direct (Mimarchy renders its own
effects in software; see `lightd`).

*Protocol, as the firmware sees it.* The HID report descriptor declares report
ID `0xEC` with 64-byte input/output reports, so every transfer is 65 bytes
starting with `0xEC` — that first byte is the report id, not payload:

* config table (`0xB0`) — one write, one 65-byte read; 60 bytes copied from
  offset 4 when byte 1 replies `0x30`. Byte `0x02` counts the addressable
  headers; bytes `0x1B`/`0x1D` count onboard LEDs and 12V RGB headers. This is
  how OpenRGB learns the channel list, and hence how many zones the board has
  and which channels are strips.
* direct frame (`0x40`) — `[0xEC, 0x40, 0x80|channel|0x00, offset, count,
  r,g,b ...]`, 0x14 LEDs per packet, the last packet of a channel ORed with
  `0x80` to apply. Channel numbers are the *direct* channel (addressable
  headers are addressed by header index), not the effect channel.
* Direct entry (`0x35`) — `[0xEC, 0x35, effect_channel, 0x00, 0x00, 0xFF]`,
  one packet per channel, sent before the first direct frame. The board
  powers up (and is left by other tools) in a firmware effect mode where it
  runs its own animation and ignores direct frames, so OpenRGB parks every
  zone in Direct first (`DeviceUpdateMode` sends `SetMode(zone, Direct)`,
  which is just this packet with mode `0xFF`, before the first
  `DeviceUpdateLEDs`). Channel here is the *effect* channel — the zone's
  position in the channel list — which only coincides with the direct
  channel while there is no onboard zone (the onboard zone is effect 0 but
  direct `0x04`, pushing every header's effect channel up by one).
* Gen1 init (`EC 52 53 00 01`) — sent once after open on mainboard controllers.

The board's *registers* are channels fixed at probe time (onboard LEDs, then
one per addressable header). There is no runtime length to set: a strip shows
whatever frames arrive, so `led_count` here is the configured strip length to
render, not anything negotiated with the controller. Only the channels in the
config are written — never the stranger's keyboard on the same bus, which is
also why there is no scan: this opens a fixed VID/PID and reads its table.
"""

from __future__ import annotations

from dataclasses import dataclass

from mimarchy import hidraw

VID = 0x0B05
PID = 0x19AF

LEDS_PER_PACKET = 0x14
MAX_ADDRESSABLE = 120

_REQ_CONFIG = 0xB0
_CONFIG_OK = 0x30
_MODE_DIRECT = 0x40
_MODE_EFFECT = 0x35
_EFFECT_DIRECT = 0xFF
_GEN1 = bytes((0xEC, 0x52, 0x53, 0x00, 0x01))

# Config-table field offsets, straight from OpenRGB's AuraMainboardController
# constructor (AuraMain.cpp): byte 0x02 counts the addressable headers, 0x1B
# the onboard LEDs, 0x1D the 12 V RGB headers.
_OFF_ADDRESSABLE = 0x02
_OFF_ONBOARD = 0x1B
_OFF_HEADERS_12V = 0x1D


@dataclass
class Channel:
    index: int
    name: str
    addressable: bool
    direct_channel: int
    effect_channel: int

def _packet(*body: int) -> bytes:
    """A 65-byte HID report starting with the `0xEC` report id.

    OpenRGB fills `usb_buf[65]` with `usb_buf[0] = 0xEC` and passes all 65
    bytes to `hid_write`, which writes them verbatim to the hidraw node —
    the first byte is the report id the descriptor declares, not payload.
    """
    if len(body) > hidraw.REPORT_LEN:
        raise ValueError(f"Aura packet holds 65 bytes, got {len(body)}")
    return bytes(body).ljust(hidraw.REPORT_LEN, b"\x00")


class AuraBoard:
    def __init__(self, dev: hidraw.HidDevice, path: str):
        self._dev = dev
        self.path = path
        self.channels: list[Channel] = []
        self._read_table()

    @classmethod
    def open(cls, sysfs: str = "/sys/bus/hid/devices") -> "AuraBoard":
        nodes = hidraw.find(VID, PID, sysfs)
        if not nodes:
            raise hidraw.HidError(
                "no ASUS Aura USB controller (0b05:19af) found. "
                "Is the board's RGB header controller exposed over USB?"
            )
        last: Exception | None = None
        for node in nodes:
            try:
                return cls(hidraw.HidDevice(node.path), node.path)
            except hidraw.HidError as exc:
                last = exc
        raise hidraw.HidError(str(last) if last else "no Aura controller")

    def _query(self, request: int, expect: int) -> bytes:
        self._dev.write(_packet(0xEC, request))
        reply = self._dev.read()
        if len(reply) < 2:
            raise hidraw.HidError(
                f"Aura controller at {self.path} answered "
                f"{len(reply)} bytes, expected a 65-byte report"
            )
        if reply[1] != expect:
            raise hidraw.HidError(
                f"Aura controller at {self.path} answered "
                f"0x{reply[1]:02x}, expected 0x{expect:02x}"
            )
        return reply

    def _read_table(self) -> None:
        # OpenRGB copies 60 bytes from reply offset 4 on a 0x30 answer
        # (AuraUSBController::GetConfigTable); this slice is that copy.
        table = self._query(_REQ_CONFIG, _CONFIG_OK)[4:64]
        addressable = table[_OFF_ADDRESSABLE]
        onboard = table[_OFF_ONBOARD]
        # _OFF_HEADERS_12V (table[0x1D]) deliberately unread: the 12 V headers
        # hang off the onboard zone's tail — OpenRGB subtracts them only for
        # LED naming — so they add no channel of their own.
        channels: list[Channel] = []
        if onboard > 0:
            channels.append(Channel(0, "Aura Mainboard", False, 0x04, 0))
        for i in range(min(addressable, 8)):
            channels.append(Channel(len(channels),
                                    f"Addressable RGB Header {i + 1}",
                                    True, i, len(channels)))
        if not channels:  # pragma: no cover — table parsed but empty
            raise hidraw.HidError(
                f"Aura controller at {self.path} reported no channels")
        self.channels = channels
        self._dev.write(_GEN1.ljust(hidraw.REPORT_LEN, b"\x00"))
        for channel in channels:
            self.enter_direct(channel)

    def enter_direct(self, channel: Channel) -> None:
        """Park one channel in Direct mode so it honours direct frames.

        This is OpenRGB's `AuraMainboardController::SetMode` with mode Direct
        (`SendEffect(effect_channel, 0xFF)`), which `DeviceUpdateMode` sends
        for every zone before the first `DeviceUpdateLEDs`. Without it the
        board stays in whatever firmware effect it powered up in and ignores
        the `0x40` frames `set_channel` sends. The channel addressed is the
        *effect* channel (`index`), not the direct channel the frames use.
        """
        self._dev.write(_packet(0xEC, _MODE_EFFECT, channel.effect_channel,
                                0x00, 0x00, _EFFECT_DIRECT))

    def set_channel(self, channel: Channel,
                    colours: list[tuple[int, int, int]]) -> None:
        """One frame to one channel, in 20-LED packets like OpenRGB sends."""
        count = len(colours)
        offset = 0
        while True:
            chunk = colours[offset:offset + LEDS_PER_PACKET]
            last = offset + len(chunk) >= count
            body = bytearray((0xEC, _MODE_DIRECT,
                              (0x80 if last else 0x00) | channel.direct_channel,
                              offset, len(chunk)))
            for r, g, b in chunk:
                body += bytes((r & 0xFF, g & 0xFF, b & 0xFF))
            self._dev.write(bytes(body).ljust(hidraw.REPORT_LEN, b"\x00"))
            offset += len(chunk)
            if last:
                return

    def close(self) -> None:
        self._dev.close()

    def __enter__(self) -> "AuraBoard":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
