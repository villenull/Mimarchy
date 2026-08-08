"""Read-only temperature/fan telemetry via `sensors -j` (lm-sensors)."""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass

# nct6687d reports a sentinel-ish garbage value (tens of thousands of RPM)
# for unpopulated/idle Super I/O fan headers instead of 0 — no real case or
# CPU fan exceeds this, so treat anything above it as "not connected".
MAX_PLAUSIBLE_RPM = 10_000


@dataclass
class FanReading:
    chip: str
    label: str
    rpm: float


@dataclass
class TempReading:
    chip: str
    label: str
    celsius: float


def _read_sensors_json() -> dict:
    out = subprocess.run(
        ["sensors", "-j"], capture_output=True, text=True, check=True
    ).stdout
    return json.loads(out)


def read_fans() -> list[FanReading]:
    readings = []
    for chip, entries in _read_sensors_json().items():
        for label, values in entries.items():
            if not isinstance(values, dict):
                continue
            for key, val in values.items():
                if key.endswith("_input") and "fan" in key and val <= MAX_PLAUSIBLE_RPM:
                    readings.append(FanReading(chip=chip, label=label, rpm=val))
    return readings


def read_temps() -> list[TempReading]:
    readings = []
    for chip, entries in _read_sensors_json().items():
        for label, values in entries.items():
            if not isinstance(values, dict):
                continue
            for key, val in values.items():
                if key.endswith("_input") and "temp" in key:
                    readings.append(TempReading(chip=chip, label=label, celsius=val))
    return readings


def read_cpu_temp() -> float | None:
    """CPU package temperature from k10temp's Tctl.

    Deliberately not nct6687's "CPU" reading: that driver reports implausible
    near-zero values on this board (see README), while k10temp reads the CPU's
    own sensor and is trustworthy.
    """
    for t in read_temps():
        if t.chip.startswith("k10temp") and t.label == "Tctl":
            return t.celsius
    return None


def read_gpu_temp() -> float | None:
    """Discrete GPU edge temperature.

    Two amdgpu chips are present (discrete card and the CPU's integrated
    graphics); the discrete one is distinguished by exposing a fan.
    """
    data = _read_sensors_json()
    for chip, entries in data.items():
        if not chip.startswith("amdgpu"):
            continue
        if not any(k.startswith("fan") for k in entries):
            continue  # integrated GPU — no fan
        edge = entries.get("edge", {})
        for key, val in edge.items():
            if key.endswith("_input"):
                return val
    return None


def read_cpu_fan_rpm() -> float | None:
    """The cooler's fan speed, for the panel's RPM readout.

    Prefers nct6687's "CPU Fan" over "Pump Fan": on this board the latter
    reports an implausible ~255 while the former tracks the cooler's fans.
    """
    fans = read_fans()
    for preferred in ("CPU Fan", "Pump Fan"):
        for f in fans:
            if f.chip.startswith("nct6687") and f.label == preferred and f.rpm > 0:
                return f.rpm
    return None


def read_cpu_load() -> int:
    """Whole-CPU utilisation percentage, sampled over a short interval."""
    def _busy_total() -> tuple[int, int]:
        with open("/proc/stat") as f:
            parts = [int(x) for x in f.readline().split()[1:]]
        idle = parts[3] + parts[4]  # idle + iowait
        return sum(parts) - idle, sum(parts)

    busy1, total1 = _busy_total()
    time.sleep(0.2)
    busy2, total2 = _busy_total()
    delta_total = total2 - total1
    if delta_total <= 0:
        return 0
    return max(0, min(100, round((busy2 - busy1) * 100 / delta_total)))
