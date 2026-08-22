"""Tests for `mimarchy-setup`, the wizard that makes this run on other people's rigs.

Everything here runs against a fake OpenRGB client and a `tmp_path` config, so a
run cannot reach the real server, the real `~/.config/mimarchy/config.toml`, or
any hardware. The fake is shaped like the real SDK objects rather than like the
code's expectations — `zone.leds` is a list because that is how length is
reported, `device.data.zones` carries `leds_min`/`leds_max` because that is where
resizability actually lives — since a fake built from the code's assumptions
cannot catch a wrong assumption.

The interactive half is driven by handing `main()` a callable in place of
`input`, which is also why the prompts are asked through one, rather than by
calling `input` directly.
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402
from openrgb.utils import ZoneType  # noqa: E402

from mimarchy import setup  # noqa: E402
from mimarchy.config import load_config  # noqa: E402
from mimarchy.rgb import RGBError  # noqa: E402


class FakeZone:
    def __init__(self, name, leds, zone_type=ZoneType.LINEAR):
        self.name = name
        self.leds = [None] * leds
        self.type = zone_type


class FakeZoneData:
    def __init__(self, leds_min, leds_max):
        self.leds_min, self.leds_max = leds_min, leds_max


class FakeDeviceType:
    def __init__(self, name):
        self.name = name


class FakeDevice:
    def __init__(self, name, kind, zones, zone_data=None):
        self.name = name
        self.type = FakeDeviceType(kind)
        self.zones = zones
        if zone_data is not None:
            self.data = type("Data", (), {"zones": zone_data})()


class FakeClient:
    def __init__(self, devices):
        self.devices = devices


def board_and_card() -> FakeClient:
    """The development rig's shape: an addressable header, and a one-LED card."""
    return FakeClient([
        FakeDevice("ASUS PRIME X870-P WIFI", "MOTHERBOARD",
                   [FakeZone("Addressable 1", 0), FakeZone("Aura Core", 1)],
                   zone_data=[FakeZoneData(0, 120), FakeZoneData(1, 1)]),
        FakeDevice("Sapphire Radeon RX 9070 XT Nitro+", "GPU",
                   [FakeZone("GPU Zone", 1, ZoneType.SINGLE)],
                   zone_data=[FakeZoneData(1, 1)]),
    ])


@pytest.fixture(autouse=True)
def no_real_server(monkeypatch):
    """Nothing in this file may open a socket."""
    def refuse(*a, **kw):
        raise AssertionError("a test tried to reach the real OpenRGB server")
    monkeypatch.setattr(setup, "connect", refuse)


def serve(monkeypatch, client) -> None:
    monkeypatch.setattr(setup, "connect", lambda *a, **kw: client)


def scripted(answers):
    """An `input` stand-in that replays answers and then behaves like ctrl-d."""
    remaining = list(answers)

    def ask(_prompt: str) -> str:
        if not remaining:
            raise EOFError
        return remaining.pop(0)
    return ask


def openrgb_config(tmp_path: Path, names) -> Path:
    path = tmp_path / "OpenRGB.json"
    path.write_text(json.dumps(
        {"Detectors": {"detectors": {name: True for name in names}}}))
    return path


class TestListing:
    def test_list_prints_devices_zones_and_coordinates(self, monkeypatch, capsys):
        serve(monkeypatch, board_and_card())
        assert setup.main(["--list"]) == 0
        out = capsys.readouterr().out

        assert "ASUS PRIME X870-P WIFI" in out
        assert "(motherboard)" in out
        # The coordinate is the thing the wizard asks for, so it has to be
        # printed rather than left to be counted off the indentation.
        assert "0.0" in out and "1.0" in out
        assert "'GPU Zone'" in out

    def test_list_distinguishes_addressable_from_fixed(self, monkeypatch, capsys):
        serve(monkeypatch, board_and_card())
        setup.main(["--list"])
        lines = capsys.readouterr().out.splitlines()

        header = next(line for line in lines if "Addressable 1" in line)
        gpu = next(line for line in lines if "GPU Zone" in line)
        assert "addressable" in header
        assert "fixed" in gpu

    def test_a_zero_length_strip_is_explained_rather_than_reported(
            self, monkeypatch, capsys):
        """`leds=0` is the single most alarming thing in this listing."""
        serve(monkeypatch, board_and_card())
        setup.main(["--list"])

        assert "normal" in capsys.readouterr().out

    def test_list_writes_nothing(self, monkeypatch, tmp_path, capsys):
        serve(monkeypatch, board_and_card())
        config = tmp_path / "config.toml"

        setup.main(["--list", "--config", str(config)])
        capsys.readouterr()

        assert not config.exists()

    def test_list_needs_no_terminal(self, monkeypatch, capsys):
        """It is the half of this command that belongs in a pipe or a bug report."""
        serve(monkeypatch, board_and_card())
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

        assert setup.main(["--list"]) == 0
        capsys.readouterr()


class TestDegradation:
    def test_no_server_is_a_message_and_not_a_traceback(self, monkeypatch, capsys):
        def refuse(*a, **kw):
            raise RGBError("Can't reach the OpenRGB server on 127.0.0.1:6742 "
                           "— is it running? Try: systemctl --user start "
                           "openrgb.service")
        monkeypatch.setattr(setup, "connect", refuse)

        assert setup.main(["--list"]) == 1
        err = capsys.readouterr().err
        assert "systemctl --user start openrgb.service" in err

    def test_no_devices_blames_the_detector_list(self, monkeypatch, capsys):
        """The likely cause, and the one nobody guesses.

        `install.sh` narrows detectors before the server first starts, so a
        machine whose hardware was never in that narrow set sees nothing — and
        "check your cables" would send the user in exactly the wrong direction.
        """
        serve(monkeypatch, FakeClient([]))
        assert setup.main(["--list"]) == 0
        out = capsys.readouterr().out

        assert "detector list was narrowed" in out
        assert "--discover" in out

    def test_the_wizard_refuses_a_pipe_rather_than_hanging(
            self, monkeypatch, capsys):
        serve(monkeypatch, board_and_card())
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

        assert setup.main([]) == 2
        assert "needs a terminal" in capsys.readouterr().err

    def test_ctrl_d_mid_question_writes_nothing(self, monkeypatch, tmp_path,
                                                capsys):
        serve(monkeypatch, board_and_card())
        config = tmp_path / "config.toml"

        assert setup.main(["--config", str(config)],
                          ask=scripted(["0.0"])) == 1
        capsys.readouterr()

        assert not config.exists()

    def test_choosing_nothing_leaves_an_existing_config_alone(
            self, monkeypatch, tmp_path, capsys):
        serve(monkeypatch, board_and_card())
        config = tmp_path / "config.toml"
        config.write_text("# mine\n")

        assert setup.main(["--config", str(config)], ask=scripted([""])) == 1
        capsys.readouterr()

        assert config.read_text() == "# mine\n"


class TestWizard:
    def run(self, monkeypatch, tmp_path, answers, detectors=("ASUS Aura Core",)):
        serve(monkeypatch, board_and_card())
        config = tmp_path / "config.toml"
        code = setup.main(
            ["--config", str(config),
             "--openrgb-config", str(openrgb_config(tmp_path, detectors))],
            ask=scripted(answers))
        return code, config

    def test_writes_the_zones_that_were_picked(self, monkeypatch, tmp_path,
                                               capsys):
        code, config = self.run(monkeypatch, tmp_path,
                                ["0.0", "", "24", "1.0", "", ""])
        capsys.readouterr()
        assert code == 0

        loaded = load_config(config)
        assert set(loaded.zones) == {"cpu_fans", "gpu"}
        assert loaded.zones["cpu_fans"].device == "ASUS PRIME X870-P WIFI"
        assert loaded.zones["cpu_fans"].zone == 0
        assert loaded.zones["gpu"].zone == 0

    def test_strip_length_is_asked_for_and_stored_per_zone(
            self, monkeypatch, tmp_path, capsys):
        """The number the user gives is the strip's real length, and a second
        strip is not obliged to be the same length as the first."""
        code, config = self.run(monkeypatch, tmp_path,
                                ["0.0", "", "24", "1.0", "", ""])
        capsys.readouterr()
        assert code == 0

        loaded = load_config(config)
        assert loaded.zones["cpu_fans"].leds == 24
        assert loaded.leds_for("cpu_fans") == 24
        # The card's zone is fixed at one LED, so it was never asked about and
        # falls through to the global default.
        assert loaded.zones["gpu"].leds is None
        assert loaded.leds_for("gpu") == loaded.zone_size

    def test_a_fixed_zone_is_not_asked_about_its_length(self, monkeypatch,
                                                       tmp_path, capsys):
        """A resize a fixed zone will reject is silent, so the question would be
        an invitation to a value that never takes effect."""
        code, config = self.run(monkeypatch, tmp_path, ["1.0", "", ""])
        capsys.readouterr()
        assert code == 0

        assert "leds" not in config.read_text().split("[ui]")[0].split(
            "[rgb.zones.gpu]")[1]

    def test_suggested_names_are_the_ones_the_link_feature_knows(
            self, monkeypatch, tmp_path, capsys):
        """Linking is defined as `cpu_fans` + `gpu`. Any other pair of names is
        a working config in which the TUI's `u` key does nothing."""
        code, config = self.run(monkeypatch, tmp_path,
                                ["0.0", "", "", "1.0", "", ""])
        capsys.readouterr()
        assert code == 0

        assert set(load_config(config).zones) == {"cpu_fans", "gpu"}

    def test_a_chosen_name_beats_the_suggestion(self, monkeypatch, tmp_path,
                                                capsys):
        code, config = self.run(monkeypatch, tmp_path,
                                ["0.0", "top_strip", "", ""])
        capsys.readouterr()
        assert code == 0

        assert set(load_config(config).zones) == {"top_strip"}

    def test_a_third_zone_is_just_another_block(self, monkeypatch, tmp_path,
                                                capsys):
        """Two zones is this rig, not the design. Nothing caps the count."""
        code, config = self.run(
            monkeypatch, tmp_path,
            ["0.0", "", "24", "0.1", "aura_core", "1.0", "", ""])
        capsys.readouterr()
        assert code == 0

        assert set(load_config(config).zones) == {"cpu_fans", "aura_core", "gpu"}

    def test_a_bad_coordinate_is_re_asked_not_fatal(self, monkeypatch, tmp_path,
                                                    capsys):
        code, config = self.run(monkeypatch, tmp_path,
                                ["9.9", "banana", "1.0", "", ""])
        out = capsys.readouterr().out
        assert code == 0

        assert out.count("no such zone") == 2
        assert set(load_config(config).zones) == {"gpu"}

    def test_a_non_numeric_strip_length_is_re_asked(self, monkeypatch, tmp_path,
                                                    capsys):
        code, config = self.run(monkeypatch, tmp_path,
                                ["0.0", "", "lots", "0", "30", ""])
        out = capsys.readouterr().out
        assert code == 0

        assert "needs to be a number" in out
        assert "at least one LED" in out
        assert load_config(config).zones["cpu_fans"].leds == 30

    def test_the_same_zone_twice_is_refused(self, monkeypatch, tmp_path, capsys):
        code, config = self.run(monkeypatch, tmp_path,
                                ["1.0", "", "1.0", ""])
        out = capsys.readouterr().out
        assert code == 0

        assert "already added" in out
        assert len(load_config(config).zones) == 1


class TestWrittenFile:
    def written(self, monkeypatch, tmp_path, detectors=()):
        serve(monkeypatch, board_and_card())
        config = tmp_path / "config.toml"
        setup.main(["--config", str(config),
                    "--openrgb-config", str(openrgb_config(tmp_path, detectors))],
                   ask=scripted(["0.0", "", "24", "1.0", "", ""]))
        return config

    def test_it_is_valid_toml_the_loader_accepts(self, monkeypatch, tmp_path,
                                                 capsys):
        config = self.written(monkeypatch, tmp_path)
        capsys.readouterr()

        tomllib.loads(config.read_text())
        assert load_config(config).zones

    def test_the_explanations_survive(self, monkeypatch, tmp_path, capsys):
        """The file is documented as hand-editable, which is only true if the
        comments saying what each value is for are still in it."""
        config = self.written(monkeypatch, tmp_path)
        capsys.readouterr()
        text = config.read_text()

        assert "rainbow shows a quarter of the hue wheel" in text
        assert "#4888" in text
        assert "substring" in text

    def test_device_names_are_escaped_rather_than_pasted(self, monkeypatch,
                                                        tmp_path, capsys):
        """A quote in a device name would otherwise write a broken file."""
        serve(monkeypatch, FakeClient([
            FakeDevice('Weird "Quoted" \\ Device', "LEDSTRIP",
                       [FakeZone("Zone", 0)])]))
        config = tmp_path / "config.toml"
        setup.main(["--config", str(config),
                    "--openrgb-config", str(openrgb_config(tmp_path, []))],
                   ask=scripted(["0.0", "", "10", ""]))
        capsys.readouterr()

        assert load_config(config).zones["strip"].device == \
            'Weird "Quoted" \\ Device'

    def test_a_previous_config_is_backed_up_not_replaced(self, monkeypatch,
                                                        tmp_path, capsys):
        config = tmp_path / "config.toml"
        config.write_text("# hand written, with notes\n")
        serve(monkeypatch, board_and_card())
        setup.main(["--config", str(config),
                    "--openrgb-config", str(openrgb_config(tmp_path, []))],
                   ask=scripted(["1.0", "", ""]))
        capsys.readouterr()

        backups = list(tmp_path.glob("config.toml.bak-*"))
        assert len(backups) == 1
        assert backups[0].read_text() == "# hand written, with notes\n"

    def test_settings_the_wizard_has_no_opinion_about_are_carried_over(
            self, monkeypatch, tmp_path, capsys):
        """A re-run must not silently reset the display ids or the link toggle."""
        config = tmp_path / "config.toml"
        config.write_text(
            "[rgb]\n[rgb.zones.a]\ndevice = 'x'\nzone = 0\n"
            "[ui]\nlink_cpu_gpu = false\n"
            "[display]\nvendor_id = 0x1234\nproduct_id = 0x5678\n")
        serve(monkeypatch, board_and_card())
        setup.main(["--config", str(config),
                    "--openrgb-config", str(openrgb_config(tmp_path, []))],
                   ask=scripted(["1.0", "", ""]))
        capsys.readouterr()

        loaded = load_config(config)
        assert loaded.display.vendor_id == 0x1234
        assert loaded.display.product_id == 0x5678
        assert loaded.link_cpu_gpu is False

    def test_the_detector_allowlist_follows_the_devices_chosen(
            self, monkeypatch, tmp_path, capsys):
        config = self.written(monkeypatch, tmp_path, detectors=[
            "ASUS Aura Addressable", "ASUS Aura Core", "ASUS Aura Motherboard",
            "Sapphire Radeon RX 9070 XT Nitro+",
            "Sapphire Radeon RX 5700 XT Nitro+",
            "Corsair Lighting Node Pro",
        ])
        capsys.readouterr()

        assert set(load_config(config).detectors) == {
            "ASUS Aura Addressable", "ASUS Aura Core", "ASUS Aura Motherboard",
            "Sapphire Radeon RX 9070 XT Nitro+",
        }

    def test_an_unreadable_openrgb_config_still_writes_the_zones(
            self, monkeypatch, tmp_path, capsys):
        """The zones are what the user came for; the allowlist has its own tool."""
        serve(monkeypatch, board_and_card())
        config = tmp_path / "config.toml"
        code = setup.main(["--config", str(config),
                           "--openrgb-config", str(tmp_path / "nope.json")],
                          ask=scripted(["1.0", "", ""]))
        out = capsys.readouterr().out

        assert code == 0
        assert "start OpenRGB once first" in out
        assert load_config(config).zones
        assert load_config(config).detectors == []
