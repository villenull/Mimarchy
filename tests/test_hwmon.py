"""Tests for the telemetry reader's behaviour when there is no telemetry.

lm-sensors is optional: the lighting is the point of this tool and temperatures
sit beside it, so a machine without the package is a supported configuration.
That path had no coverage, and the reader raised on it — every consumer is
written to expect `None`, but `_read_sensors_json` never gave them the chance.

These deliberately exercise the real function with only `subprocess.run`
replaced. Stubbing `read_cpu_temp` instead, which is what the TUI and ctl tests
do for determinism, is exactly what hid the bug: the crash lived below the seam
those tests mock.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from mimarchy import hwmon  # noqa: E402

SENSORS = {
    "k10temp-pci-00c3": {"Tctl": {"temp1_input": 52.2}},
    "amdgpu-pci-0300": {"fan1": {"fan1_input": 1200},
                        "edge": {"temp1_input": 40.0}},
    "nct6687-isa-0a20": {"CPU Fan": {"fan1_input": 768}},
}


def fake_sensors(monkeypatch, *, raises=None, stdout=""):
    def run(*args, **kwargs):
        if raises is not None:
            raise raises
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=stdout)
    monkeypatch.setattr(hwmon.subprocess, "run", run)


class TestSensorsMissing:
    """Each of these raised before, on every TUI repaint and widget poll."""

    @pytest.mark.parametrize("failure", [
        pytest.param(FileNotFoundError(2, "No such file or directory", "sensors"),
                     id="lm-sensors-not-installed"),
        pytest.param(PermissionError(13, "Permission denied", "sensors"),
                     id="not-executable"),
        pytest.param(subprocess.CalledProcessError(1, "sensors"),
                     id="sensors-exits-nonzero"),
    ])
    def test_reader_returns_empty_rather_than_raising(self, monkeypatch, failure):
        fake_sensors(monkeypatch, raises=failure)
        assert hwmon._read_sensors_json() == {}

    def test_unparseable_output_returns_empty(self, monkeypatch):
        fake_sensors(monkeypatch, stdout="sensors: command not found\n")
        assert hwmon._read_sensors_json() == {}

    @pytest.mark.parametrize("reader", [
        "read_cpu_temp", "read_gpu_temp", "read_cpu_fan_rpm",
    ])
    def test_every_reading_degrades_to_none(self, monkeypatch, reader):
        fake_sensors(monkeypatch,
                     raises=FileNotFoundError(2, "No such file", "sensors"))
        assert getattr(hwmon, reader)() is None

    def test_list_readers_degrade_to_empty(self, monkeypatch):
        fake_sensors(monkeypatch,
                     raises=FileNotFoundError(2, "No such file", "sensors"))
        assert hwmon.read_fans() == []
        assert hwmon.read_temps() == []


class TestSensorsPresent:
    """The failure path must not have been bought by breaking the normal one."""

    def test_readings_are_still_parsed(self, monkeypatch):
        fake_sensors(monkeypatch, stdout=json.dumps(SENSORS))

        assert hwmon.read_cpu_temp() == 52.2
        assert hwmon.read_gpu_temp() == 40.0
        assert hwmon.read_cpu_fan_rpm() == 768

    def test_implausible_fan_rpm_is_rejected(self, monkeypatch):
        """Unpopulated headers report tens of thousands of RPM, not zero."""
        fake_sensors(monkeypatch, stdout=json.dumps(
            {"nct6687-isa-0a20": {"CPU Fan": {"fan1_input": 65535}}}))

        assert hwmon.read_fans() == []
        assert hwmon.read_cpu_fan_rpm() is None

    def test_integrated_gpu_without_a_fan_is_skipped(self, monkeypatch):
        """Two amdgpu chips are present; only the discrete one has a fan."""
        fake_sensors(monkeypatch, stdout=json.dumps({
            "amdgpu-pci-1000": {"edge": {"temp1_input": 99.0}},          # iGPU
            "amdgpu-pci-0300": {"fan1": {"fan1_input": 1200},
                                "edge": {"temp1_input": 40.0}},          # discrete
        }))

        assert hwmon.read_gpu_temp() == 40.0
