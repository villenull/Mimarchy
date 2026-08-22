"""Which OpenRGB detectors get enabled, and — mostly — which do not.

This is the one part of Mimarchy that can hang a machine if it is wrong: OpenRGB's
broad GPU/I2C probing is a documented total-system freeze (#4888) and its server
starts at login, so an allowlist that is too generous is a freeze on every boot.
The tests are therefore weighted towards what is *refused*: a missed detector is
a dark zone somebody reports, a spurious one is a locked-up desktop nobody can.

The tool is loaded from `tools/` by path rather than imported, because it is a
script that ships next to the code rather than inside the package — and it is run
straight out of the checkout with a bare `python3`, which is exactly the path
`test_the_tool_runs_from_a_bare_checkout` pins down.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from mimarchy import detectors  # noqa: E402

TOOL_PATH = (Path(__file__).resolve().parents[1] / "tools"
             / "restrict-openrgb-detectors.py")

#: A plausible slice of OpenRGB's list: the reference rig's four, a handful of
#: other Sapphire cards (the trap — `device = "Sapphire"` names all of them),
#: and some unrelated hardware.
KNOWN = [
    "ASUS Aura Addressable",
    "ASUS Aura Core",
    "ASUS Aura Motherboard",
    "ASUS ROG Strix Keyboard",
    "Corsair Lighting Node Pro",
    "Gigabyte RGB Fusion 2 USB",
    "Sapphire Radeon RX 5700 XT Nitro+",
    "Sapphire Radeon RX 6800 XT Nitro+",
    "Sapphire Radeon RX 7900 XTX Nitro+",
    "Sapphire Radeon RX 9070 XT Nitro+",
    "Sapphire Radeon RX 9070 XT Pulse",
]


@pytest.fixture
def tool():
    spec = importlib.util.spec_from_file_location("restrict_tool", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def openrgb_config(tmp_path: Path, enabled=None) -> Path:
    """An OpenRGB.json with the usual surrounding settings, so the writer is
    tested against a document it has to preserve rather than against a stub."""
    path = tmp_path / "OpenRGB.json"
    path.write_text(json.dumps({
        "Detectors": {"detectors": {name: (enabled is None or name in enabled)
                                    for name in KNOWN}},
        "Server": {"port": 6742},
        "Theme": {"theme": "dark"},
    }, indent=4))
    return path


def mimarchy_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(body)
    return path


class TestMatching:
    def test_a_card_matches_its_own_detector_and_no_sibling(self):
        match = detectors.detectors_for_device(
            "Sapphire Radeon RX 9070 XT Nitro+", KNOWN)

        assert match.detectors == {"Sapphire Radeon RX 9070 XT Nitro+"}

    def test_punctuation_differences_do_not_matter(self):
        """Detector and device names disagree about `Nitro+` against `Nitro +`
        for the same hardware, and that difference is never meaningful."""
        match = detectors.detectors_for_device(
            "sapphire radeon rx 9070 xt nitro +", KNOWN)

        assert match.detectors == {"Sapphire Radeon RX 9070 XT Nitro+"}

    def test_a_vendor_name_is_refused_rather_than_expanded(self):
        """The trap this guard exists for.

        `device = "Sapphire"` is a perfectly good way to *find* one card by
        substring — it is what this repo's own default config shipped — and a
        terrible way to pick detectors, because it names every Sapphire card
        OpenRGB knows. Enabling those is dozens of I2C probes, i.e. the freeze.
        """
        match = detectors.detectors_for_device("Sapphire", KNOWN)

        assert match.detectors == set()
        assert "product line" in match.note

    def test_two_matches_are_already_too_many(self):
        """Found by running it: the guard used to allow anything up to the size
        of the largest real family, which let a four-card product line straight
        through — four I2C probes for cards this machine does not have."""
        match = detectors.detectors_for_device("Sapphire Radeon RX 9070", KNOWN)

        assert match.detectors == set()
        assert "2 detectors" in match.note

    def test_an_exact_name_beats_a_longer_one_containing_it(self):
        """A product and that product plus a variant suffix are two devices, and
        the one that reported this name is the first."""
        known = KNOWN + ["Corsair Lighting Node Pro (Legacy)"]
        match = detectors.detectors_for_device("Corsair Lighting Node Pro",
                                               known)

        assert match.detectors == {"Corsair Lighting Node Pro"}

    def test_an_asus_board_gets_the_aura_usb_family(self):
        """Named for the protocol at one end and the board model at the other,
        so nothing but a table can connect the two."""
        match = detectors.detectors_for_device("PRIME X870-P", KNOWN)

        assert match.detectors == {"ASUS Aura Addressable", "ASUS Aura Core",
                                   "ASUS Aura Motherboard"}

    def test_the_family_is_added_to_a_name_match_not_instead_of_it(self):
        """A board whose device name *is* a detector name still has three
        headers, and containment alone would light one of them."""
        match = detectors.detectors_for_device("ASUS Aura Motherboard", KNOWN)

        assert len(match.detectors) == 3

    def test_only_detectors_this_build_offers_come_back(self):
        match = detectors.detectors_for_device("PRIME X870-P",
                                               ["ASUS Aura Core"])

        assert match.detectors == {"ASUS Aura Core"}

    def test_an_unrecognised_device_says_so_instead_of_guessing(self):
        match = detectors.detectors_for_device("Some Unbranded Strip", KNOWN)

        assert match.detectors == set()
        assert match.note

    def test_board_tokens_are_whole_words(self):
        """`tuf` and `prime` are short enough to turn up inside other words."""
        assert detectors.detectors_for_device("Primetime LEDs", KNOWN).detectors \
            == set()

    def test_resolve_answers_once_per_device(self):
        matches = detectors.resolve(["PRIME X870-P", "Sapphire"], KNOWN)

        assert [m.device for m in matches] == ["PRIME X870-P", "Sapphire"]


class TestReadingOpenRGBsConfig:
    def test_a_missing_file_names_the_fix(self, tmp_path):
        with pytest.raises(detectors.DetectorConfigError, match="start OpenRGB"):
            detectors.read_config(tmp_path / "nope.json")

    def test_an_unexpected_shape_is_not_read_as_no_detectors(self, tmp_path):
        path = tmp_path / "OpenRGB.json"
        path.write_text(json.dumps({"Server": {}}))

        with pytest.raises(detectors.DetectorConfigError, match="Detectors"):
            detectors.read_config(path)


class TestAllowlist:
    def test_an_explicit_list_in_the_config_wins(self, tool, tmp_path):
        config = mimarchy_config(tmp_path, '[rgb]\ndetectors = ["ASUS Aura Core"]\n'
                                           '[rgb.zones.a]\ndevice = "Sapphire"\n'
                                           "zone = 0\n")
        keep, _notes = tool.wanted_detectors(config, KNOWN, [])

        assert keep == {"ASUS Aura Core"}

    def test_zones_are_derived_from_when_there_is_no_explicit_list(
            self, tool, tmp_path):
        config = mimarchy_config(
            tmp_path,
            '[rgb.zones.cpu_fans]\ndevice = "PRIME X870-P"\nzone = 0\n'
            '[rgb.zones.gpu]\ndevice = "Sapphire Radeon RX 9070 XT Nitro+"\n'
            "zone = 0\n")
        keep, _notes = tool.wanted_detectors(config, KNOWN, [])

        assert keep == {"ASUS Aura Addressable", "ASUS Aura Core",
                        "ASUS Aura Motherboard",
                        "Sapphire Radeon RX 9070 XT Nitro+"}

    def test_keep_adds_to_whatever_was_derived(self, tool, tmp_path):
        config = mimarchy_config(
            tmp_path, '[rgb.zones.a]\ndevice = "PRIME X870-P"\nzone = 0\n')
        keep, _notes = tool.wanted_detectors(config, KNOWN,
                                             ["Corsair Lighting Node Pro"])

        assert "Corsair Lighting Node Pro" in keep
        assert "ASUS Aura Core" in keep

    def test_no_config_falls_back_to_the_reference_rig(self, tool, tmp_path):
        """Preserved deliberately: it is what this tool did before it could read
        a config, and an install that never got as far as the wizard should not
        change behaviour underneath itself."""
        keep, notes = tool.wanted_detectors(tmp_path / "absent.toml", KNOWN, [])

        assert keep == set(detectors.REFERENCE_KEEP) & set(KNOWN)
        assert any("reference set" in note for note in notes)

    def test_a_config_that_derives_nothing_does_not_borrow_another_rigs_set(
            self, tool, tmp_path):
        """The reference set is for a machine that has chosen nothing at all.

        Falling back to it here would enable one particular card's I2C detector
        because somebody else's rig needed it — on a machine that has said, in
        writing, that it has different hardware.
        """
        config = mimarchy_config(
            tmp_path, '[rgb.zones.a]\ndevice = "Some Unbranded Strip"\nzone = 0\n')
        keep, _notes = tool.wanted_detectors(config, KNOWN, [])

        assert keep == set()

    def test_a_stale_detector_name_is_dropped_with_a_note(self, tool, tmp_path):
        """OpenRGB renames detectors between releases, and a name this build
        does not have would otherwise be an invisible missing device."""
        config = mimarchy_config(
            tmp_path, '[rgb]\ndetectors = ["ASUS Aura Core", "Retired Name"]\n')
        keep, notes = tool.wanted_detectors(config, KNOWN, [])

        assert keep == {"ASUS Aura Core"}
        assert any("Retired Name" in note for note in notes)


class TestApplying:
    def enabled(self, path: Path) -> set[str]:
        data = json.loads(path.read_text())
        return {k for k, v in data["Detectors"]["detectors"].items() if v}

    def test_only_the_allowlist_is_left_on(self, tool, tmp_path, capsys):
        openrgb = openrgb_config(tmp_path)
        config = mimarchy_config(
            tmp_path, '[rgb]\ndetectors = ["ASUS Aura Core"]\n')

        assert tool.main(["--config", str(openrgb),
                          "--mimarchy-config", str(config)]) == 0
        capsys.readouterr()

        assert self.enabled(openrgb) == {"ASUS Aura Core"}

    def test_the_rest_of_openrgbs_settings_survive(self, tool, tmp_path, capsys):
        """The whole document is rewritten, so anything the GUI stored has to
        come back out unchanged."""
        openrgb = openrgb_config(tmp_path)
        config = mimarchy_config(
            tmp_path, '[rgb]\ndetectors = ["ASUS Aura Core"]\n')
        tool.main(["--config", str(openrgb), "--mimarchy-config", str(config)])
        capsys.readouterr()

        data = json.loads(openrgb.read_text())
        assert data["Server"] == {"port": 6742}
        assert data["Theme"] == {"theme": "dark"}

    def test_the_previous_config_is_backed_up(self, tool, tmp_path, capsys):
        """The GUI rewrites this file too, so an undo has to exist."""
        openrgb = openrgb_config(tmp_path)
        config = mimarchy_config(
            tmp_path, '[rgb]\ndetectors = ["ASUS Aura Core"]\n')
        tool.main(["--config", str(openrgb), "--mimarchy-config", str(config)])
        capsys.readouterr()

        backups = list(tmp_path.glob("OpenRGB.json.bak-*"))
        assert len(backups) == 1
        assert len(self.enabled(backups[0])) == len(KNOWN)

    def test_nothing_derivable_leaves_the_file_untouched(self, tool, tmp_path,
                                                         capsys):
        """Refusing keeps whatever is enabled — possibly everything — so the
        message has to say that rather than read as "you are protected"."""
        openrgb = openrgb_config(tmp_path)
        before = openrgb.read_text()
        config = mimarchy_config(
            tmp_path, '[rgb.zones.a]\ndevice = "Some Unbranded Strip"\nzone = 0\n')

        assert tool.main(["--config", str(openrgb),
                          "--mimarchy-config", str(config)]) == 1
        err = capsys.readouterr().err

        assert openrgb.read_text() == before
        assert "left exactly as it is" in err
        assert "mimarchy-setup" in err

    def test_a_missing_openrgb_config_is_a_message(self, tool, tmp_path, capsys):
        assert tool.main(["--config", str(tmp_path / "nope.json")]) == 1
        assert "start OpenRGB once first" in capsys.readouterr().err


class TestCheck:
    def test_the_safe_set_passes(self, tool, tmp_path, capsys):
        openrgb = openrgb_config(tmp_path, enabled={"ASUS Aura Core"})
        config = mimarchy_config(
            tmp_path, '[rgb]\ndetectors = ["ASUS Aura Core"]\n')

        assert tool.main(["--config", str(openrgb), "--mimarchy-config",
                          str(config), "--check"]) == 0
        assert "OK" in capsys.readouterr().out

    def test_extra_detectors_fail_loudly_and_say_why(self, tool, tmp_path,
                                                     capsys):
        """This is the state a machine is in after someone opens the OpenRGB
        GUI, which rewrites the config and can re-enable everything."""
        openrgb = openrgb_config(tmp_path)
        config = mimarchy_config(
            tmp_path, '[rgb]\ndetectors = ["ASUS Aura Core"]\n')

        assert tool.main(["--config", str(openrgb), "--mimarchy-config",
                          str(config), "--check"]) == 1
        out = capsys.readouterr().out

        assert "UNSAFE" in out
        assert "#4888" in out

    def test_a_missing_detector_fails_too(self, tool, tmp_path, capsys):
        openrgb = openrgb_config(tmp_path, enabled=set())
        config = mimarchy_config(
            tmp_path, '[rgb]\ndetectors = ["ASUS Aura Core"]\n')

        assert tool.main(["--config", str(openrgb), "--mimarchy-config",
                          str(config), "--check"]) == 1
        assert "missing expected" in capsys.readouterr().out

    def test_check_writes_nothing(self, tool, tmp_path, capsys):
        openrgb = openrgb_config(tmp_path)
        before = openrgb.read_text()
        tool.main(["--config", str(openrgb), "--check"])
        capsys.readouterr()

        assert openrgb.read_text() == before
        assert not list(tmp_path.glob("OpenRGB.json.bak-*"))


class TestDiscover:
    def test_it_warns_before_it_asks(self, tool, tmp_path, monkeypatch, capsys):
        """The one command here that can hang the machine it runs on."""
        openrgb = openrgb_config(tmp_path, enabled={"ASUS Aura Core"})
        monkeypatch.setattr("builtins.input", lambda _prompt: "no")

        assert tool.main(["--config", str(openrgb), "--discover"]) == 1
        out = capsys.readouterr().out

        assert "#4888" in out
        assert self.still_narrow(openrgb)

    def still_narrow(self, path: Path) -> bool:
        data = json.loads(path.read_text())
        return sum(data["Detectors"]["detectors"].values()) == 1

    def test_confirming_enables_everything(self, tool, tmp_path, monkeypatch,
                                           capsys):
        openrgb = openrgb_config(tmp_path, enabled={"ASUS Aura Core"})
        monkeypatch.setattr("builtins.input", lambda _prompt: "discover")

        assert tool.main(["--config", str(openrgb), "--discover"]) == 0
        capsys.readouterr()

        assert not self.still_narrow(openrgb)
        assert list(tmp_path.glob("OpenRGB.json.bak-*"))

    def test_anything_but_the_word_is_a_refusal(self, tool, tmp_path,
                                                monkeypatch, capsys):
        openrgb = openrgb_config(tmp_path, enabled={"ASUS Aura Core"})
        monkeypatch.setattr("builtins.input", lambda _prompt: "y")

        assert tool.main(["--config", str(openrgb), "--discover"]) == 1
        capsys.readouterr()

        assert self.still_narrow(openrgb)


def test_the_tool_runs_outside_the_virtualenv():
    """The README tells people to re-run this after opening the OpenRGB GUI, by
    which point the virtualenv is long out of mind — so it has to find its half
    of the logic in the checkout it is sitting in, under a `python3` that has
    never heard of `mimarchy`. Skipped where no such interpreter exists, since
    the alternative is testing the venv against itself and proving nothing."""
    import subprocess

    system_python = Path("/usr/bin/python3")
    if not system_python.exists() or sys.prefix == sys.base_prefix:
        pytest.skip("no interpreter here without mimarchy installed")

    result = subprocess.run(
        [str(system_python), str(TOOL_PATH), "--help"],
        capture_output=True, text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "--discover" in result.stdout
