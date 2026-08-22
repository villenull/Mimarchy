"""Tests for palette loading across both Omarchy theme dialects.

Omarchy 4 moved the active theme to the XDG state directory and rewrote
`colors.toml` from the 16 ANSI slots into 24 semantic keys, with no
compatibility shim on either change. This tool has to read both, because an
install is not synchronised with the user's upgrade — so the mapping, the
search order, and the fail-soft path all get held down here.

The v4 fixtures are the real `colors.toml` bodies from stock Omarchy themes
(tokyo-night, and catppuccin-latte for the light-mode case) rather than
invented hex, so a test passing means the mapping works on a palette that
actually ships.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from mimarchy import theme  # noqa: E402

# Stock Omarchy 4 theme, verbatim. Dark mode.
V4_DARK = """\
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

# Stock Omarchy 4 theme, verbatim. Light mode — the case that breaks a mapping
# which assumes the background is the dark end of the ramp.
V4_LIGHT = """\
mode = "light"

accent = "#1e66f5"
selection = "#ccd0da"
muted = "#acb0be"

background = "#eff1f5"
dark_background = "#e3e4e8"
darker_background = "#d7d8dc"
lighter_background = "#dce0e8"

foreground = "#4c4f69"
dark_foreground = "#9ca0b0"
light_foreground = "#5c5f77"
bright_foreground = "#4c4f69"

red = "#d20f39"
yellow = "#df8e1d"
orange = "#d84e2b"
green = "#40a02b"
cyan = "#179299"
blue = "#1e66f5"
magenta = "#ea76cb"
brown = "#6c2715"
"""

# Omarchy 3.x dialect: numbered ANSI slots.
V3 = """\
background = "#1a1b26"
foreground = "#a9b1d6"
color2 = "#9ece6a"
color3 = "#e0af68"
color7 = "#a9b1d6"
color8 = "#414868"
color12 = "#7da6ff"
"""


def write_theme(directory: Path, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "colors.toml").write_text(body)
    return directory


@pytest.fixture
def xdg(tmp_path, monkeypatch):
    """Point both XDG roots at a temp dir and clear the palette cache.

    `theme_dirs()` reads the environment on every call precisely so this works;
    the cache on `palette()` is process-wide and would otherwise leak one
    test's theme into the next.
    """
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    theme.palette.cache_clear()
    yield tmp_path
    theme.palette.cache_clear()


def v4_dir(root: Path) -> Path:
    return root / "state" / "omarchy" / "current" / "theme"


def v3_dir(root: Path) -> Path:
    return root / "config" / "omarchy" / "current" / "theme"


class TestV4Dialect:
    def test_semantic_keys_map_onto_roles(self, xdg):
        write_theme(v4_dir(xdg), V4_DARK)
        p = theme.load_palette()

        assert p.background == "#1a1b26"
        assert p.foreground == "#a9b1d6"
        assert p.frame == "#9ece6a"       # green
        assert p.header == "#e0af68"      # yellow
        assert p.accent == "#7aa2f7"      # a real key in v4, not a borrowed slot

    def test_muted_footer_yields_to_the_legibility_guard(self, xdg):
        """tokyo-night's `muted` is 1.9 against its background, so it loses.

        Asserted as policy rather than as a hex value because `muted` is
        *designed* to sit low — it clears MIN_CONTRAST on only three of the 22
        stock themes. The guard firing here is the feature, not a mapping bug.
        """
        write_theme(v4_dir(xdg), V4_DARK)
        p = theme.load_palette()

        assert theme.contrast("#414868", p.background) < theme.MIN_CONTRAST
        assert p.footer == p.foreground

    def test_muted_footer_is_used_when_the_theme_makes_it_readable(self, xdg):
        """Ethereal's muted clears the bar, and then the theme wins."""
        write_theme(v4_dir(xdg), """\
background = "#000000"
foreground = "#ffffff"
muted = "#8a8a8a"
selection = "#222222"
""")
        p = theme.load_palette()

        assert theme.contrast("#8a8a8a", "#000000") >= theme.MIN_CONTRAST
        assert p.footer == "#8a8a8a"

    def test_selection_pairs_with_bright_foreground(self, xdg):
        """v4 documents selection_foreground = bright_foreground; follow it."""
        write_theme(v4_dir(xdg), V4_DARK)
        p = theme.load_palette()

        assert p.select_bg == "#292e42"
        assert p.select_fg == "#c0caf5"

    def test_light_theme_selection_stays_legible(self, xdg):
        """The light-mode pairing has to clear the bar too, not just dark."""
        write_theme(v4_dir(xdg), V4_LIGHT)
        p = theme.load_palette()

        ratio = theme.contrast(p.select_fg, p.select_bg)
        assert ratio is not None and ratio >= theme.MIN_CONTRAST

    def test_selection_has_an_escape_when_bright_foreground_is_unreadable(self, xdg):
        """The guard must have two real options, not the same colour twice.

        Light themes routinely set `foreground` and `bright_foreground` to the
        same value — catppuccin-latte does — so a fallback from one to the other
        is a no-op, and a selection the theme made unreadable would stay
        unreadable. Here `bright_foreground` is deliberately illegible on
        `selection`, and the background is the only way out.
        """
        write_theme(v4_dir(xdg), """\
background = "#000000"
foreground = "#fdfdfd"
bright_foreground = "#fdfdfd"
selection = "#ffffff"
""")
        p = theme.load_palette()

        assert p.select_fg == "#000000"
        ratio = theme.contrast(p.select_fg, p.select_bg)
        assert ratio is not None and ratio >= theme.MIN_CONTRAST

    def test_every_role_is_legible_on_every_stock_dialect(self, xdg):
        """No role may come back unreadable against its own background."""
        for body in (V4_DARK, V4_LIGHT):
            write_theme(v4_dir(xdg), body)
            theme.palette.cache_clear()
            p = theme.load_palette()
            for role in ("frame", "header", "accent", "footer"):
                ratio = theme.contrast(getattr(p, role), p.background)
                assert ratio is not None and ratio >= theme.MIN_CONTRAST, role

    def test_dark_theme_keeps_its_roles_distinct(self, xdg):
        """Legible is not enough — the roles must still be *different colours*.

        Asserting only contrast cannot catch a mapping that has quietly
        collapsed every role onto the foreground, because the foreground passes
        the contrast check by construction. tokyo-night should lose exactly one
        role (the footer, whose `muted` is deliberately dim).
        """
        write_theme(v4_dir(xdg), V4_DARK)
        p = theme.load_palette()

        assert p.footer == p.foreground
        assert len({p.frame, p.header, p.accent}) == 3
        for role in ("frame", "header", "accent"):
            assert getattr(p, role) != p.foreground, role

    def test_light_theme_collapse_is_known_and_bounded(self, xdg):
        """catppuccin-latte is the worst case: its bright hues miss the bar.

        Pinned rather than fixed. The guard is doing what it is meant to, and
        preferring a themed-but-dim colour instead is a colour-policy decision
        that belongs with the theme-following work. This test is here so that
        change shows up as a deliberate edit rather than a silent drift.
        """
        write_theme(v4_dir(xdg), V4_LIGHT)
        p = theme.load_palette()

        collapsed = [r for r in ("frame", "header", "accent", "footer")
                     if getattr(p, r) == p.foreground]
        assert collapsed == ["frame", "header", "footer"]
        assert p.accent == "#1e66f5"      # the accent still survives


class TestV3Dialect:
    def test_numbered_slots_still_map(self, xdg):
        write_theme(v3_dir(xdg), V3)
        p = theme.load_palette()

        assert p.frame == "#9ece6a"       # color2
        assert p.header == "#e0af68"      # color3
        assert p.accent == "#a9b1d6"      # color7
        assert p.select_bg == "#7da6ff"   # color12
        # color8 is "bright black" and fails the guard here, exactly as it did
        # before the v4 work — the dim-footer behaviour is unchanged by it.
        assert p.footer == p.foreground

    def test_illegible_slot_falls_back_to_foreground(self, xdg):
        """Gruvbox's color8 against its background is a 1.3 ratio footer."""
        write_theme(v3_dir(xdg), """\
background = "#282828"
foreground = "#ebdbb2"
color8 = "#3c3836"
""")
        p = theme.load_palette()
        assert p.footer == "#ebdbb2"


class TestSearchOrder:
    def test_v4_location_wins_when_both_exist(self, xdg):
        """An un-migrated v3 theme must not shadow the live v4 one."""
        write_theme(v4_dir(xdg), V4_DARK)
        write_theme(v3_dir(xdg), V3)

        assert theme.load_palette().accent == "#7aa2f7"

    def test_v3_location_used_when_alone(self, xdg):
        write_theme(v3_dir(xdg), V3)
        assert theme.load_palette().frame == "#9ece6a"

    def test_explicit_directory_is_the_only_one_consulted(self, xdg):
        write_theme(v4_dir(xdg), V4_DARK)
        explicit = write_theme(xdg / "elsewhere", V3)

        assert theme.load_palette(explicit).accent == "#a9b1d6"

    def test_dialect_is_read_from_content_not_location(self, xdg):
        """A hand-kept v3 theme left in the v4 path still resolves."""
        write_theme(v4_dir(xdg), V3)
        assert theme.load_palette().frame == "#9ece6a"


class TestFailSoft:
    def test_no_theme_at_all_yields_fallback(self, xdg):
        assert theme.load_palette() == theme.Palette.fallback()

    def test_malformed_toml_yields_fallback(self, xdg):
        write_theme(v4_dir(xdg), "this is not { valid toml")
        assert theme.load_palette() == theme.Palette.fallback()

    def test_malformed_file_falls_through_to_the_next_candidate(self, xdg):
        """A broken v4 theme should not hide a working v3 one."""
        write_theme(v4_dir(xdg), "this is not { valid toml")
        write_theme(v3_dir(xdg), V3)

        assert theme.load_palette().frame == "#9ece6a"

    def test_non_hex_values_are_ignored(self, xdg):
        write_theme(v4_dir(xdg), """\
muted = "not-a-colour"
background = "#1a1b26"
foreground = "#a9b1d6"
green = "#9ece6a"
""")
        p = theme.load_palette()
        assert p.frame == "#9ece6a"
        assert p.footer == "#a9b1d6"      # fell back to foreground


def test_css_variables_are_dashed():
    names = theme.Palette.fallback().css_variables()
    assert "mim-select-bg" in names
    assert "mim_select_bg" not in names


def test_fallback_palette_is_renderable_by_both_engines():
    """Every fallback value must parse in Textual *and* in Rich.

    This is the palette a machine with no Omarchy theme gets, so a value only
    one engine understands is a crash on exactly the install that has no theme
    to fall back to — which is how `bright_black` (fine in Rich, rejected by
    Textual) shipped as a startup error. Both parsers are asserted here rather
    than trusting that a plausible-looking colour name is portable.
    """
    from rich.style import Style
    from textual.color import Color

    for role, value in vars(theme.Palette.fallback()).items():
        Color.parse(value)              # Textual CSS
        Style.parse(value)              # Rich markup
