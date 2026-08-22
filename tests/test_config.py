"""Reading `config.toml`, including the versions of it people actually write.

This file is documented as hand-editable and is full of comments inviting edits,
so malformed input is a normal operating condition rather than an exotic one —
and every consumer loads it: the daemon, the TUI, the CLI, and the bar widget on
every poll. A parse that raises therefore takes all four down at once, from one
typo, which is why most of what is pinned here is degradation.

Nothing touches `~/.config/mimarchy/config.toml`: every test passes an explicit
`tmp_path`.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mimarchy.config import DEFAULT_CONFIG, load_config  # noqa: E402


def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(body)
    return path


class TestMalformedZones:
    def test_a_zone_missing_its_fields_is_skipped_not_fatal(self, tmp_path,
                                                            capsys):
        """One typo used to raise KeyError out of every caller at once."""
        config = load_config(write(tmp_path, """
            [rgb.zones.good]
            device = "Board"
            zone = 0
            [rgb.zones.typo]
            devcie = "Card"
            zone = 0
        """))

        assert set(config.zones) == {"good"}
        assert "[rgb.zones.typo]" in capsys.readouterr().err

    def test_the_skip_is_announced(self, tmp_path, capsys):
        """A zone that quietly stops existing looks exactly like hardware that
        has stopped being detected, which is a far longer thing to debug."""
        load_config(write(tmp_path, '[rgb.zones.a]\ndevice = "Board"\n'))

        err = capsys.readouterr().err
        assert "device" in err and "zone" in err

    def test_a_non_numeric_zone_index_is_skipped(self, tmp_path, capsys):
        config = load_config(write(
            tmp_path, '[rgb.zones.a]\ndevice = "Board"\nzone = "first"\n'))
        capsys.readouterr()

        assert config.zones == {}

    def test_a_zone_key_that_is_not_a_table_is_skipped(self, tmp_path, capsys):
        """`[rgb.zones]` followed by `a = 1` parses fine and means nothing."""
        config = load_config(write(tmp_path, "[rgb.zones]\na = 1\n"))
        capsys.readouterr()

        assert config.zones == {}

    def test_one_bad_zone_does_not_cost_the_rest_of_the_file(self, tmp_path,
                                                             capsys):
        config = load_config(write(tmp_path, """
            [rgb]
            zone_size = 42
            [rgb.zones.broken]
            zone = 0
            [ui]
            link_cpu_gpu = false
        """))
        capsys.readouterr()

        assert config.zone_size == 42
        assert config.link_cpu_gpu is False


class TestZoneLengths:
    def test_a_zone_without_its_own_length_takes_the_global_one(self, tmp_path):
        config = load_config(write(
            tmp_path,
            '[rgb]\nzone_size = 30\n[rgb.zones.a]\ndevice = "x"\nzone = 0\n'))

        assert config.leds_for("a") == 30

    def test_a_zone_can_override_it(self, tmp_path):
        """Two strips of different lengths is the ordinary case once there is
        more than one, and one global number truncates one of them."""
        config = load_config(write(tmp_path, """
            [rgb]
            zone_size = 30
            [rgb.zones.a]
            device = "x"
            zone = 0
            leds = 60
        """))

        assert config.leds_for("a") == 60

    def test_an_unknown_zone_still_answers(self, tmp_path):
        """`leds_for` is called per detected zone, and detection can find one
        the config never mentioned."""
        config = load_config(write(tmp_path, "[rgb]\nzone_size = 8\n"))

        assert config.leds_for("nobody") == 8


class TestDetectorList:
    def test_it_is_absent_unless_the_file_sets_one(self, tmp_path):
        """Absent means "work it out from the device names"; the restrict tool
        depends on being able to tell that from an explicit empty list."""
        assert load_config(write(tmp_path, "[rgb]\n")).detectors == []

    def test_it_is_read_verbatim(self, tmp_path):
        config = load_config(write(
            tmp_path, '[rgb]\ndetectors = ["ASUS Aura Core", "Other"]\n'))

        assert config.detectors == ["ASUS Aura Core", "Other"]


class TestTheShippedDefault:
    def test_it_is_written_when_there_is_none(self, tmp_path):
        path = tmp_path / "sub" / "config.toml"
        config = load_config(path)

        assert path.exists()
        assert set(config.zones) == {"cpu_fans", "gpu"}

    def test_it_parses_as_what_it_documents(self, tmp_path):
        """The default file is the worked example every other config is copied
        from, so a comment that has drifted from the value under it is worse
        than no comment."""
        raw = tomllib.loads(DEFAULT_CONFIG)

        assert raw["rgb"]["zone_size"] == 15
        assert raw["display"]["vendor_id"] == 0x5131
        assert set(raw["rgb"]["zones"]) == {"cpu_fans", "gpu"}
        # Spelled out rather than derived, because "Sapphire" names every
        # Sapphire card OpenRGB knows — fine for finding a device, a freeze
        # risk for picking detectors.
        assert len(raw["rgb"]["detectors"]) == 4

    def test_the_link_toggle_rewrites_one_line_and_keeps_the_comments(
            self, tmp_path):
        """The whole reason `save_link_state` is a line edit rather than a TOML
        dump: a round-trip through a writer strips every explanation in here."""
        path = write(tmp_path, DEFAULT_CONFIG)
        config = load_config(path)

        config.save_link_state(False, path)

        assert load_config(path).link_cpu_gpu is False
        assert "rainbow shows a quarter of the hue wheel" in path.read_text()
