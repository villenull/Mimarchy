"""Colours borrowed from the user's Omarchy theme, resolved at runtime.

Omarchy ships every theme as a `colors.toml` of the same 22 keys — the 16 ANSI
slots plus `accent`, `cursor`, `foreground`, `background` and a selection pair —
and symlinks the active one to `~/.config/omarchy/current/theme`. Reading that
symlink at startup is what makes this tool re-theme with everything else on the
desktop instead of carrying its own palette.

Roles are assigned to *slots*, never to hex values, which is the whole point:
`color3` is "the theme's yellow" in every theme, so the header colour tracks the
theme rather than being gold forever.

Two things this module refuses to assume:

* **That a slot is legible.** Themes disagree about what the dim slots mean —
  Gruvbox sets `color8` to `#3c3836` against a `#282828` background, a contrast
  ratio of 1.3, which is a footer nobody can read. Every role therefore goes
  through `_legible`, which falls back to the plain foreground when the chosen
  slot does not clear a contrast threshold against the background.
* **That the theme exists.** A missing or malformed `colors.toml` yields
  `Palette.fallback()`, whose values are Textual's own design tokens rather than
  invented hex, so the TUI still starts on a machine without Omarchy.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

#: Omarchy symlinks the active theme here. Following the symlink on every load
#: (rather than caching a resolved path) is what lets a theme switch be picked up
#: by simply reopening the TUI.
THEME_DIR = Path.home() / ".config" / "omarchy" / "current" / "theme"

#: Minimum contrast ratio a slot must reach against the background before it is
#: trusted for text. 3.0 is the WCAG large-text bar — the right one here, since
#: every string this styles is a short label at terminal size, and holding out
#: for 4.5 would reject slots that read perfectly well.
MIN_CONTRAST = 3.0


@dataclass(frozen=True)
class Palette:
    """The colour roles this TUI needs, already resolved to hex strings.

    Named by *role*, not by slot or by hue, so the TUI never mentions a colour:
    `frame` rather than `sage`, `header` rather than `gold`. Renaming a hue after
    a theme switch is how hardcoded palettes creep back in.
    """

    background: str
    foreground: str
    frame: str        # Lights panel border + title
    header: str       # Lights column headers
    select_bg: str    # selected row background
    select_fg: str    # selected row text
    accent: str       # Controls border, title, labels, and the *active* value
    footer: str       # hint bar, one colour for keys and descriptions alike

    @classmethod
    def fallback(cls) -> "Palette":
        """Used when there is no Omarchy theme to read.

        Terminal colour *names*, not hex and not Textual design tokens, because
        these values are consumed in two places that understand different
        dialects: Textual CSS (which takes `$variables`, names, and hex) and Rich
        markup inside the Controls panel (which takes names and hex but not
        Textual variables). Names are the only vocabulary both accept, and they
        also mean the terminal's own palette still shows through — so a machine
        without Omarchy gets the user's terminal theme rather than a second
        hardcoded palette competing with the first.
        """
        return cls(
            background="black", foreground="white",
            frame="green", header="yellow",
            select_bg="blue", select_fg="black",
            accent="magenta", footer="bright_black",
        )

    def css_variables(self) -> dict[str, str]:
        """Role -> value, for `App.get_css_variables`, as `$mim-<role>`.

        Field names are dashed on the way out: `select_bg` becomes
        `$mim-select-bg`, matching how every built-in Textual token is spelled.
        Leaving the underscore in works but reads as foreign next to
        `$text-muted`, and it is the kind of inconsistency that turns into a
        typo'd variable and a startup error later.
        """
        return {f"mim-{name.replace('_', '-')}": value
                for name, value in vars(self).items()}


def _channels(hex_colour: str) -> tuple[float, float, float] | None:
    h = hex_colour.strip().lstrip("#")
    if len(h) != 6:
        return None
    try:
        return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return None


def _luminance(hex_colour: str) -> float | None:
    """WCAG relative luminance, which needs the sRGB transfer curve undone first.

    Averaging the raw bytes instead is the tempting shortcut and it misjudges
    exactly the mid-tone slots this has to rule on.
    """
    ch = _channels(hex_colour)
    if ch is None:
        return None
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
           for c in ch]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float | None:
    """WCAG contrast ratio, 1.0 (identical) to 21.0 (black on white)."""
    la, lb = _luminance(a), _luminance(b)
    if la is None or lb is None:
        return None
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _legible(candidate: str, background: str, fallback: str) -> str:
    """`candidate` if it can be read against `background`, else `fallback`.

    Unknown values pass through untouched — a CSS variable reference has no hex
    to measure, and rejecting it would throw away the fallback palette.
    """
    ratio = contrast(candidate, background)
    if ratio is None or ratio >= MIN_CONTRAST:
        return candidate
    return fallback


def load_palette(theme_dir: Path = THEME_DIR) -> Palette:
    """Read `colors.toml` and map its slots onto this TUI's roles.

    The slot choices follow the roles the design asks for — a list panel framed
    in the theme's green with yellow headers, a settings panel in a single warm
    accent — but nothing here depends on those hues being green or warm. A theme
    that makes `color2` blue simply gets a blue frame.
    """
    try:
        with (theme_dir / "colors.toml").open("rb") as f:
            raw = tomllib.load(f)
    except (OSError, ValueError):
        return Palette.fallback()

    fb = Palette.fallback()

    def slot(name: str, default: str) -> str:
        value = raw.get(name)
        return value if isinstance(value, str) and _channels(value) else default

    background = slot("background", fb.background)
    foreground = slot("foreground", fb.foreground)

    def text(name: str, default: str) -> str:
        return _legible(slot(name, default), background, foreground)

    # The selected row is the one place a slot is used as a *background*, so it is
    # paired with the theme background as its text colour rather than the
    # foreground: every theme sampled puts its bright slots and its background at
    # opposite ends, which is the contrast wanted here. Light themes are the case
    # that can break it, so the pairing is checked rather than assumed.
    select_bg = slot("color12", fb.select_bg)
    select_fg = _legible(background, select_bg, foreground)

    return Palette(
        background=background,
        foreground=foreground,
        frame=text("color2", fb.frame),
        header=text("color3", fb.header),
        select_bg=select_bg,
        select_fg=select_fg,
        accent=text("color7", fb.accent),
        footer=text("color8", fb.foreground),
    )


@lru_cache(maxsize=1)
def palette() -> Palette:
    """The active palette, read once per process.

    Cached because it is consumed on every panel repaint, and read at startup
    rather than watched: a theme switch is picked up the next time the TUI opens,
    which is how the rest of Omarchy's terminal utilities behave too.
    """
    return load_palette()
