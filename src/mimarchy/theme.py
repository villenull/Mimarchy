"""Colours borrowed from the user's Omarchy theme, resolved at runtime.

Omarchy ships every theme as a `colors.toml` and symlinks the active one into
place. Reading that symlink at startup is what makes this tool re-theme with
everything else on the desktop instead of carrying its own palette.

Two things about that file changed in Omarchy 4, and this module handles both
so a single build works either side of the upgrade:

* **Where it lives.** v3 kept the active theme at
  `~/.config/omarchy/current/theme`; v4 moved it to
  `~/.local/state/omarchy/current/theme` and left no compatibility symlink.
  Both are tried, newest first.
* **What is in it.** v3 spelled the palette as the 16 ANSI slots — `color0`
  through `color15` — plus a few extras. v4 replaced that with *semantic*
  keys (around two dozen, and not a fixed count: `orange` and `brown` are
  absent from three of the stock themes): `accent`, `selection`, `muted`,
  four background and four foreground tiers, eight named colours, and bright
  variants of most. The two dialects are mapped onto the same roles below, so
  nothing downstream knows which one it got.

Roles are assigned to *keys*, never to hex values, which is the whole point:
the header is "the theme's yellow" in every theme, so it tracks the theme
rather than being gold forever.

Two things this module refuses to assume:

* **That a key is legible.** Themes disagree about what the dim values mean —
  Gruvbox sets its v3 `color8` to `#3c3836` against a `#282828` background, a
  contrast ratio of 1.3, which is a footer nobody can read. Every role
  therefore goes through `_legible`, which falls back to the plain foreground
  when the chosen key does not clear a contrast threshold against the
  background.
* **That the theme exists.** A missing or malformed `colors.toml` yields
  `Palette.fallback()`, whose values are terminal colour names rather than
  invented hex, so the TUI still starts on a machine without Omarchy.
"""

from __future__ import annotations

import colorsys
import os
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

#: Minimum contrast ratio a key must reach against the background before it is
#: trusted for text. 3.0 is the WCAG large-text bar — the right one here, since
#: every string this styles is a short label at terminal size, and holding out
#: for 4.5 would reject values that read perfectly well.
MIN_CONTRAST = 3.0


def theme_dirs() -> list[Path]:
    """Candidate active-theme directories, most current first.

    Omarchy 4 moved the active theme out of `~/.config` and into the XDG state
    directory, without leaving a symlink behind. Returning both — rather than
    picking one at import time — is what lets the same install work on a
    machine that has not upgraded yet, and makes the upgrade a no-op here.

    Read from the environment on every call rather than cached at import, so
    the XDG variables can be pointed elsewhere (which is how this is tested).
    """
    state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return [
        state / "omarchy" / "current" / "theme",     # Omarchy 4
        config / "omarchy" / "current" / "theme",    # Omarchy 3.x
    ]


@dataclass(frozen=True)
class Palette:
    """The colour roles this TUI needs, already resolved to hex strings.

    Named by *role*, not by palette key or by hue, so the TUI never mentions a
    colour: `frame` rather than `sage`, `header` rather than `gold`. Renaming a
    hue after a theme switch is how hardcoded palettes creep back in.
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
        Textual variables). Names are the vocabulary both accept, and they also
        mean the terminal's own palette still shows through — so a machine
        without Omarchy gets the user's terminal theme rather than a second
        hardcoded palette competing with the first.

        The footer is the one exception, and it is not a matter of taste. There
        is no *name* for a dim grey that both engines take: Rich wants
        `bright_black`, Textual rejects that and wants `ansi_bright_black`,
        which Rich in turn rejects — and the same mismatch holds for `grey`,
        `grey50`, `dim`, and `silver`. Shipping either name means the TUI raises
        a stylesheet error on startup on every machine that has no Omarchy theme
        to read, which is precisely the case this palette exists to serve. Hex is
        the only vocabulary left, so the footer alone is spelled that way.
        """
        return cls(
            background="black", foreground="white",
            frame="green", header="yellow",
            select_bg="blue", select_fg="black",
            accent="magenta", footer="#808080",
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
    exactly the mid-tone values this has to rule on.
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


def _most_legible(background: str, *candidates: str) -> str:
    """Whichever candidate reads best against `background`.

    Needed where the obvious fallback may be the same colour as the thing it is
    meant to rescue: several themes (every light one sampled) set `foreground`
    and `bright_foreground` identically, so falling back from one to the other
    is a no-op and an unreadable selected row would have no way out. Picking by
    measured contrast gives the guard something to actually choose between.

    Unmeasurable candidates (colour *names*, from the fallback palette) score
    below any real one but still beat returning nothing, so the first is kept.
    """
    best, best_ratio = candidates[0], -1.0
    for candidate in candidates:
        ratio = contrast(candidate, background)
        if ratio is not None and ratio > best_ratio:
            best, best_ratio = candidate, ratio
    return best


def _shift_to_contrast(candidate: str, background: str) -> str | None:
    """`candidate` moved along its own brightness until it clears MIN_CONTRAST.

    Hue and saturation are held fixed, so the theme's green stays green and only
    stops being *too pale against white* — which is what a designer picking a
    text colour for a light theme does by hand, and what simply substituting the
    foreground refuses to do.

    Binary search rather than a fixed nudge because relative luminance is
    monotonic in HSV value at fixed hue and saturation, so this converges on the
    smallest change that clears the bar. Taking a bigger step would darken a
    colour further than the theme's own design needs.

    Both directions are attempted and the nearer result wins: on a light
    background the answer is almost always darker and on a dark one lighter, but
    a mid-tone background genuinely admits either, and a saturated hue can hit
    the v=1 ceiling before it clears. None when neither direction reaches the
    bar — a colour too close to its background at every brightness, which is
    when the plain foreground really is the only honest answer.
    """
    channels = _channels(candidate)
    if channels is None:
        return None

    hue, saturation, value = colorsys.rgb_to_hsv(*channels)

    def at(v: float) -> str:
        r, g, b = colorsys.hsv_to_rgb(hue, saturation, v)
        return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))

    best: str | None = None
    for lo, hi in ((0.0, value), (value, 1.0)):
        if (contrast(at(hi if hi != value else lo), background) or 0) < MIN_CONTRAST:
            continue
        low, high = lo, hi
        for _ in range(16):
            mid = (low + high) / 2
            if (contrast(at(mid), background) or 0) >= MIN_CONTRAST:
                low, high = (mid, high) if hi == value else (low, mid)
            else:
                low, high = (low, mid) if hi == value else (mid, high)
        found = at(low if hi == value else high)
        if best is None or abs(colorsys.rgb_to_hsv(*_channels(found))[2] - value) < \
                abs(colorsys.rgb_to_hsv(*_channels(best))[2] - value):
            best = found
    return best


def _legible(candidate: str, background: str, fallback: str) -> str:
    """`candidate`, made readable against `background`, else `fallback`.

    Unknown values pass through untouched — a CSS variable reference has no hex
    to measure, and rejecting it would throw away the fallback palette.

    A rejected colour is first offered back at a different brightness rather
    than replaced outright, which is what keeps a theme looking like itself.
    Substituting the foreground was the original behaviour and it costs more
    than it looks: measured over the 22 stock v4 themes it fired on 1.0 of the
    4 text roles per dark theme and 1.6 per light one, and catppuccin-latte lost
    three of four — arriving as two colours instead of four, on the one desktop
    whose whole pitch is that everything matches.
    """
    ratio = contrast(candidate, background)
    if ratio is None or ratio >= MIN_CONTRAST:
        return candidate
    return _shift_to_contrast(candidate, background) or fallback


#: Keys that only ever appear in an Omarchy 4 palette. `selection` and `muted`
#: are new words in v4 (v3 spelled its selection as a *pair*), and every stock
#: v4 theme defines all three — so any one of them is enough to identify the
#: dialect without depending on a single key surviving future edits.
_V4_MARKERS = ("muted", "selection", "bright_foreground")


def _is_v4(raw: dict) -> bool:
    """Whether `colors.toml` speaks the Omarchy 4 semantic dialect.

    Checked before the numbered slots because Omarchy's own resolver gives
    canonical names precedence when a theme somehow defines both, and a
    hand-written theme carrying a legacy block alongside the new keys should
    be read the same way Omarchy reads it.
    """
    return any(key in raw for key in _V4_MARKERS)


def _read_theme(theme_dir: Path | None = None) -> dict | None:
    """The active theme's parsed `colors.toml`, or None if there is not one.

    With no directory given, every candidate from `theme_dirs()` is tried in
    order and the first one that parses wins; a directory passed explicitly is
    the only one consulted. A malformed file falls through to the next candidate
    rather than ending the search, so a half-written v4 theme does not hide a
    working v3 one during an upgrade.
    """
    for candidate in ([theme_dir] if theme_dir is not None else theme_dirs()):
        try:
            with (candidate / "colors.toml").open("rb") as f:
                return tomllib.load(f)
        except (OSError, ValueError):
            continue
    return None


def load_palette(theme_dir: Path | None = None) -> Palette:
    """Read `colors.toml` and map its keys onto this TUI's roles.

    Either dialect is accepted from either location, since a v3 theme kept by
    hand still resolves after an upgrade.
    """
    raw = _read_theme(theme_dir)
    if raw is None:
        return Palette.fallback()

    fb = Palette.fallback()

    def key(name: str, default: str) -> str:
        value = raw.get(name)
        return value if isinstance(value, str) and _channels(value) else default

    background = key("background", fb.background)
    foreground = key("foreground", fb.foreground)

    def text(name: str, default: str) -> str:
        return _legible(key(name, default), background, foreground)

    if _is_v4(raw):
        # v4 names the roles this TUI wanted all along: `accent` is a real key
        # rather than a slot borrowed for the purpose, `muted` is documented as
        # the de-emphasised role (and as the ANSI color8 stand-in, which is
        # exactly what the footer used before), and `selection` is the
        # text-selection background — paired by Omarchy with `bright_foreground`
        # as its text, which is the pairing followed here rather than reinvented.
        #
        # Expect roles to resolve to the plain foreground sometimes, and do not
        # read that as a broken mapping. Measured across all 22 stock v4 themes:
        #
        #   footer  `muted` clears MIN_CONTRAST on only three of them (median
        #           ratio 2.25). It is *designed* to sit low against the
        #           background, so the guard fires for the same reason it fired
        #           on v3's `color8`.
        #   frame,  the named colours are bright, which is a light theme's
        #   header  problem: on catppuccin-latte both `green` (2.98) and
        #           `yellow` (2.2) miss the bar and the panel comes back in two
        #           colours instead of four. Dark themes lose 1.0 of 4 roles on
        #           average, light themes 1.6.
        #
        # Keeping the guard means every label is readable on every theme, which
        # is worth more than a themed but unreadable one. Making light themes
        # look *themed* rather than merely legible — a fallback that prefers
        # `accent` over the foreground, say — is a colour-policy change, and
        # belongs with the theme-following work rather than in a compatibility
        # pass. See docs/omarchy-4-plan.md.
        select_bg = key("selection", fb.select_bg)
        return Palette(
            background=background,
            foreground=foreground,
            frame=text("green", fb.frame),
            header=text("yellow", fb.header),
            select_bg=select_bg,
            select_fg=_legible(key("bright_foreground", foreground),
                               select_bg,
                               _most_legible(select_bg, foreground, background)),
            accent=text("accent", fb.accent),
            footer=text("muted", foreground),
        )

    # v3: the 16 ANSI slots. The choices follow the roles the design asks for —
    # a list panel framed in the theme's green with yellow headers, a settings
    # panel in a single warm accent — but nothing here depends on those hues
    # being green or warm. A theme that makes `color2` blue gets a blue frame.
    #
    # The selected row is the one place a slot is used as a *background*, so it
    # is paired with the theme background as its text colour rather than the
    # foreground: every v3 theme sampled puts its bright slots and its
    # background at opposite ends, which is the contrast wanted here. Light
    # themes are the case that can break it, so the pairing is checked rather
    # than assumed.
    select_bg = key("color12", fb.select_bg)
    return Palette(
        background=background,
        foreground=foreground,
        frame=text("color2", fb.frame),
        header=text("color3", fb.header),
        select_bg=select_bg,
        select_fg=_legible(background, select_bg, foreground),
        accent=text("color7", fb.accent),
        footer=text("color8", foreground),
    )


# --------------------------------------------------------------------------
# LED colours
#
# A separate vocabulary from the Palette above, because the two are judged
# against completely different constraints. Palette roles are *text* on a known
# background and go through a contrast guard. These are light in a dark room:
# there is no background to contrast against, nothing has to be readable, and
# the only real failure is a colour so dim the strip looks off.
# --------------------------------------------------------------------------

#: Theme keys offered as LED colours, in the order the TUI cycles them.
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

#: Where to look for a role in a v3 palette, which had no semantic names beyond
#: `accent` and used the ANSI slots for hues. `orange` has no slot of its own in
#: a 16-colour palette and borrows yellow — the closest thing available, not a
#: claim that they are the same colour.
#:
#: Consulted only after the role's own name misses, so it never overrides a real
#: key. That ordering is what makes `accent` work on both dialects without a
#: dialect test: v3 defined `accent` literally *and* had slots, so keying off the
#: dialect would send v3 to `color4` and quietly ignore the theme's own accent.
_V3_LED_SLOTS = {
    "red": "color1", "orange": "color3", "yellow": "color3",
    "green": "color2", "cyan": "color6", "blue": "color4",
    "magenta": "color5", "accent": "color4",
}


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
        value = raw.get(_V3_LED_SLOTS.get(role, ""))
    if not isinstance(value, str):
        return None

    channels = _channels(value)
    if channels is None:
        return None
    return _lift(tuple(round(c * 255) for c in channels))  # type: ignore[arg-type]


@lru_cache(maxsize=1)
def palette() -> Palette:
    """The active palette, read once per process.

    Cached because it is consumed on every panel repaint, and read at startup
    rather than watched: a theme switch is picked up the next time the TUI opens,
    which is how the rest of Omarchy's terminal utilities behave too.
    """
    return load_palette()
