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


def _legible(candidate: str, background: str, fallback: str) -> str:
    """`candidate` if it can be read against `background`, else `fallback`.

    Unknown values pass through untouched — a CSS variable reference has no hex
    to measure, and rejecting it would throw away the fallback palette.
    """
    ratio = contrast(candidate, background)
    if ratio is None or ratio >= MIN_CONTRAST:
        return candidate
    return fallback


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


def load_palette(theme_dir: Path | None = None) -> Palette:
    """Read `colors.toml` and map its keys onto this TUI's roles.

    With no directory given, every candidate from `theme_dirs()` is tried in
    order and the first one that parses wins; a directory passed explicitly is
    the only one consulted. Either dialect is accepted from either location,
    since a v3 theme kept by hand still resolves after an upgrade.
    """
    candidates = [theme_dir] if theme_dir is not None else theme_dirs()

    raw: dict | None = None
    for candidate in candidates:
        try:
            with (candidate / "colors.toml").open("rb") as f:
                raw = tomllib.load(f)
            break
        except (OSError, ValueError):
            continue
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


@lru_cache(maxsize=1)
def palette() -> Palette:
    """The active palette, read once per process.

    Cached because it is consumed on every panel repaint, and read at startup
    rather than watched: a theme switch is picked up the next time the TUI opens,
    which is how the rest of Omarchy's terminal utilities behave too.
    """
    return load_palette()
