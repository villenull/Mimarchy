"""Colours borrowed from the user's Omarchy theme, resolved at runtime.

Omarchy ships every theme as a `colors.toml` and symlinks the active one into
place, at `~/.local/state/omarchy/current/theme`. Reading that symlink on
every lookup is what makes this tool re-theme with everything else on the
desktop instead of carrying its own palette.

The palette is *semantic* keys (around two dozen, and not a fixed count:
`orange` and `brown` are absent from three of the stock themes): `accent`,
`selection`, `muted`, four background and four foreground tiers, eight named
colours, and bright variants of most.

Roles are assigned to *keys*, never to hex values, which is the whole point:
`accent` is "the theme's accent" in every theme, so it tracks the theme rather
than being one particular blue forever.

There is no fallback palette here. A missing or malformed `colors.toml` reads
as None and every caller decides for itself, because each of them already has
something better to fall back on than a colour invented in this module — see
`led_colour`.
"""

from __future__ import annotations

import colorsys
import os
import tomllib
from pathlib import Path


def theme_dirs() -> list[Path]:
    """Candidate active-theme directories.

    A list rather than a single path so an explicit override — passed by
    callers, and by every test — has somewhere to slot in without a second
    code path. In practice there is exactly one: Omarchy 4's XDG state
    directory.

    Read from the environment on every call rather than cached at import, so
    the XDG variable can be pointed elsewhere (which is how this is tested).
    """
    state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return [state / "omarchy" / "current" / "theme"]


def _channels(hex_colour: str) -> tuple[float, float, float] | None:
    h = hex_colour.strip().lstrip("#")
    if len(h) != 6:
        return None
    try:
        return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return None


def _read_theme(theme_dir: Path | None = None) -> dict | None:
    """The active theme's parsed `colors.toml`, or None if there is not one.

    With no directory given, every candidate from `theme_dirs()` is tried in
    order and the first one that parses wins; a directory passed explicitly is
    the only one consulted.
    """
    for candidate in ([theme_dir] if theme_dir is not None else theme_dirs()):
        try:
            with (candidate / "colors.toml").open("rb") as f:
                return tomllib.load(f)
        except (OSError, ValueError):
            continue
    return None


# --------------------------------------------------------------------------
# LED colours
#
# These are light in a dark room, not text on a background. Nothing here has a
# legibility guard and nothing here should grow one: there is no background to
# contrast against, nothing has to be readable, and the only real failure is a
# colour so dim the strip looks off. That is what LED_VALUE_FLOOR is for.
#
# This module used to carry a second, text-shaped vocabulary too — a Palette of
# contrast-checked roles for the TUI's Textual CSS. The TUI is gone, and it went
# with it.
# --------------------------------------------------------------------------

#: Theme keys offered as LED colours, in the order the panel cycles them.
#:
#: Vivid roles only. `muted`, the background tiers and the foreground tiers are
#: deliberately absent — they are chosen by theme authors to sit *quietly*
#: against a background, which is the opposite of what a light source wants, and
#: several of them are near-black. `brown` is skipped for the same reason it is
#: rarely used in a UI: it is the one named hue that reads as "dirty" rather than
#: as a colour when a strip is showing it.
LED_ROLES = ("accent", "red", "orange", "yellow", "green", "cyan", "blue",
             "magenta")

#: HSV value a theme colour is lifted to before it drives an LED.
#:
#: A floor, not a scale, and that distinction is the whole design. Measured over
#: the 8 roles across all 22 stock v4 themes (173 defined colours): the median
#: value is 0.70–0.87 and only 17 of them fall below 0.55, 7 below 0.40. Scaling
#: every colour to a target brightness would therefore rewrite ~90% of them to
#: rescue the ~10% that need it, and the rewrite is visible — it is what makes a
#: carefully chosen muted theme come back looking like a toy. Lifting only what
#: is under the floor leaves the great majority of themes reproduced exactly as
#: authored.
#:
#: Hue and saturation are never touched. A dim blue becomes a brighter blue, not
#: a whiter one.
LED_VALUE_FLOOR = 0.55


def _lift(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    """Raise a colour to `LED_VALUE_FLOOR` if it falls below it, preserving hue."""
    r, g, b = (c / 255 for c in rgb)
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    if v >= LED_VALUE_FLOOR or v == 0:
        return rgb
    lifted = colorsys.hsv_to_rgb(h, s, LED_VALUE_FLOOR)
    return tuple(round(c * 255) for c in lifted)  # type: ignore[return-value]


def led_colour(role: str, theme_dir: Path | None = None
               ) -> tuple[int, int, int] | None:
    """The active theme's colour for `role`, as RGB, or None if unavailable.

    None rather than a substitute colour, because every caller already has
    something better to fall back on than a guess made here: the state file
    keeps the last colour that resolved, so a theme missing `orange` (three of
    the stock themes do) keeps showing whatever it showed before rather than
    silently becoming a different hue.
    """
    if role not in LED_ROLES:
        return None

    raw = _read_theme(theme_dir)
    if raw is None:
        return None

    value = raw.get(role)
    if not isinstance(value, str):
        return None

    channels = _channels(value)
    if channels is None:
        return None
    return _lift(tuple(round(c * 255) for c in channels))  # type: ignore[arg-type]