"""User-editable device identifiers and OpenRGB zone mapping (fallback path)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

_CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))

CONFIG_PATH = _CONFIG_HOME / "mimarchy" / "config.toml"

DEFAULT_CONFIG = """\
# Mimarchy config.

[rgb]
# How many LEDs each addressable zone is resized to. Set this to your strip's
# real length: addressable zones report leds=0 until told, and a zero-length zone
# silently swallows colour writes.
#
# Getting it wrong is visible either way. Too short leaves the tail dark; too long
# is worse than it sounds, because spatial effects span the *zone* — at 60 on a
# 15-LED strip, rainbow shows a quarter of the hue wheel and looks like spectrum.
zone_size = 15

# The zones to drive. `device` is matched as a case-insensitive substring of the
# OpenRGB device name, so it survives minor naming changes; `zone` is the index
# within that device. Run `openrgb --list-devices` to see yours.
#
# Only list zones you have something plugged into — driving an empty header does
# nothing but costs a write every frame.
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


def load_config(path: Path = CONFIG_PATH) -> Config:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_CONFIG)

    with path.open("rb") as f:
        raw = tomllib.load(f)

    rgb_raw = raw.get("rgb", {})
    zones = {
        name: RGBZoneConfig(device=z["device"], zone=int(z["zone"]))
        for name, z in rgb_raw.get("zones", {}).items()
    }
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
    )
