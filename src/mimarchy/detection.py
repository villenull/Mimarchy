"""What the lighting daemon found when it last started, for `mimarchy-ctl`.

The daemon is the only process that ever asks OpenRGB which devices exist, and
until this file existed it kept the answer to itself. `mimarchy-ctl status`
read the *desired* state file and asked systemd whether the unit was active,
so a configured zone whose device had vanished from OpenRGB — a graphics card
whose LED controller dropped off its I2C bus, in the case that prompted this —
showed up as `gpu: rainbow` and `lighting: running`, both true and neither
useful. The incident took a week to notice because nothing said "gpu: not
detected" anywhere a person looks.

So the daemon now writes down, once per startup, which configured zones it
found and which it did not, and `status` reports that alongside the rest. Kept
in the runtime directory like the lighting state: it describes this login's
OpenRGB, and a reboot re-detects everything anyway.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

_RUNTIME_DIR = Path(
    os.environ.get("XDG_RUNTIME_DIR")
    or os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
)

DETECTION_PATH = _RUNTIME_DIR / "mimarchy-detection.json"


@dataclass
class ZoneDetection:
    #: The `device` substring from config.toml — what the user asked for.
    configured_device: str
    #: The OpenRGB device name it matched, or None when nothing did.
    device_name: str | None = None
    led_count: int = 0

    @property
    def detected(self) -> bool:
        return self.device_name is not None


@dataclass
class Detection:
    zones: dict[str, ZoneDetection] = field(default_factory=dict)
    #: When the daemon settled on this answer, as `time.time()`.
    checked_at: float = 0.0

    @property
    def missing(self) -> list[str]:
        return [key for key, zone in self.zones.items() if not zone.detected]

    def to_json(self) -> str:
        return json.dumps(
            {"checked_at": self.checked_at,
             "zones": {k: asdict(v) for k, v in self.zones.items()}},
            indent=2,
        )

    @classmethod
    def from_json(cls, text: str) -> "Detection":
        raw = json.loads(text)
        return cls(
            checked_at=float(raw.get("checked_at", 0.0)),
            zones={
                k: ZoneDetection(
                    configured_device=str(v.get("configured_device", "")),
                    device_name=v.get("device_name") or None,
                    led_count=int(v.get("led_count", 0)),
                )
                for k, v in raw.get("zones", {}).items()
            },
        )


def save(detection: Detection, path: Path | None = None) -> None:
    """Write-then-rename, like the lighting state, for the same reason: the
    bar polls `status` every two seconds and must never read half a file."""
    path = path or DETECTION_PATH
    if not detection.checked_at:
        detection.checked_at = time.time()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(detection.to_json())
        tmp.replace(path)
    except OSError:
        # Reporting is best-effort; a daemon that cannot write its report
        # should still light the LEDs.
        pass


def load(path: Path | None = None) -> Detection | None:
    """The last report, or None when the daemon has not started this login."""
    path = path or DETECTION_PATH
    try:
        return Detection.from_json(path.read_text())
    except (OSError, ValueError):
        return None
