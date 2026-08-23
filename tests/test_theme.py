"""Tests for finding and parsing the active theme's `colors.toml`.

The fail-soft path gets held down here — a missing or malformed file, an
explicit directory override — because a mistake in it is silent everywhere
else. The *mapping* of keys onto LED roles is `tests/test_theme_following.py`'s
subject; this file is only about which file gets read and what happens when
there is not one.

The fixture is a real `colors.toml` body from a stock Omarchy theme
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

# A second, differently-valued theme — only for telling "which file did this
# come from" apart in the explicit-directory test below. Not another dialect.
ALT = """\
background = "#1a1b26"
foreground = "#a9b1d6"
accent = "#7da6ff"
green = "#9ece6a"
"""


def write_theme(directory: Path, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "colors.toml").write_text(body)
    return directory


@pytest.fixture
def xdg(tmp_path, monkeypatch):
    """Point the XDG state root at a temp dir.

    `theme_dirs()` reads the environment on every call precisely so this works.
    """
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return tmp_path


def v4_dir(root: Path) -> Path:
    return root / "state" / "omarchy" / "current" / "theme"


class TestSearchOrder:
    def test_explicit_directory_is_the_only_one_consulted(self, xdg):
        write_theme(v4_dir(xdg), V4)
        explicit = write_theme(xdg / "elsewhere", ALT)

        assert theme.led_colour("accent", explicit) == (0x7D, 0xA6, 0xFF)


class TestFailSoft:
    def test_no_theme_at_all_yields_none(self, xdg):
        assert theme.led_colour("accent") is None

    def test_malformed_toml_yields_none(self, xdg):
        write_theme(v4_dir(xdg), "this is not { valid toml")
        assert theme.led_colour("accent") is None

    def test_non_hex_values_are_ignored(self, xdg):
        """A theme key that is a string but not a colour is not a crash."""
        write_theme(v4_dir(xdg), """\
accent = "not-a-colour"
green = "#9ece6a"
""")
        assert theme.led_colour("accent") is None
        assert theme.led_colour("green") == (0x9E, 0xCE, 0x6A)
