"""Sapphire Nitro Glow V3 (RX 9070 XT Nitro+), driven over raw I2C.

Ports the register map and mode values from OpenRGB's
`SapphireNitroGlowV3Controller` (`SapphireNitroGlowV3Controller.h/.cpp`,
GPL-2.0-or-later) — the whole protocol is SMBus byte reads and writes to one
address, so there is nothing else to port:

* presence: single-byte read of the address answers.
* mode/colours/speeds: register write of one byte each.
* firmware effects: write the speed register, then the mode register —
  exactly the order `RGBController_SapphireNitroGlowV3::DeviceUpdateMode`
  uses (speed first, mode second), because the mode latch reads the speed at
  entry time.

The card exposes one controllable LED for a bar with many physical segments,
so per-LED rendering is one colour (`SetColor` writes R/G/B registers) and
spatial effects go to firmware (rainbow/runway/spectrum), which is the
routing `lightd.plan` already implements. Rendered colours show only with
external control *off* and mode `CUSTOM` (0x06) — filmed on this card:
`ext=1` blanks the bar whatever the colour registers hold, while
`ext=0` + `CUSTOM` lights it in the register colour. That is also what
OpenRGB's `DeviceUpdateMode` does for its Static mode (`SetExternalControl`
(false) then `SetMode(CUSTOM)`), and what its External Control mode undoes
(`SetExternalControl(true)`, no mode write). Two quirks preserved from the
OpenRGB-backed driver, both measured against this card:

* entering a firmware effect via an external-control bounce (`External`
  Control on first, 0.4 s settle, then off + mode) lands reliably; a direct
  firmware-to-firmware switch was dropped about half the time.
* leaving a firmware effect for direct rendering needs the off + CUSTOM
  pair twice — a single write was dropped 5 of 5 times.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from mimarchy import smbus

ADDR = 0x28

REG_MODE = 0x10
REG_EXTERNAL = 0x0F
REG_RUNWAY_SPEED = 0x11
REG_RUNWAY_REPEAT = 0x12
REG_SPECTRUM_SPEED = 0x13
REG_RAINBOW_SPEED = 0x15
REG_SERIAL_SPEED = 0x16
REG_RED = 0x1A
REG_GREEN = 0x1B
REG_BLUE = 0x1C

MODE_RAINBOW = 0x00
MODE_RUNWAY = 0x01
MODE_SPECTRUM = 0x02
MODE_SERIAL = 0x03
MODE_BLUE = 0x04
MODE_AUDIO = 0x05
MODE_CUSTOM = 0x06
MODE_OFF = 0x07

MODE_FOR_EFFECT = {
    "rainbow": (MODE_RAINBOW, REG_RAINBOW_SPEED, 10, 250),
    "chase": (MODE_RUNWAY, REG_RUNWAY_SPEED, 5, 50),
    "spectrum": (MODE_SPECTRUM, REG_SPECTRUM_SPEED, 30, 1),
}

SPEED_ANCHORS = {
    "rainbow": ((10, 0.61), (250, 10.63)),
    "chase": ((5, 1.26), (50, 10.83)),
}


@dataclass
class I2cNode:
    bus: int
    addr: int
    path: str


def _bus_name(bus: int, sysfs: str) -> str:
    try:
        with open(os.path.join(sysfs, f"i2c-{bus}", "name")) as handle:
            return handle.read().strip()
    except OSError:
        return ""


def candidate_buses(sysfs: str = "/sys/bus/i2c/devices") -> list[int]:
    """GPU I2C buses worth asking — AMD display buses, never the SMBus.

    Narrow by name, not by number: bus numbers move between boots, and the
    PIIX4 SMBus adapters on this board are exactly the buses whose broad
    probing froze the machine (OpenRGB #4888). Display (`AMDGPU DM`) buses
    belong to the card; the OEM bus is the one that answered here.
    """
    try:
        entries = sorted(os.listdir(sysfs))
    except OSError:
        return []
    buses: list[int] = []
    for entry in entries:
        if not entry.startswith("i2c-") or "-" in entry[4:]:
            continue
        try:
            bus = int(entry[4:])
        except ValueError:
            continue
        name = _bus_name(bus, sysfs)
        if "AMDGPU" in name and ("OEM" in name or "DM i2c" in name):
            buses.append(bus)
    return buses


def find(buses: list[int] | None = None) -> I2cNode | None:
    """The first GPU bus whose controller answers, or None.

    One single-byte read per bus — the same probe OpenRGB's
    `TestForSapphireGPUController` runs, and the same one `i2cdetect -r 0x28`
    does by hand. Returns the node so the caller opens exactly that bus.
    """
    for bus in candidate_buses() if buses is None else buses:
        node = I2cNode(bus, ADDR, f"/dev/i2c-{bus}")
        try:
            with smbus.I2cBus(bus) as handle:
                handle.read_byte(ADDR)
        except smbus.I2cError:
            continue
        return node
    return None


class NitroCard:
    def __init__(self, bus: smbus.I2cBus, node: I2cNode):
        self._bus = bus
        self.node = node

    @classmethod
    def open(cls, buses: list[int] | None = None) -> "NitroCard":
        node = find(buses)
        if node is None:
            raise smbus.I2cError(
                "no Sapphire Nitro Glow controller answered at 0x28 on any "
                "AMDGPU bus. If the bar went dark suddenly, a cold boot "
                "(PSU off ~30 s) resets its microcontroller."
            )
        return cls(smbus.I2cBus(node.bus), node)

    def set_external(self, enabled: bool) -> None:
        self._bus.write_byte_data(ADDR, REG_EXTERNAL, 0x01 if enabled else 0x00)

    def set_mode(self, mode: int) -> None:
        self._bus.write_byte_data(ADDR, REG_MODE, mode)

    def set_speed(self, register: int, value: int) -> None:
        self._bus.write_byte_data(ADDR, register, value)

    def set_colour(self, colour: tuple[int, int, int]) -> None:
        r, g, b = colour
        self._bus.write_byte_data(ADDR, REG_RED, r)
        self._bus.write_byte_data(ADDR, REG_GREEN, g)
        self._bus.write_byte_data(ADDR, REG_BLUE, b)

    def close(self) -> None:
        self._bus.close()

    def __enter__(self) -> "NitroCard":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
