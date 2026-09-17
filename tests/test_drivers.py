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
from mimarchy import rgb as rgb_mod  # noqa: E402


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
    assert [(c.index, c.name, c.addressable, c.direct_channel, c.effect_channel)
            for c in board.channels] == [
        (0, "Aura Mainboard", False, 0x04, 0),
        (1, "Addressable RGB Header 1", True, 0, 1),
        (2, "Addressable RGB Header 2", True, 1, 2),
    ]
    # Config query first, Gen1 init once right after open, then one
    # Direct-entry packet per channel (OpenRGB's DeviceUpdateMode parity).
    assert fake.writes[0][:2] == bytes((0xEC, 0xB0))
    assert fake.writes[1][:5] == bytes((0xEC, 0x52, 0x53, 0x00, 0x01))
    assert [w[:6] for w in fake.writes[2:]] == [
        bytes((0xEC, 0x35, 0, 0x00, 0x00, 0xFF)),
        bytes((0xEC, 0x35, 1, 0x00, 0x00, 0xFF)),
        bytes((0xEC, 0x35, 2, 0x00, 0x00, 0xFF)),
    ]
    assert len(fake.writes) == 5
    assert all(len(w) == 65 for w in fake.writes)


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


def test_aura_direct_entry_uses_effect_channel_not_direct() -> None:
    """The onboard zone is effect 0 but direct 0x04, so headers must not use
    their direct channel in the 0x35 packet — that packet would park the wrong
    zone and leave the target ignoring frames."""
    fake = _FakeHid([_config_reply(addressable=1, onboard=5)])
    board = aura.AuraBoard(fake, "/dev/hidraw9")  # type: ignore[arg-type]
    _, header = board.channels
    assert (header.direct_channel, header.effect_channel) == (0, 1)
    fake.writes.clear()
    board.enter_direct(header)
    assert len(fake.writes) == 1
    assert fake.writes[0][:6] == bytes((0xEC, 0x35, 0x01, 0x00, 0x00, 0xFF))
    assert len(fake.writes[0]) == 65


def test_aura_no_onboard_keeps_effect_and_direct_aligned() -> None:
    """This rig's shape: no onboard zone, so header N is effect N and direct
    N. The packets still go out once per open, addressed by effect channel."""
    fake = _FakeHid([_config_reply(addressable=3, onboard=0)])
    board = aura.AuraBoard(fake, "/dev/hidraw9")  # type: ignore[arg-type]
    assert [(c.direct_channel, c.effect_channel) for c in board.channels] == [
        (0, 0), (1, 1), (2, 2)]
    assert [w[:6] for w in fake.writes[2:]] == [
        bytes((0xEC, 0x35, 0, 0x00, 0x00, 0xFF)),
        bytes((0xEC, 0x35, 1, 0x00, 0x00, 0xFF)),
        bytes((0xEC, 0x35, 2, 0x00, 0x00, 0xFF)),
    ]


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


def test_hid_device_writes_report_verbatim(tmp_path: Path, monkeypatch) -> None:
    """What `HidDevice.write` hands to the kernel is the whole 65-byte report.

    The regression this pins: `write` was lost in the hidraw rewrite while
    `aura` and `display` kept calling it, so the board was undetectable with
    an AttributeError instead of a permission hint. A fake fd keeps this off
    hardware.
    """
    import os

    written: list[bytes] = []
    monkeypatch.setattr(os, "open", lambda *a, **k: 99)
    monkeypatch.setattr(os, "write",
                        lambda fd, data: written.append(bytes(data)) or len(data))
    monkeypatch.setattr(os, "close", lambda fd: None)

    dev = hidraw.HidDevice("/dev/hidraw9")
    dev.write(aura._packet(0xEC, 0xB0))
    assert len(written) == 1
    assert len(written[0]) == 65
    assert written[0][:2] == bytes((0xEC, 0xB0))

class _FakeBus:
    """Records SMBus register writes; duck-typed stand-in for I2cBus."""

    def __init__(self) -> None:
        self.writes: list[tuple[int, int]] = []

    def write_byte_data(self, addr: int, register: int, value: int) -> None:
        self.writes.append((register, value))

    def close(self) -> None:
        pass


class _FakeNitro:
    """Duck-typed stand-in for NitroCard: canned nothing, recorded calls."""

    def __init__(self) -> None:
        self.bus = _FakeBus()
        self.calls: list[tuple] = []

    def set_external(self, enabled: bool) -> None:
        self.calls.append(("external", enabled))

    def set_mode(self, mode: int) -> None:
        self.calls.append(("mode", mode))

    def set_speed(self, register: int, value: int) -> None:
        self.calls.append(("speed", register, value))

    def set_colour(self, colour: tuple[int, int, int]) -> None:
        self.calls.append(("colour", tuple(colour)))

    def close(self) -> None:
        pass


def _nitro_only_controller(monkeypatch) -> tuple:
    """An RGBController with only the GPU zone, behind a fake Nitro card."""
    from mimarchy.config import Config, RGBZoneConfig

    fake = _FakeNitro()
    monkeypatch.setattr(nitro.NitroCard, "open",
                        classmethod(lambda cls, buses=None: fake))
    monkeypatch.setattr("mimarchy.rgb._aura.AuraBoard.open",
                        classmethod(lambda cls, *a, **k: (_ for _ in ()).throw(
                            hidraw.HidError("no board"))))
    config = Config(zones={"gpu": RGBZoneConfig(device="Sapphire Nitro Glow",
                                                zone=0)})
    import mimarchy.rgb as rgb_here
    controller = rgb_here.RGBController(config)
    assert isinstance(controller._nitro, _FakeNitro)
    return controller, fake


def test_rendered_colours_use_external_off_plus_custom(monkeypatch) -> None:
    """The bar only lights register colours with ext off + CUSTOM (0x06).

    Regression: the driver sent `ext=1` for rendered frames, which blanks
    the bar outright — filmed on this card, where every rendered effect
    showed a lit strip and a dark bar while the colour registers held live
    values. `ext=1` is what OpenRGB's External Control mode does, not its
    Static mode.
    """
    controller, fake = _nitro_only_controller(monkeypatch)
    controller.prepare_zone_for_direct_render("gpu")
    assert ("external", False) in fake.calls
    assert ("mode", nitro.MODE_CUSTOM) in fake.calls
    assert ("external", True) not in fake.calls


def test_write_frame_ensures_external_off_not_on(monkeypatch) -> None:
    """A frame must never re-blank the bar it is about to colour."""
    controller, fake = _nitro_only_controller(monkeypatch)
    controller.write_frame("gpu", [(255, 0, 0)])
    assert ("external", False) in fake.calls
    assert ("mode", nitro.MODE_CUSTOM) in fake.calls
    assert ("colour", (255, 0, 0)) in fake.calls
    assert ("external", True) not in fake.calls


def test_firmware_entry_clears_external_before_the_mode(monkeypatch) -> None:
    """Entering firmware leaves ext off + CUSTOM behind, never ext on."""
    controller, fake = _nitro_only_controller(monkeypatch)
    controller.prepare_zone_for_direct_render("gpu")
    fake.calls.clear()
    controller.set_mode("gpu", "rainbow", speed=11)
    ext_off = fake.calls.index(("external", False))
    modes = [i for i, c in enumerate(fake.calls) if c[0] == "mode"]
    assert modes and ext_off < modes[0]
    assert ("external", True) not in [
        c for c in fake.calls if c[0] == "external"
        and fake.calls.index(c) >= ext_off]
    assert fake.calls[modes[0]] == ("mode", nitro.MODE_RAINBOW)

def test_firmware_to_firmware_bounce_passes_through_off_plus_custom(
        monkeypatch) -> None:
    """A firmware effect entered from another firmware effect still lands.

    The card drops a direct firmware-to-firmware switch about half the time,
    so the driver bounces through external control first — and the bounce
    must leave ext off + CUSTOM behind, the combination rendered colours
    show in, never ext on (which blanks the bar).
    """
    controller, fake = _nitro_only_controller(monkeypatch)
    controller.prepare_zone_for_direct_render("gpu")
    fake.calls.clear()
    controller.set_mode("gpu", "rainbow", speed=11)
    fake.calls.clear()
    controller.set_mode("gpu", "chase", speed=5)
    assert fake.calls[0] == ("external", True)
    rest = fake.calls[1:]
    ext_off = rest.index(("external", False))
    custom = rest.index(("mode", nitro.MODE_CUSTOM))
    runway = rest.index(("mode", nitro.MODE_RUNWAY))
    assert ext_off < custom < runway
