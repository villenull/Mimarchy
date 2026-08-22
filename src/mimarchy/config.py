"""User-editable device identifiers and OpenRGB zone mapping (fallback path)."""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

_CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))

CONFIG_PATH = _CONFIG_HOME / "mimarchy" / "config.toml"

DEFAULT_CONFIG = """\
# Mimarchy config. Hand-editable, and meant to be read as well as edited — the
# comments are why each value is what it is.
#
# `mimarchy-setup` writes this file for you from what OpenRGB actually detects,
# which is easier than guessing device names. `mimarchy-setup --list` prints the
# devices and zones without changing anything.

[rgb]
# Default length for addressable zones that do not set their own `leds` below.
# Set this to your strip's real length: addressable zones report leds=0 until
# told, and a zero-length zone silently swallows colour writes.
#
# Getting it wrong is visible either way. Too short leaves the tail dark; too long
# is worse than it sounds, because spatial effects span the *zone* — at 60 on a
# 15-LED strip, rainbow shows a quarter of the hue wheel and looks like spectrum.
zone_size = 15

# Which OpenRGB detectors `tools/restrict-openrgb-detectors.py` may enable.
# Optional: without it the tool works the list out from the device names below.
# It is spelled out here because "Sapphire" is a fine way to *find* one card and
# a hopeless way to *pick* a detector — it names every Sapphire card OpenRGB
# knows, which would mean dozens of I2C probes, which is the freeze this whole
# dance exists to avoid (OpenRGB #4888).
#
# These four are this machine's; `mimarchy-setup` replaces them with yours.
detectors = [
    "ASUS Aura Addressable",
    "ASUS Aura Core",
    "ASUS Aura Motherboard",
    "Sapphire Radeon RX 9070 XT Nitro+",
]

# The zones to drive. `device` is matched as a case-insensitive substring of the
# OpenRGB device name, so it survives minor naming changes; `zone` is the index
# within that device. Run `openrgb --list-devices` to see yours.
#
# Add as many as you have: the daemon renders every zone listed here, and the
# names are yours to choose. Only list zones you have something plugged into —
# driving an empty header does nothing but costs a write every frame.
#
# `leds` overrides `zone_size` for one zone, which is what two strips of
# different lengths need.
[rgb.zones.cpu_fans]
device = "PRIME X870-P"
zone = 0

[rgb.zones.gpu]
device = "Sapphire"
zone = 0

[ui]
# CPU and GPU lighting move together by default; `u` in the TUI splits them so
# each can run its own mode. Persisted so the choice survives a restart.
link_cpu_gpu = true

[display]
# CPU cooler display controller. usb.ids mislabels this as an "MSR-101U magnetic
# card reader" — the ID is cloned and also used by USB relay boards. Needs
# udev/99-mimarchy.rules installed, or its hidraw node is root-only.
vendor_id = 0x5131
product_id = 0x2007
"""


@dataclass
class RGBZoneConfig:
    device: str
    zone: int
    #: This zone's strip length, or None to take `[rgb] zone_size`. Per zone
    #: because two strips of different lengths are the ordinary case once there
    #: is more than one, and a single global length silently truncates one of
    #: them or stretches an effect across slots that drive nothing.
    leds: int | None = None


@dataclass
class DisplayConfig:
    vendor_id: int = 0
    product_id: int = 0

    @property
    def known(self) -> bool:
        return bool(self.vendor_id and self.product_id)


@dataclass
class Config:
    zones: dict[str, RGBZoneConfig] = field(default_factory=dict)
    display: DisplayConfig = field(default_factory=DisplayConfig)
    zone_size: int = 15
    link_cpu_gpu: bool = True
    #: An explicit OpenRGB detector allowlist, empty unless the file sets one.
    #: See `mimarchy.detectors` for why a device name is not enough on its own.
    detectors: list[str] = field(default_factory=list)

    def leds_for(self, key: str) -> int:
        """The strip length one zone is resized to."""
        zone = self.zones.get(key)
        return (zone.leds if zone and zone.leds else self.zone_size)

    def save_link_state(self, linked: bool, path: Path | None = None) -> None:
        """Persist just the link toggle, leaving the rest of the file alone.

        A line rewrite rather than a TOML dump: the config is hand-editable and
        full of comments explaining why each value is what it is, and a
        round-trip through `tomllib` + a writer would strip all of them.
        """
        path = path or CONFIG_PATH
        self.link_cpu_gpu = linked
        try:
            lines = path.read_text().splitlines()
        except OSError:
            return
        for i, line in enumerate(lines):
            if line.strip().startswith("link_cpu_gpu"):
                lines[i] = f"link_cpu_gpu = {str(linked).lower()}"
                path.write_text("\n".join(lines) + "\n")
                return


def _zones(raw: dict) -> dict[str, RGBZoneConfig]:
    """Every well-formed zone table, with the rest skipped and reported.

    A zone table missing `device` or `zone` used to raise, and this config is
    documented as hand-editable — so one typo took down the daemon, the TUI, the
    bar widget's every poll and the CLI, all at once, and none of them said why.
    One bad zone now costs that zone and nothing else.

    Loud on stderr rather than silent: a zone that quietly stops existing looks
    exactly like hardware that has stopped being detected, which is a much
    longer thing to debug than a typo.
    """
    zones: dict[str, RGBZoneConfig] = {}
    for name, table in raw.items():
        try:
            leds = table.get("leds")
            zones[name] = RGBZoneConfig(
                device=str(table["device"]),
                zone=int(table["zone"]),
                leds=int(leds) if leds is not None else None,
            )
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            print(f"mimarchy: ignoring [rgb.zones.{name}] — needs a `device` "
                  f"name and a `zone` index ({exc})", file=sys.stderr)
    return zones


def load_config(path: Path = CONFIG_PATH) -> Config:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_CONFIG)

    with path.open("rb") as f:
        raw = tomllib.load(f)

    rgb_raw = raw.get("rgb", {})
    zones = _zones(rgb_raw.get("zones", {}))
    display_raw = raw.get("display", {})
    display = DisplayConfig(
        vendor_id=int(display_raw.get("vendor_id", 0)),
        product_id=int(display_raw.get("product_id", 0)),
    )
    return Config(
        zones=zones,
        display=display,
        zone_size=int(rgb_raw.get("zone_size", 15)),
        link_cpu_gpu=bool(raw.get("ui", {}).get("link_cpu_gpu", True)),
        detectors=[str(name) for name in rgb_raw.get("detectors", [])],
    )
