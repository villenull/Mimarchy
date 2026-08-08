"""CPU cooler (Balam Rush Heliux Pro HEX75) built-in display.

Protocol, reverse-engineered from a USBPcap capture of the vendor app
("PC Monitor", `Enfriamiento de torre software.exe`), captured under Windows:

    device : USB 5131:2007, HID, vendor-defined 64-byte in/out pipe
    frame  : 64 bytes written to the interrupt OUT endpoint, ~1 Hz

    offset 0   0x40      constant frame header (64 == frame length)
    offset 1   0x00-0xff CPU temperature, °C      <- rendered by the panel
    offset 2   0x00-0xff CPU load, %              (sent by the vendor app,
    offset 9   0x00-0xff GPU temperature, °C       but not shown on the panel)
    offset 5-6 uint16 BE fan/pump RPM             <- rendered by the panel
    all other bytes zero

There is no on/off command and no handshake. The display lights up because
frames arrive and blanks on a firmware timeout a while after they stop, so
"turning it on" means starting the stream — which is why this module exposes a
run loop rather than a toggle. No off opcode exists: byte 0 was swept across
plausible values, every other byte was driven at 0x01 and 0xff, and a USB
device reset was tried; none blank the panel on demand.

The capture alone could not pin the field layout down — the machine running the
vendor app had no sensors, so every value byte was zero. Offsets were resolved by
driving known values at the real hardware and reading the panel back through a
webcam.

The panel renders RPM to the nearest 100 (it draws the last two digits as a
fixed "00"), so 1284 displays as 1200. That truncation initially made a
big-endian pair look like a little-endian one; (5,6) = (9,100) -> 2404 -> 2400
is what distinguished them.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import hid

from mimarchy.config import DisplayConfig

FRAME_LEN = 64
FRAME_HEADER = 0x40


class ProtocolUnknownError(RuntimeError):
    """The display's vendor/product id is not configured."""

OFF_HEADER = 0
OFF_CPU_TEMP = 1
OFF_CPU_LOAD = 2
OFF_RPM = 5  # uint16, big-endian, spans offsets 5-6
OFF_GPU_TEMP = 9

#: The vendor app sends one frame per second. The panel's own blank timeout is
#: **50.35 s**, filmed and reproduced across two 5-trial runs at sigma 0.02 and
#: 0.04 — so one second is enormously conservative. An earlier comment here said
#: "a few seconds", which was an assumption, never measured, and wrong by more
#: than an order of magnitude.
#:
#: Worth knowing if this is ever re-measured: a third run gave 48.27 s at sigma
#: 0.03 and never reproduced. Within-run sigma said nothing about run-to-run
#: agreement here, so a tight sigma is not evidence the number is settled.
#:
#: Sending faster does not blank it sooner, so there is no shorter interval worth
#: choosing here.
DEFAULT_INTERVAL = 1.0


@dataclass
class DisplayFrame:
    cpu_temp: int = 0
    cpu_load: int = 0
    gpu_temp: int = 0
    rpm: int = 0

    def encode(self) -> bytes:
        buf = bytearray(FRAME_LEN)
        buf[OFF_HEADER] = FRAME_HEADER
        buf[OFF_CPU_TEMP] = _clamp(self.cpu_temp)
        buf[OFF_CPU_LOAD] = _clamp(self.cpu_load)
        buf[OFF_GPU_TEMP] = _clamp(self.gpu_temp)
        rpm = max(0, min(0xFFFF, int(round(self.rpm))))
        buf[OFF_RPM] = rpm >> 8
        buf[OFF_RPM + 1] = rpm & 0xFF
        return bytes(buf)


def _clamp(value: float) -> int:
    return max(0, min(255, int(round(value))))


class CPUDisplay:
    """Writes frames to the cooler's display over hidraw."""

    def __init__(self, config: DisplayConfig):
        self._config = config
        self._device: hid.Device | None = None

    def _require_configured(self) -> None:
        if not self._config.known:
            raise ProtocolUnknownError(
                "Cooler display vendor_id/product_id not set in config.toml."
            )

    def open(self) -> None:
        self._require_configured()
        if self._device is None:
            self._device = hid.Device(
                self._config.vendor_id, self._config.product_id
            )

    def close(self) -> None:
        if self._device is not None:
            self._device.close()
            self._device = None

    def send(self, frame: DisplayFrame) -> None:
        self.open()
        assert self._device is not None
        # hidapi prepends a report ID byte; this device uses no numbered
        # reports, so it must be 0 and is not part of the 64-byte frame.
        self._device.write(b"\x00" + frame.encode())

    def __enter__(self) -> "CPUDisplay":
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def stream(
    display: CPUDisplay,
    read_frame,
    interval: float = DEFAULT_INTERVAL,
    stop=None,
) -> None:
    """Push frames until `stop()` returns True (or forever).

    `read_frame` is a zero-arg callable returning a DisplayFrame, so the
    telemetry source stays injectable and testable.

    A failing frame doesn't end the stream. Telemetry comes from a `sensors`
    subprocess, and on shutdown SIGTERM reaches that child first — so the read
    raises, and without this the daemon died with a traceback and systemd
    recorded a *clean stop* as `failed`. Transient sensor errors are likewise
    not worth dropping the display for.
    """
    with display:
        while not (stop and stop()):
            try:
                display.send(read_frame())
            except Exception:  # noqa: BLE001 — see above; keep streaming
                if stop and stop():
                    return
            time.sleep(interval)
