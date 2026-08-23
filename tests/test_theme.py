"""Tests for finding and parsing the active theme's `colors.toml`.

Omarchy 4 moved the active theme to the XDG state directory and rewrote
`colors.toml` from the 16 ANSI slots into 24 semantic keys, with no
compatibility shim on either change. This tool has to read both, because an
install is not synchronised with the user's upgrade — so the search order and
the fail-soft path get held down here. The *mapping* of keys onto LED roles is
`tests/test_theme_following.py`'s subject; this file is only about which file
gets read and what happens when there is not one.

The v4 fixture is a real `colors.toml` body from a stock Omarchy theme
(tokyo-night) rather than invented hex, so a test passing means the reader
works on a palette that actually ships.

Everything is asserted through `led_colour`, the module's only public consumer
of the reader. There used to be a second one — `load_palette`, which resolved
contrast-checked text roles for the TUI — and its tests lived here too. It was
deleted with the TUI it was written for.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from mimarchy import theme  # noqa: E402

# Stock Omarchy 4 theme, verbatim.
V4 = """\
mode = "dark"

accent = "#7aa2f7"
selection = "#292e42"
muted = "#414868"

background = "#1a1b26"
dark_background = "#13141c"
darker_background = "#0e0e14"
lighter_background = "#24283b"

foreground = "#a9b1d6"
dark_foreground = "#565f89"
light_foreground = "#b4bee6"
bright_foreground = "#c0caf5"

red = "#f7768e"
yellow = "#e0af68"
orange = "#eb927b"
green = "#9ece6a"
cyan = "#449dab"
blue = "#7aa2f7"
magenta = "#ad8ee6"
brown = "#75493d"
"""

# Omarchy 3.x dialect: numbered ANSI slots. Deliberately given a different
# `color4` from V4's `accent`, so a test can tell which file was read.
V3 = """\
background = "#1a1b26"
foreground = "#a9b1d6"
color2 = "#9ece6a"
color3 = "#e0af68"
color4 = "#7da6ff"
color7 = "#a9b1d6"
color8 = "#414868"
"""


def write_theme(directory: Path, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "colors.toml").write_text(body)
    return directory


@pytest.fixture
def xdg(tmp_path, monkeypatch):
    """Point both XDG roots at a temp dir.

    `theme_dirs()` reads the environment on every call precisely so this works.
    """
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return tmp_path


def v4_dir(root: Path) -> Path:
    return root / "state" / "omarchy" / "current" / "theme"


def v3_dir(root: Path) -> Path:
    return root / "config" / "omarchy" / "current" / "theme"


class TestSearchOrder:
    def test_v4_location_wins_when_both_exist(self, xdg):
        """An un-migrated v3 theme must not shadow the live v4 one."""
        write_theme(v4_dir(xdg), V4)
        write_theme(v3_dir(xdg), V3)

        assert theme.led_colour("accent") == (0x7A, 0xA2, 0xF7)

    def test_v3_location_used_when_alone(self, xdg):
        write_theme(v3_dir(xdg), V3)
        assert theme.led_colour("accent") == (0x7D, 0xA6, 0xFF)  # color4

    def test_explicit_directory_is_the_only_one_consulted(self, xdg):
        write_theme(v4_dir(xdg), V4)
        explicit = write_theme(xdg / "elsewhere", V3)

        assert theme.led_colour("accent", explicit) == (0x7D, 0xA6, 0xFF)

    def test_dialect_is_read_from_content_not_location(self, xdg):
        """A hand-kept v3 theme left in the v4 path still resolves."""
        write_theme(v4_dir(xdg), V3)
        assert theme.led_colour("green") == (0x9E, 0xCE, 0x6A)  # color2


class TestFailSoft:
    def test_no_theme_at_all_yields_none(self, xdg):
        assert theme.led_colour("accent") is None

    def test_malformed_toml_yields_none(self, xdg):
        write_theme(v4_dir(xdg), "this is not { valid toml")
        assert theme.led_colour("accent") is None

    def test_malformed_file_falls_through_to_the_next_candidate(self, xdg):
        """A broken v4 theme should not hide a working v3 one."""
        write_theme(v4_dir(xdg), "this is not { valid toml")
        write_theme(v3_dir(xdg), V3)

        assert theme.led_colour("green") == (0x9E, 0xCE, 0x6A)

    def test_non_hex_values_are_ignored(self, xdg):
        """A theme key that is a string but not a colour is not a crash."""
        write_theme(v4_dir(xdg), """\
accent = "not-a-colour"
green = "#9ece6a"
""")
        assert theme.led_colour("accent") is None
        assert theme.led_colour("green") == (0x9E, 0xCE, 0x6A)
