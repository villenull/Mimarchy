"""Tests for the stdlib-only lighting drivers (hidraw/smbus/aura/nitro).

Live hardware is deliberately absent here — CI has no Sapphire card and the
Aura hidraw node is root-only until the udev rule lands — so everything below
runs against fake sysfs trees and a fake HidDevice. The one live check
(Nitro find() -> bus 7, mode/ext register reads) was demonstrated read-only
on the dev machine at write time, not encoded as a test.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from mimarchy import aura, hidraw, nitro  # noqa: E402


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_hid_id_parses_vendor_product(tmp_path: Path) -> None:
    uevent = tmp_path / "uevent"
    uevent.write_text(
        "DRIVER=hid-generic\n"
        "HID_ID=0003:00000B05:000019AF\n"
        "HID_NAME=AsusTek Computer Inc. AURA LED Controller\n"
    )
    assert hidraw._hid_id(str(tmp_path)) == (0x0B05, 0x19AF)
    assert hidraw._hid_id(str(tmp_path / "missing")) == (None, None)


def test_find_against_fake_sysfs_tree(tmp_path: Path) -> None:
    sysfs = tmp_path / "hid"
    match = sysfs / "0003:0B05:19AF.0001"
    other = sysfs / "0003:046D:C52B.0002"
    _write(match / "uevent", "HID_ID=0003:00000B05:000019AF\n")
    _write(other / "uevent", "HID_ID=0003:0000046D:0000C52B\n")
    (match / "hidraw" / "hidraw9").mkdir(parents=True)
    (other / "hidraw" / "hidraw8").mkdir(parents=True)
    found = hidraw.find(0x0B05, 0x19AF, str(sysfs))
    assert [(n.vendor_id, n.product_id, n.path) for n in found] == [
        (0x0B05, 0x19AF, "/dev/hidraw9")
    ]


def test_packet_is_65_bytes_starting_with_ec() -> None:
    packet = aura._packet(0xEC, 0xB0)
    assert len(packet) == 65
    assert packet[0] == 0xEC
    assert packet[1] == 0xB0
    with pytest.raises(ValueError):
        aura._packet(*([0] * 66))


class _FakeHid:
    """Duck-typed stand-in for HidDevice: canned reads, recorded writes."""

    def __init__(self, reads: list[bytes]):
        self._reads = list(reads)
        self.writes: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.writes.append(bytes(data))

    def read(self, size: int = 65, timeout: float = 1.0) -> bytes:
        return self._reads.pop(0)

    def close(self) -> None:
        pass


def _config_reply(*, addressable: int, onboard: int) -> bytes:
    table = bytearray(60)
    table[0x02] = addressable
    table[0x1B] = onboard
    return bytes(bytearray((0xEC, 0x30, 0x00, 0x00)) + table + bytes(1))


def test_aura_table_parse_and_gen1_init() -> None:
    fake = _FakeHid([_config_reply(addressable=2, onboard=5)])
    board = aura.AuraBoard(fake, "/dev/hidraw9")  # type: ignore[arg-type]
    assert [(c.index, c.name, c.addressable, c.direct_channel) for c in board.channels] == [
        (0, "Aura Mainboard", False, 0x04),
        (1, "Addressable RGB Header 1", True, 0),
        (2, "Addressable RGB Header 2", True, 1),
    ]
    # Config query first, Gen1 init once right after open.
    assert fake.writes[0][:2] == bytes((0xEC, 0xB0))
    assert fake.writes[1][:5] == bytes((0xEC, 0x52, 0x53, 0x00, 0x01))
    assert len(fake.writes) == 2


def test_aura_set_channel_chunks_45_colours() -> None:
    fake = _FakeHid([_config_reply(addressable=1, onboard=0)])
    board = aura.AuraBoard(fake, "/dev/hidraw9")  # type: ignore[arg-type]
    (channel,) = board.channels
    assert channel.direct_channel == 0
    colours = [(i % 256, (i + 1) % 256, (i + 2) % 256) for i in range(45)]
    fake.writes.clear()
    board.set_channel(channel, colours)
    assert len(fake.writes) == 3
    offsets_counts_flags = []
    for packet in fake.writes:
        assert len(packet) == 65
        assert packet[0] == 0xEC and packet[1] == 0x40
        offsets_counts_flags.append((packet[3], packet[4], packet[2]))
    assert offsets_counts_flags == [(0, 20, 0x00), (20, 20, 0x00), (40, 5, 0x80)]
    # First LED of the last chunk lands at the right byte offset.
    assert tuple(fake.writes[2][5:8]) == colours[40]


def test_aura_rejects_bad_table_answer() -> None:
    fake = _FakeHid([bytes(65)])
    with pytest.raises(hidraw.HidError):
        aura.AuraBoard(fake, "/dev/hidraw9")  # type: ignore[arg-type]


def test_nitro_candidate_buses_filters_by_name(tmp_path: Path) -> None:
    names = {
        "i2c-3": "AMDGPU DM i2c hw bus 1",
        "i2c-7": "AMDGPU DM i2c OEM bus",
        "i2c-8": "AMDGPU DM aux hw bus 0",
        "i2c-1": "AMDGPU SMU 0",
        "i2c-0": "Synopsys DesignWare I2C adapter",
        "i2c-15": "SMBus PIIX4 adapter port 0 at 0b00",
        "i2c-ITE8853:00": "ITE8853:00",
    }
    for entry, name in names.items():
        _write(tmp_path / entry / "name", name + "\n")
    assert nitro.candidate_buses(str(tmp_path)) == [3, 7]


def test_nitro_registers_match_openrgb_header() -> None:
    # Values from SapphireNitroGlowV3Controller.h (GPL-2.0-or-later).
    assert (nitro.REG_EXTERNAL, nitro.REG_MODE) == (0x0F, 0x10)
    assert (nitro.REG_RUNWAY_SPEED, nitro.REG_RUNWAY_REPEAT) == (0x11, 0x12)
    assert nitro.REG_SPECTRUM_SPEED == 0x13
    assert (nitro.REG_RAINBOW_SPEED, nitro.REG_SERIAL_SPEED) == (0x15, 0x16)
    assert (nitro.REG_RED, nitro.REG_GREEN, nitro.REG_BLUE) == (0x1A, 0x1B, 0x1C)
    assert (
        nitro.MODE_RAINBOW,
        nitro.MODE_RUNWAY,
        nitro.MODE_SPECTRUM,
        nitro.MODE_SERIAL,
        nitro.MODE_BLUE,
        nitro.MODE_AUDIO,
        nitro.MODE_CUSTOM,
        nitro.MODE_OFF,
    ) == (0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07)
    assert nitro.ADDR == 0x28


def test_aura_offsets_match_openrgb() -> None:
    # AuraMain.cpp: config_table[0x02] addressable, [0x1B] onboard, [0x1D] 12V.
    assert (aura._OFF_ADDRESSABLE, aura._OFF_ONBOARD, aura._OFF_HEADERS_12V) == (
        0x02,
        0x1B,
        0x1D,
    )
    assert aura.LEDS_PER_PACKET == 0x14  # AsusAuraUSBController.h
    assert aura.MAX_ADDRESSABLE == 120  # RGBAura.h AURA_ADDRESSABLE_MAX_LEDS
