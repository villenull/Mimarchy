"""The daemon notices a configured zone that OpenRGB did not produce.

Before this, `lightd` took whatever device list it saw at startup as the
truth for the rest of its life and never said a word about a zone that was
not in it. A graphics card whose LED controller had dropped off its I2C bus
therefore went unnoticed for a week: the state file still said `gpu:
rainbow`, the unit was green, and the card was dark.

Three things are pinned here. The startup check names exactly the configured
zones that are missing, in config order. The grace window keeps re-asking
OpenRGB — because its SDK listener opens before detection has finished, an
early connect can miss a slow controller that turns up seconds later — but
gives up after a bounded time and runs with what it found. And the report
written for `mimarchy-ctl status` says which zones were and were not found.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mimarchy import detection  # noqa: E402
from mimarchy.config import Config, RGBZoneConfig  # noqa: E402
from mimarchy.lightd import (describe_missing, missing_zones,  # noqa: E402
                             report_detection, settle_zones)
from mimarchy.rgb import ZoneInfo  # noqa: E402


def _config() -> Config:
    return Config(zones={
        "cpu_fans": RGBZoneConfig(device="ASUS PRIME X870-P WIFI", zone=0, leds=15),
        "gpu": RGBZoneConfig(device="Sapphire Radeon RX 9070 XT Nitro+", zone=0),
    })


BOARD = ZoneInfo(key="cpu_fans", label="cpu fans",
                 device_name="ASUS PRIME X870-P WIFI", led_count=15)
CARD = ZoneInfo(key="gpu", label="gpu",
                device_name="Sapphire Radeon RX 9070 XT Nitro+", led_count=1)


class FakeRGB:
    def __init__(self, zones):
        self._zones = list(zones)

    def list_zones(self):
        return self._zones


class FakeClock:
    """A clock and a sleep that agree with each other, so a test can run the
    whole grace window in no time and still count the looks."""

    def __init__(self):
        self.now = 100.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_missing_zones_are_named_in_config_order() -> None:
    assert missing_zones(_config(), {"cpu_fans": 15}) == ["gpu"]
    assert missing_zones(_config(), {}) == ["cpu_fans", "gpu"]
    assert missing_zones(_config(), {"cpu_fans": 15, "gpu": 1}) == []


def test_the_message_says_what_was_looked_for() -> None:
    """The journal line has to be enough to act on without reading the code:
    the zone, the device string it was matched by, and where to look next."""
    text = describe_missing(_config(), ["gpu"])
    assert "'gpu'" in text
    assert "Sapphire Radeon RX 9070 XT Nitro+" in text
    assert "mimarchy-setup --list" in text


def test_a_late_controller_is_picked_up_within_the_grace_window() -> None:
    """OpenRGB answered before its detection pass had reached the card."""
    looks = iter([FakeRGB([BOARD]), FakeRGB([BOARD]), FakeRGB([BOARD, CARD])])
    clock = FakeClock()
    rgb, zones, missing = settle_zones(_config(), lambda: next(looks),
                                       grace=20.0, poll=4.0,
                                       clock=clock, sleep=clock.sleep)
    assert missing == []
    assert zones == {"cpu_fans": 15, "gpu": 1}
    assert clock.sleeps == [4.0, 4.0]


def test_a_zone_that_never_appears_is_given_up_on_after_the_window() -> None:
    """Bounded: a card that has genuinely gone must not hold the board's
    lighting hostage forever. The daemon settles for what it found and says
    what it did not."""
    clock = FakeClock()
    connects = 0

    def connect():
        nonlocal connects
        connects += 1
        return FakeRGB([BOARD])

    rgb, zones, missing = settle_zones(_config(), connect, grace=20.0, poll=4.0,
                                       clock=clock, sleep=clock.sleep)
    assert missing == ["gpu"]
    assert zones == {"cpu_fans": 15}
    assert connects == 6                       # t = 0, 4, 8, 12, 16, 20
    assert clock.now == 120.0                  # and not a second longer


def test_nothing_missing_means_no_waiting_at_all() -> None:
    clock = FakeClock()
    rgb, zones, missing = settle_zones(_config(), lambda: FakeRGB([BOARD, CARD]),
                                       clock=clock, sleep=clock.sleep)
    assert missing == []
    assert clock.sleeps == []


def test_the_report_names_found_and_missing_zones(tmp_path) -> None:
    report = report_detection(FakeRGB([BOARD]), _config(), {"cpu_fans": 15})
    assert report.zones["cpu_fans"].detected
    assert report.zones["cpu_fans"].device_name == "ASUS PRIME X870-P WIFI"
    assert not report.zones["gpu"].detected
    assert report.zones["gpu"].configured_device == "Sapphire Radeon RX 9070 XT Nitro+"
    assert report.missing == ["gpu"]

    # Round-trips through the file `mimarchy-ctl status` reads.
    path = tmp_path / "detection.json"
    detection.save(report, path)
    back = detection.load(path)
    assert back is not None
    assert back.missing == ["gpu"]
    assert back.zones["cpu_fans"].led_count == 15
    assert back.checked_at > 0


def test_no_report_reads_as_unknown_not_as_fine(tmp_path) -> None:
    assert detection.load(tmp_path / "never-written.json") is None
