"""Tests for `mimarchy-ctl`, the command the bar widget drives everything through.

This is where the widget's behaviour is actually pinned down. The QML calls one
command per interaction and parses one JSON document, so a mistake here shows up
as a wrong bar rather than a stack trace — and the QML itself cannot be tested
from Python at all. Everything with a decision in it therefore lives on this
side of the boundary, and gets held down here.

No test touches the real state file, the real config, or systemd: `lightstate`
is pointed at a temp directory and the unit calls are stubbed, so a run cannot
disturb the user's lighting or start a service.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from mimarchy import ctl, lightstate  # noqa: E402
from mimarchy.effects import SPEED_LEVELS  # noqa: E402


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Temp state, no systemd, deterministic sensors."""
    monkeypatch.setattr(lightstate, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(lightstate, "PERSIST_PATH", tmp_path / "persist.json")
    monkeypatch.setattr(ctl, "unit_active", lambda unit: False)
    monkeypatch.setattr(ctl, "set_unit", lambda unit, running: None)
    # The readers take an optional pre-read snapshot so `status` spawns
    # `sensors` once rather than three times; the stubs accept it and ignore it.
    monkeypatch.setattr(ctl, "snapshot", lambda: {})
    monkeypatch.setattr(ctl, "read_cpu_temp", lambda data=None: 52.2)
    monkeypatch.setattr(ctl, "read_gpu_temp", lambda data=None: 40.0)
    monkeypatch.setattr(ctl, "read_cpu_fan_rpm", lambda data=None: 768.0)
    monkeypatch.setattr(ctl, "load_config", _NoConfig)
    yield


class _NoConfig:
    """Stands in for Config so nothing reads or rewrites the user's file."""

    zones = {"cpu_fans": None, "gpu": None}

    def __init__(self, *a, **kw):
        pass

    def save_link_state(self, linked: bool, path=None) -> None:
        pass


def seed(**targets) -> None:
    """Write a state file with the given per-target effect/speed."""
    state = lightstate.LightingState()
    for key, (effect, speed) in targets.items():
        target = state.for_target(key)
        target.effect, target.speed = effect, speed
    lightstate.save(state)


def status(capsys) -> dict:
    assert ctl.main(["status", "--json"]) == 0
    return json.loads(capsys.readouterr().out)


class TestStatus:
    def test_json_carries_what_the_widget_paints(self, capsys):
        seed(cpu_fans=("rainbow", 0.6))
        payload = status(capsys)

        assert payload["targets"]["cpu_fans"]["effect"] == "rainbow"
        assert payload["linked"] is True
        assert payload["sensors"]["cpu_temp"] == 52.2
        assert payload["display_active"] is False
        assert payload["lighting_active"] is False

    def test_speed_is_reported_as_a_readable_stop(self, capsys):
        seed(cpu_fans=("rainbow", SPEED_LEVELS[2]))
        payload = status(capsys)

        assert payload["targets"]["cpu_fans"]["speed_stop"] == 3
        assert payload["speed_stops"] == len(SPEED_LEVELS)

    def test_capability_flags_are_declared_not_inferred(self, capsys):
        """The widget must not have to know which effects take a colour."""
        seed(a=("static", 1.0), b=("rainbow", 1.0))
        targets = status(capsys)["targets"]

        assert targets["a"]["takes_colour"] is True
        assert targets["a"]["takes_speed"] is False
        assert targets["b"]["takes_colour"] is False
        assert targets["b"]["takes_speed"] is True

    def test_survives_a_state_file_that_does_not_exist(self, capsys):
        """First run from a fresh install: seed targets rather than emit none."""
        payload = status(capsys)
        assert set(payload["targets"]) == {"cpu_fans", "gpu"}

    def test_json_is_a_single_line(self, capsys):
        """QML parses one document; stray formatting is a parse failure there."""
        seed(cpu_fans=("static", 1.0))
        ctl.main(["status", "--json"])
        out = capsys.readouterr().out

        assert out.count("\n") == 1
        json.loads(out)

    def test_sensors_are_read_once_per_invocation(self, monkeypatch, capsys):
        """The widget polls this every 2s from inside the user's shell process.

        Three readings called bare meant three `sensors -j` spawns per poll.
        Pinned because the saving is invisible — nothing looks wrong when it
        regresses, it just costs 150 processes a minute instead of 50.
        """
        spawns = []
        monkeypatch.setattr(ctl, "snapshot",
                            lambda: (spawns.append(1), {})[1])
        monkeypatch.setattr(ctl, "read_cpu_temp", lambda data=None: 52.2)
        monkeypatch.setattr(ctl, "read_gpu_temp", lambda data=None: 40.0)
        monkeypatch.setattr(ctl, "read_cpu_fan_rpm", lambda data=None: 768.0)

        seed(cpu_fans=("static", 1.0))
        ctl.main(["status", "--json"])
        capsys.readouterr()

        assert len(spawns) == 1

    def test_human_output_is_not_json(self, capsys):
        seed(cpu_fans=("rainbow", 0.6))
        assert ctl.main(["status"]) == 0
        out = capsys.readouterr().out

        assert "rainbow" in out
        with pytest.raises(json.JSONDecodeError):
            json.loads(out)


class TestSpeed:
    def test_up_and_down_walk_the_ladder(self):
        seed(cpu_fans=("rainbow", SPEED_LEVELS[2]))

        ctl.main(["speed", "+"])
        assert lightstate.load().for_target("cpu_fans").speed == SPEED_LEVELS[3]

        ctl.main(["speed", "-"])
        assert lightstate.load().for_target("cpu_fans").speed == SPEED_LEVELS[2]

    def test_clamps_at_both_ends(self):
        seed(cpu_fans=("rainbow", SPEED_LEVELS[-1]))
        ctl.main(["speed", "+"])
        assert lightstate.load().for_target("cpu_fans").speed == SPEED_LEVELS[-1]

        seed(cpu_fans=("rainbow", SPEED_LEVELS[0]))
        ctl.main(["speed", "-"])
        assert lightstate.load().for_target("cpu_fans").speed == SPEED_LEVELS[0]

    def test_a_speedless_effect_is_left_alone_and_is_not_an_error(self, capsys):
        """Scrolling the bar icon cannot know what effect is running."""
        seed(cpu_fans=("static", 1.0))

        assert ctl.main(["speed", "+"]) == 0
        assert lightstate.load().for_target("cpu_fans").speed == 1.0
        assert "no speed change" in capsys.readouterr().err

    def test_every_target_moves_together(self):
        seed(cpu_fans=("rainbow", SPEED_LEVELS[0]), gpu=("chase", SPEED_LEVELS[0]))
        ctl.main(["speed", "+"])

        state = lightstate.load()
        assert state.for_target("cpu_fans").speed == SPEED_LEVELS[1]
        assert state.for_target("gpu").speed == SPEED_LEVELS[1]

    def test_unsnapped_stored_speed_still_steps(self):
        """State written under the old six-stop ladder holds off-ladder values."""
        seed(cpu_fans=("rainbow", 3.0))
        ctl.main(["speed", "-"])

        speed = lightstate.load().for_target("cpu_fans").speed
        assert speed in SPEED_LEVELS

    def test_up_and_down_are_spelled_two_ways(self):
        seed(cpu_fans=("rainbow", SPEED_LEVELS[1]))
        ctl.main(["speed", "up"])
        assert lightstate.load().for_target("cpu_fans").speed == SPEED_LEVELS[2]
        ctl.main(["speed", "down"])
        assert lightstate.load().for_target("cpu_fans").speed == SPEED_LEVELS[1]

    def test_no_zone_still_moves_every_target(self):
        """Regression guard: omitting --zone must keep today's meaning."""
        seed(cpu_fans=("rainbow", SPEED_LEVELS[0]), gpu=("chase", SPEED_LEVELS[0]))
        assert ctl.main(["speed", "+"]) == 0

        state = lightstate.load()
        assert state.for_target("cpu_fans").speed == SPEED_LEVELS[1]
        assert state.for_target("gpu").speed == SPEED_LEVELS[1]

    def test_zone_scopes_to_one_target(self):
        seed(cpu_fans=("rainbow", SPEED_LEVELS[0]), gpu=("chase", SPEED_LEVELS[0]))
        assert ctl.main(["speed", "+", "--zone", "cpu_fans"]) == 0

        state = lightstate.load()
        assert state.for_target("cpu_fans").speed == SPEED_LEVELS[1]
        assert state.for_target("gpu").speed == SPEED_LEVELS[0]

    def test_unknown_zone_is_rejected_without_writing(self, capsys):
        seed(cpu_fans=("rainbow", SPEED_LEVELS[0]), gpu=("chase", SPEED_LEVELS[0]))

        assert ctl.main(["speed", "+", "--zone", "nope"]) != 0

        state = lightstate.load()
        assert state.for_target("cpu_fans").speed == SPEED_LEVELS[0]
        assert state.for_target("gpu").speed == SPEED_LEVELS[0]
        assert "unknown zone" in capsys.readouterr().err

    def test_set_moves_to_an_absolute_stop(self):
        seed(cpu_fans=("rainbow", SPEED_LEVELS[0]), gpu=("chase", SPEED_LEVELS[0]))
        assert ctl.main(["speed", "set", "4"]) == 0

        state = lightstate.load()
        assert state.for_target("cpu_fans").speed == SPEED_LEVELS[3]
        assert state.for_target("gpu").speed == SPEED_LEVELS[3]

    def test_set_with_a_zone_touches_only_that_target(self):
        seed(cpu_fans=("rainbow", SPEED_LEVELS[0]), gpu=("chase", SPEED_LEVELS[0]))
        assert ctl.main(["speed", "set", "4", "--zone", "cpu_fans"]) == 0

        state = lightstate.load()
        assert state.for_target("cpu_fans").speed == SPEED_LEVELS[3]
        assert state.for_target("gpu").speed == SPEED_LEVELS[0]

    def test_set_on_a_static_zone_is_not_an_error(self, capsys):
        """Mirrors the relative form: a static/off zone has no speed to set."""
        seed(cpu_fans=("static", 1.0))

        assert ctl.main(["speed", "set", "5", "--zone", "cpu_fans"]) == 0
        assert lightstate.load().for_target("cpu_fans").speed == 1.0
        assert "no speed change" in capsys.readouterr().err

    def test_set_on_a_static_zone_without_a_zone_option_is_also_not_an_error(
            self, capsys):
        seed(cpu_fans=("static", 1.0))

        assert ctl.main(["speed", "set", "5"]) == 0
        assert lightstate.load().for_target("cpu_fans").speed == 1.0
        assert "no speed change" in capsys.readouterr().err


class TestEffect:
    def test_sets_every_target(self):
        seed(cpu_fans=("static", 1.0), gpu=("static", 1.0))
        assert ctl.main(["effect", "spectrum"]) == 0

        state = lightstate.load()
        assert state.for_target("cpu_fans").effect == "spectrum"
        assert state.for_target("gpu").effect == "spectrum"

    def test_unknown_effect_is_rejected_without_writing(self, capsys):
        seed(cpu_fans=("static", 1.0))
        assert ctl.main(["effect", "disco"]) == 2

        assert lightstate.load().for_target("cpu_fans").effect == "static"
        assert "unknown effect" in capsys.readouterr().err

    def test_no_zone_still_sets_every_target(self):
        """Regression guard: omitting --zone must keep today's meaning."""
        seed(cpu_fans=("static", 1.0), gpu=("static", 1.0))
        assert ctl.main(["effect", "spectrum"]) == 0

        state = lightstate.load()
        assert state.for_target("cpu_fans").effect == "spectrum"
        assert state.for_target("gpu").effect == "spectrum"

    def test_zone_scopes_to_one_target(self):
        seed(cpu_fans=("static", 1.0), gpu=("static", 1.0))
        assert ctl.main(["effect", "spectrum", "--zone", "cpu_fans"]) == 0

        state = lightstate.load()
        assert state.for_target("cpu_fans").effect == "spectrum"
        assert state.for_target("gpu").effect == "static"

    def test_unknown_zone_is_rejected_without_writing(self, capsys):
        seed(cpu_fans=("static", 1.0), gpu=("static", 1.0))
        assert ctl.main(["effect", "spectrum", "--zone", "nope"]) != 0

        state = lightstate.load()
        assert state.for_target("cpu_fans").effect == "static"
        assert state.for_target("gpu").effect == "static"
        assert "unknown zone" in capsys.readouterr().err


class TestColour:
    def test_no_zone_still_sets_every_target(self):
        """Regression guard: omitting --zone must keep today's meaning."""
        seed(cpu_fans=("static", 1.0), gpu=("static", 1.0))
        assert ctl.main(["colour", "#ff0044"]) == 0

        state = lightstate.load()
        assert state.for_target("cpu_fans").colour == (255, 0, 0x44)
        assert state.for_target("gpu").colour == (255, 0, 0x44)

    def test_zone_scopes_to_one_target(self):
        seed(cpu_fans=("static", 1.0), gpu=("static", 1.0))
        assert ctl.main(["colour", "#ff0044", "--zone", "cpu_fans"]) == 0

        state = lightstate.load()
        assert state.for_target("cpu_fans").colour == (255, 0, 0x44)
        assert state.for_target("gpu").colour == (255, 255, 255)

    def test_unknown_zone_is_rejected_without_writing(self, capsys):
        seed(cpu_fans=("static", 1.0), gpu=("static", 1.0))
        assert ctl.main(["colour", "#ff0044", "--zone", "nope"]) != 0

        state = lightstate.load()
        assert state.for_target("cpu_fans").colour == (255, 255, 255)
        assert state.for_target("gpu").colour == (255, 255, 255)
        assert "unknown zone" in capsys.readouterr().err


class TestDisplay:
    def test_toggle_starts_it_when_stopped(self, monkeypatch):
        calls = []
        monkeypatch.setattr(ctl, "unit_active", lambda unit: False)
        monkeypatch.setattr(ctl, "set_unit",
                            lambda unit, running: calls.append((unit, running)))

        assert ctl.main(["display", "toggle"]) == 0
        assert calls == [(ctl.DISPLAY_UNIT, True)]

    def test_toggle_stops_it_when_running(self, monkeypatch):
        calls = []
        monkeypatch.setattr(ctl, "unit_active", lambda unit: True)
        monkeypatch.setattr(ctl, "set_unit",
                            lambda unit, running: calls.append((unit, running)))

        assert ctl.main(["display", "toggle"]) == 0
        assert calls == [(ctl.DISPLAY_UNIT, False)]

    def test_setting_it_to_what_it_already_is_touches_nothing(self, monkeypatch):
        """`display on` from a script must not restart a running stream."""
        calls = []
        monkeypatch.setattr(ctl, "unit_active", lambda unit: True)
        monkeypatch.setattr(ctl, "set_unit",
                            lambda unit, running: calls.append((unit, running)))

        assert ctl.main(["display", "on"]) == 0
        assert calls == []

    def test_a_systemd_failure_is_reported(self, monkeypatch, capsys):
        monkeypatch.setattr(ctl, "unit_active", lambda unit: False)
        monkeypatch.setattr(ctl, "set_unit", lambda unit, running: "Unit not found.")

        assert ctl.main(["display", "on"]) == 1
        assert "Unit not found." in capsys.readouterr().err


class TestLink:
    def test_toggle_flips_and_persists(self):
        seed(cpu_fans=("static", 1.0))
        assert lightstate.load().linked is True

        ctl.main(["link", "toggle"])
        assert lightstate.load().linked is False

        ctl.main(["link", "toggle"])
        assert lightstate.load().linked is True

    def test_explicit_states_are_idempotent(self):
        seed(cpu_fans=("static", 1.0))
        ctl.main(["link", "off"])
        ctl.main(["link", "off"])
        assert lightstate.load().linked is False


class TestWriteDiscipline:
    def test_state_round_trips_through_the_atomic_writer(self):
        """The widget and the TUI must never see a half-written file.

        Asserted by going through the real save/load rather than by mocking it:
        the guarantee comes from lightstate's write-then-rename, and this is what
        would notice if a command started writing the file some other way.
        """
        seed(cpu_fans=("rainbow", SPEED_LEVELS[1]))
        ctl.main(["speed", "+"])

        text = lightstate.STATE_PATH.read_text()
        assert json.loads(text)["targets"]["cpu_fans"]["speed"] == SPEED_LEVELS[2]
        assert not list(lightstate.STATE_PATH.parent.glob("*.tmp"))

    def test_a_read_only_command_writes_nothing(self, capsys):
        seed(cpu_fans=("rainbow", SPEED_LEVELS[1]))
        before = lightstate.STATE_PATH.read_text()

        ctl.main(["status", "--json"])
        capsys.readouterr()

        assert lightstate.STATE_PATH.read_text() == before


def test_every_subcommand_is_reachable():
    """A subparser with no `func` default is a crash, not a usage message."""
    parser = ctl.build_parser()
    for command in ("status", "speed", "effect", "display", "link"):
        args = parser.parse_args({
            "status": ["status"],
            "speed": ["speed", "+"],
            "effect": ["effect", "static"],
            "display": ["display", "toggle"],
            "link": ["link", "toggle"],
        }[command])
        assert callable(args.func)
