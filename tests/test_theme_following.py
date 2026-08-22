"""Tests for LED colours that follow the Omarchy theme.

The feature is one idea in three places: `theme.led_colour` resolves a palette
role to RGB, `lightstate.TargetState.colour_role` records that a colour was
chosen as a role rather than as a value, and `mimarchy-ctl reload-theme`
re-resolves those roles when the theme changes. `mimarchy-lightd` is
deliberately not involved — it reads the resolved `colour` exactly as before,
which is what keeps the rendering path free of a new failure mode.

The brightness floor is the part most worth pinning. It was chosen from a
measurement (17 of 173 stock-theme colours fall below it), and the reason it is
a floor and not a scale is that scaling would rewrite the 156 that are already
fine. A change from one to the other would look innocuous in a diff and would
alter every theme on screen.
"""

from __future__ import annotations

import colorsys
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from mimarchy import ctl, lightstate, theme  # noqa: E402

# tokyo-night, trimmed to the roles this exercises.
V4 = """\
mode = "dark"
accent = "#7aa2f7"
selection = "#292e42"
muted = "#414868"
background = "#1a1b26"
foreground = "#a9b1d6"
bright_foreground = "#c0caf5"
red = "#f7768e"
yellow = "#e0af68"
green = "#9ece6a"
cyan = "#449dab"
blue = "#7aa2f7"
magenta = "#ad8ee6"
"""

V3 = """\
background = "#1a1b26"
foreground = "#a9b1d6"
color1 = "#f7768e"
color2 = "#9ece6a"
color3 = "#e0af68"
color4 = "#7aa2f7"
color5 = "#ad8ee6"
color6 = "#449dab"
"""


def write_theme(directory: Path, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "colors.toml").write_text(body)
    return directory


@pytest.fixture
def xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    theme.palette.cache_clear()
    yield tmp_path
    theme.palette.cache_clear()


def v4_dir(root: Path) -> Path:
    return root / "state" / "omarchy" / "current" / "theme"


def value_of(rgb) -> float:
    return colorsys.rgb_to_hsv(*[c / 255 for c in rgb])[2]


class TestLedColour:
    def test_resolves_v4_semantic_roles(self, xdg):
        write_theme(v4_dir(xdg), V4)

        assert theme.led_colour("accent") == (0x7A, 0xA2, 0xF7)
        assert theme.led_colour("green") == (0x9E, 0xCE, 0x6A)

    def test_resolves_v3_via_ansi_slots(self, xdg):
        write_theme(xdg / "config" / "omarchy" / "current" / "theme", V3)

        assert theme.led_colour("red") == (0xF7, 0x76, 0x8E)     # color1
        assert theme.led_colour("accent") == (0x7A, 0xA2, 0xF7)  # color4

    def test_unknown_role_is_refused(self, xdg):
        write_theme(v4_dir(xdg), V4)

        assert theme.led_colour("muted") is None       # dim by design
        assert theme.led_colour("background") is None  # near-black by design
        assert theme.led_colour("nonsense") is None

    def test_missing_role_is_none_not_a_substitute(self, xdg):
        """Three stock themes define no `orange`; none of them should invent one."""
        write_theme(v4_dir(xdg), V4)
        assert theme.led_colour("orange") is None

    def test_no_theme_yields_none(self, xdg):
        assert theme.led_colour("accent") is None


class TestBrightnessFloor:
    def test_bright_colours_pass_through_untouched(self, xdg):
        """The common case: ~90% of stock theme colours are already vivid."""
        write_theme(v4_dir(xdg), V4)

        for role in ("accent", "red", "yellow", "green", "magenta"):
            assert value_of(theme.led_colour(role)) >= theme.LED_VALUE_FLOOR
        assert theme.led_colour("accent") == (0x7A, 0xA2, 0xF7)

    def test_a_dim_colour_is_lifted_to_the_floor(self, xdg):
        write_theme(v4_dir(xdg), 'accent = "#001a00"\n')

        lifted = theme.led_colour("accent")
        assert value_of(lifted) == pytest.approx(theme.LED_VALUE_FLOOR, abs=0.01)

    def test_lifting_preserves_hue_and_saturation(self, xdg):
        """A dim blue must become a brighter blue, not a paler one."""
        write_theme(v4_dir(xdg), 'accent = "#001133"\n')
        original = colorsys.rgb_to_hsv(0x00 / 255, 0x11 / 255, 0x33 / 255)

        h, s, v = colorsys.rgb_to_hsv(*[c / 255 for c in theme.led_colour("accent")])
        assert h == pytest.approx(original[0], abs=0.01)
        assert s == pytest.approx(original[1], abs=0.01)
        assert v > original[2]

    def test_pure_black_is_left_alone(self, xdg):
        """Black has no hue to preserve; lifting it would invent one."""
        write_theme(v4_dir(xdg), 'accent = "#000000"\n')
        assert theme.led_colour("accent") == (0, 0, 0)


class TestStateRoundTrip:
    def test_role_survives_a_save_and_load(self, tmp_path, monkeypatch):
        monkeypatch.setattr(lightstate, "STATE_PATH", tmp_path / "s.json")
        monkeypatch.setattr(lightstate, "PERSIST_PATH", tmp_path / "p.json")

        state = lightstate.LightingState()
        target = state.for_target("cpu_fans")
        target.colour, target.colour_role = (1, 2, 3), "accent"
        lightstate.save(state)

        assert lightstate.load().for_target("cpu_fans").colour_role == "accent"

    def test_a_file_written_before_this_feature_reads_as_fixed(self, tmp_path,
                                                               monkeypatch):
        """Old state files have no `colour_role`, which means "a fixed colour"."""
        monkeypatch.setattr(lightstate, "STATE_PATH", tmp_path / "s.json")
        monkeypatch.setattr(lightstate, "PERSIST_PATH", tmp_path / "p.json")
        (tmp_path / "s.json").write_text(json.dumps({
            "linked": True,
            "targets": {"cpu_fans": {"effect": "static", "colour": [255, 0, 0],
                                     "speed": 1.0}},
        }))

        target = lightstate.load().for_target("cpu_fans")
        assert target.colour_role is None
        assert target.colour == (255, 0, 0)

    def test_the_resolved_colour_is_written_alongside_the_role(self, tmp_path,
                                                              monkeypatch):
        """lightd reads `colour` and knows nothing about themes; keep it valid."""
        monkeypatch.setattr(lightstate, "STATE_PATH", tmp_path / "s.json")
        monkeypatch.setattr(lightstate, "PERSIST_PATH", tmp_path / "p.json")

        state = lightstate.LightingState()
        target = state.for_target("cpu_fans")
        target.colour, target.colour_role = (0x7A, 0xA2, 0xF7), "accent"
        lightstate.save(state)

        raw = json.loads((tmp_path / "s.json").read_text())
        assert raw["targets"]["cpu_fans"]["colour"] == [0x7A, 0xA2, 0xF7]


@pytest.fixture
def ctl_env(tmp_path, monkeypatch):
    """`mimarchy-ctl` pointed at temp state, with no systemd and no sensors."""
    monkeypatch.setattr(lightstate, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(lightstate, "PERSIST_PATH", tmp_path / "persist.json")
    monkeypatch.setattr(ctl, "unit_active", lambda unit: False)
    monkeypatch.setattr(ctl, "snapshot", lambda: {})
    monkeypatch.setattr(ctl, "read_cpu_temp", lambda data=None: None)
    monkeypatch.setattr(ctl, "read_gpu_temp", lambda data=None: None)
    monkeypatch.setattr(ctl, "read_cpu_fan_rpm", lambda data=None: None)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state_home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config_home"))
    theme.palette.cache_clear()

    state = lightstate.LightingState()
    state.for_target("cpu_fans")
    state.for_target("gpu")
    lightstate.save(state)
    yield tmp_path
    theme.palette.cache_clear()


def theme_dir(root: Path) -> Path:
    return root / "state_home" / "omarchy" / "current" / "theme"


class TestColourCommand:
    def test_a_role_sets_both_the_value_and_the_role(self, ctl_env):
        write_theme(theme_dir(ctl_env), V4)

        assert ctl.main(["colour", "accent"]) == 0
        target = lightstate.load().for_target("cpu_fans")
        assert target.colour_role == "accent"
        assert target.colour == (0x7A, 0xA2, 0xF7)

    def test_a_hex_value_clears_the_role(self, ctl_env):
        write_theme(theme_dir(ctl_env), V4)
        ctl.main(["colour", "accent"])

        assert ctl.main(["colour", "#ff0044"]) == 0
        target = lightstate.load().for_target("cpu_fans")
        assert target.colour_role is None
        assert target.colour == (255, 0, 0x44)

    def test_hex_without_a_hash_is_accepted(self, ctl_env):
        assert ctl.main(["colour", "00ff00"]) == 0
        assert lightstate.load().for_target("cpu_fans").colour == (0, 255, 0)

    def test_nonsense_is_rejected_without_writing(self, ctl_env, capsys):
        before = lightstate.load().for_target("cpu_fans").colour

        assert ctl.main(["colour", "chartreuse"]) == 2
        assert lightstate.load().for_target("cpu_fans").colour == before
        assert "not a colour" in capsys.readouterr().err

    def test_a_role_the_theme_lacks_is_reported(self, ctl_env, capsys):
        write_theme(theme_dir(ctl_env), V4)   # defines no orange

        assert ctl.main(["colour", "orange"]) == 1
        assert "does not define" in capsys.readouterr().err

    def test_american_spelling_is_accepted(self, ctl_env):
        write_theme(theme_dir(ctl_env), V4)
        assert ctl.main(["color", "accent"]) == 0


class TestReloadTheme:
    def test_a_theme_switch_moves_a_following_colour(self, ctl_env):
        write_theme(theme_dir(ctl_env), V4)
        ctl.main(["colour", "accent"])
        assert lightstate.load().for_target("cpu_fans").colour == (0x7A, 0xA2, 0xF7)

        # catppuccin-latte's accent, i.e. the user switched themes.
        write_theme(theme_dir(ctl_env), 'accent = "#1e66f5"\nmuted = "#acb0be"\n')
        assert ctl.main(["reload-theme"]) == 0

        target = lightstate.load().for_target("cpu_fans")
        assert target.colour == (0x1E, 0x66, 0xF5)
        assert target.colour_role == "accent"      # still following

    def test_a_fixed_colour_is_never_touched(self, ctl_env):
        write_theme(theme_dir(ctl_env), V4)
        ctl.main(["colour", "#ff0044"])

        write_theme(theme_dir(ctl_env), 'accent = "#1e66f5"\n')
        ctl.main(["reload-theme"])

        assert lightstate.load().for_target("cpu_fans").colour == (255, 0, 0x44)

    def test_a_role_the_new_theme_lacks_keeps_the_last_colour(self, ctl_env):
        """Better a stale orange than a sudden black or white."""
        write_theme(theme_dir(ctl_env), V4 + 'orange = "#eb927b"\n')
        ctl.main(["colour", "orange"])

        write_theme(theme_dir(ctl_env), 'accent = "#1e66f5"\n')   # no orange
        assert ctl.main(["reload-theme"]) == 0

        target = lightstate.load().for_target("cpu_fans")
        assert target.colour == (0xEB, 0x92, 0x7B)
        assert target.colour_role == "orange"

    def test_no_theme_at_all_is_not_an_error(self, ctl_env):
        """The hook must not fail a theme switch on a half-installed machine."""
        assert ctl.main(["reload-theme"]) == 0

    def test_it_does_not_rewrite_the_file_when_nothing_moved(self, ctl_env):
        """lightd re-plans on mtime; a no-op write would churn it every switch."""
        write_theme(theme_dir(ctl_env), V4)
        ctl.main(["colour", "accent"])
        before = lightstate.STATE_PATH.stat().st_mtime_ns

        assert ctl.main(["reload-theme"]) == 0
        assert lightstate.STATE_PATH.stat().st_mtime_ns == before

    def test_status_reports_whether_a_target_follows_the_theme(self, ctl_env,
                                                               capsys):
        write_theme(theme_dir(ctl_env), V4)
        ctl.main(["colour", "accent"])

        ctl.main(["status", "--json"])
        payload = json.loads(capsys.readouterr().out)

        assert payload["targets"]["cpu_fans"]["follows_theme"] is True
        assert payload["targets"]["cpu_fans"]["colour_role"] == "accent"
