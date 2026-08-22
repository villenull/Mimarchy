"""Mimarchy — single-screen, keyboard-driven TUI for the lighting and the panel.

Two boxes rather than five. The earlier layout gave Effects, Options, Sensors and
Cooler display a bordered panel each, which spread four lines of content over
twelve rows of chrome and made the thing you actually manipulate — the zone list —
the smallest box on screen. Here a dominant Lights table sits above one Controls
box that acts on whichever row is selected, with sensors and display status folded
into its last line.

Nothing here drives hardware. The TUI writes desired state to a small JSON file
and `mimarchy-lightd` renders it. That indirection is what makes the two
controllers agree: rendering both from one clock keeps them in phase, gives speed
control the board's firmware does not expose, and removes hardware mode switching
— which was slow enough that cycling modes quickly left the GPU a mode behind.

Every colour comes from the user's Omarchy theme (see `theme.py`). There are no
hex values in this file, deliberately: the tool is launched the same way Omarchy
launches `bluetui`, and it should re-theme with everything else rather than
carrying a palette of its own.
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.reactive import reactive
from textual.widgets import DataTable, Static

from mimarchy import lightstate
from mimarchy.config import load_config
from mimarchy.effects import (COLOUR_EFFECTS, EFFECTS, SPATIAL_EFFECTS,
                                SPEED_LEVELS, nearest_speed)
from mimarchy.hwmon import read_cpu_fan_rpm, read_cpu_temp, read_gpu_temp
from mimarchy.service import DISPLAY_UNIT, set_unit, unit_active
from mimarchy.theme import led_colour, palette

PALETTE: list[tuple[str, tuple[int, int, int]]] = [
    ("white", (255, 255, 255)),
    ("red", (255, 0, 0)),
    ("orange", (255, 90, 0)),
    ("green", (0, 255, 60)),
    ("cyan", (0, 200, 255)),
    ("blue", (30, 100, 255)),
    ("magenta", (255, 0, 160)),
]

#: Fixed swatches by name, for the cycle below.
PALETTE_BY_NAME: dict[str, tuple[int, int, int]] = dict(PALETTE)

#: The colour cycle a repeat effect-key press walks. Entries carrying a role
#: resolve from the active Omarchy theme each time they are selected; entries
#: with `None` are the fixed swatches above.
#:
#: One theme entry rather than all eight LED roles: the cycle is walked by
#: repeated presses of a single key, so every entry added is a keypress taxed on
#: everyone reaching the ones after it. `accent` is the one that means "match my
#: desktop", which is the whole request. The remaining roles are reachable from
#: `mimarchy-ctl colour <role>` for anyone who wants a specific hue.
#:
#: Theme first, so the very first press of a repeat lands on the theme colour —
#: the behaviour worth discovering by accident.
COLOUR_CYCLE: list[tuple[str, str | None]] = (
    [("theme", "accent")] + [(name, None) for name, _ in PALETTE]
)

#: Row labels. Linked collapses to one row naming both devices; unlinked names
#: them individually, so the split is legible at a glance rather than inferred
#: from the row count.
LINKED_LABEL = "CPU + GPU"
LABELS = {"cpu_fans": "cpu fans", "gpu": "gpu"}

#: Width of the label gutter in the Controls panel, so `effect` / `color` /
#: `speed` and the wrapped continuation line all align.
GUTTER = 8


def numbered_effects() -> list[str]:
    """The effects bound to number keys, in key order.

    Single source of truth for the legend and the key handler, which drifted
    apart once before and put a legend on screen that disagreed with the keys.
    """
    return [e for e in EFFECTS if e != "off"]


class Panel(Vertical):
    """A bordered box with a title."""

    def __init__(self, title: str, *children, **kw) -> None:
        super().__init__(*children, **kw)
        self.border_title = title


class MimarchyApp(App):
    TITLE = "Mimarchy"

    CSS = """
    Screen { layout: vertical; padding: 0 1; background: $mim-background; }

    /* `1fr`, not a row count: Lights takes every row Controls and the hint bar
       do not need, so the window has no dead space at any size. It used to be
       `height: auto`, which hugged its two data rows and left most of an 875x600
       window empty. */
    #lights {
        border: round $mim-frame; height: 1fr; min-height: 6;
        padding: 0 1; margin-bottom: 1;
    }
    #lights { border-title-color: $mim-frame; }

    /* Same frame colour as Lights. bluetui draws both of its panels in one
       colour and reserves the brighter tones for headers and the cursor row; a
       second border colour here read as two unrelated widgets rather than two
       panes of one screen. The accent still marks the *active* value inside. */
    #controls {
        border: round $mim-frame; height: auto; padding: 0 1;
    }
    #controls { border-title-color: $mim-frame; }

    DataTable { height: auto; background: $mim-background; }
    DataTable > .datatable--header {
        background: $mim-background; color: $mim-header; text-style: bold;
    }
    DataTable > .datatable--cursor {
        background: $mim-select-bg; color: $mim-select-fg;
    }

    #control-body { color: $mim-foreground; }
    #hints { color: $mim-footer; padding: 0 1; }
    """

    #: No quit binding, by design — this is an overlay launched from the status
    #: bar, closed by closing its window, exactly like `bluetui`. Textual's own
    #: ctrl+c still works as an escape hatch and is not shadowed here.
    #:
    #: Digits pick effects, left/right move speed, up/down move the selection.
    #: `-`/`+` are kept as speed aliases for the numpad.
    #:
    #: `priority=True` on all of them is load-bearing, not defensive. The Lights
    #: table holds focus, and a focused widget's bindings are matched before the
    #: app's — DataTable binds `enter` to "select row", so without priority the
    #: link toggle silently did nothing at all. Up/down are deliberately *not*
    #: bound here and left to the table, which already moves its own cursor;
    #: binding them too would mean two handlers for one keypress.
    BINDINGS = [
        Binding("1", "effect(0)", "", priority=True),
        Binding("2", "effect(1)", "", priority=True),
        Binding("3", "effect(2)", "", priority=True),
        Binding("4", "effect(3)", "", priority=True),
        Binding("5", "effect(4)", "", priority=True),
        Binding("6", "effect(5)", "", priority=True),
        Binding("0", "effect_off", "", priority=True),
        # Left/right for speed, which is where it was before the numpad-only
        # scheme moved it to -/+. Those are kept as aliases rather than removed:
        # they cost nothing, they collide with nothing, and left/right are free
        # because the table only claims up/down for its cursor. `+` usually
        # arrives as shift+equals, so the unshifted key is accepted too.
        Binding("left,minus,underscore", "adjust(-1)", "slower", priority=True),
        Binding("right,plus,equals_sign", "adjust(1)", "faster", priority=True),
        # `enter` and `slash` stay as aliases for the keys they used to be; the
        # hint bar advertises the letters.
        Binding("u,enter", "toggle_link", "link/unlink", priority=True),
        Binding("d,slash", "toggle_display", "display", priority=True),
    ]

    linked: reactive[bool] = reactive(True)

    def __init__(self) -> None:
        self._palette = palette()
        super().__init__()
        self.config = load_config()
        self.state = lightstate.load()
        self.linked = self.state.linked
        #: One line of feedback for actions whose result is otherwise invisible —
        #: a sync that could not carry colour, a key that does not apply here.
        #: Rendered in the Controls panel rather than as a toast: a toast covers
        #: the panel it is describing, and this screen is only ten rows tall.
        self._message = ""
        self._snap_speeds()

    def get_css_variables(self) -> dict[str, str]:
        """Expose the theme's roles to CSS as `$mim-<role>`."""
        return {**super().get_css_variables(), **self._palette.css_variables()}

    # ---- layout ----------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Panel("Lights", id="lights"):
            yield DataTable(id="targets", cursor_type="row", zebra_stripes=False)
        with Panel("Controls", id="controls"):
            yield Static(id="control-body")
        yield Static(id="hints")

    def on_mount(self) -> None:
        table = self.query_one("#targets", DataTable)
        table.add_columns("target", "effect", "color", "speed", "")
        self.refresh_all()
        self.set_interval(3.0, self.refresh_controls)

    # ---- state -----------------------------------------------------------

    @property
    def targets(self) -> list[str]:
        """One row per configured zone, the linked pair collapsed into one.

        Driven by `config.zones` rather than by a fixed pair: a third
        `[rgb.zones.*]` block is one more row, and the daemon, `mimarchy-ctl`
        and the bar widget all already render it — the TUI was the last place
        still drawing exactly two. The link stays *defined* as `cpu_fans` +
        `gpu`, so every other zone is always its own row.
        """
        keys = list(self.config.zones) or ["cpu_fans", "gpu"]
        if not (self.linked and {"cpu_fans", "gpu"} <= set(keys)):
            return keys
        return ["cpu_fans+gpu" if k == "cpu_fans" else k
                for k in keys if k != "gpu"]

    def _members(self, target: str) -> list[str]:
        return target.split("+") if "+" in target else [target]

    @property
    def selected(self) -> str:
        table = self.query_one("#targets", DataTable)
        rows = self.targets
        return rows[min(table.cursor_row, len(rows) - 1)]

    def _state_key(self, target: str) -> str:
        """Linked targets share one entry, so both devices stay identical."""
        return self._members(target)[0]

    def _snap_speeds(self) -> None:
        """Move any stored speed onto a stop, once, at startup.

        The ladder's top came down from 4.0 to 1.0, so state written before that
        holds values with no stop to light — they would show as 2.5 in the table
        while the ladder highlighted nothing. Writing the snapped value back keeps
        the two readings of the same number honest.
        """
        changed = False
        for target in self.state.targets.values():
            snapped = _nearest_speed(target.speed)
            if snapped != target.speed:
                target.speed, changed = snapped, True
        if changed:
            lightstate.save(self.state)

    def _commit(self) -> None:
        self.state.linked = self.linked
        lightstate.save(self.state)

    def _on_firmware(self, target: str, effect: str | None = None) -> bool:
        """Whether this row's GPU is being animated by the card's own firmware.

        Mirrors `lightd.plan`'s routing rule. Rainbow and chase are patterns in
        space, and the GPU exposes one controllable LED for a bar with many
        segments, so rendering them there collapses to a flat colour — the card's
        Rainbow Wave and Runway do it properly instead.

        The link blocks that hand-off only for effects carrying a colour, since
        every firmware mode here reports `color_mode=0` and ignores one: linked
        chase stays rendered rather than running yellow on the bar against red on
        the strip. Rainbow has no chosen colour, and the card's wheel is the same
        wheel, so it goes to firmware whether linked or not.

        Two of `plan`'s conditions are taken as given rather than checked, since
        checking them would mean opening an OpenRGB connection from a TUI that
        deliberately touches no hardware: that the GPU zone has one controllable
        LED, and that the card offers a firmware mode for these effects. Both are
        true of this card. If either stopped being true the tag would be wrong,
        which costs a misleading label and nothing else — the routing itself is
        `lightd`'s decision, not this one.
        """
        if "gpu" not in self._members(target):
            return False
        effect = effect if effect is not None else \
            self.state.for_target(self._state_key(target)).effect
        if effect not in SPATIAL_EFFECTS:
            return False
        # The link only blocks the hand-off for effects that carry a colour the
        # card would ignore. Chase does; rainbow does not, because the card's own
        # wheel is the same wheel.
        return not (self.linked and effect in COLOUR_EFFECTS)

    # ---- rendering -------------------------------------------------------

    def refresh_all(self) -> None:
        self.refresh_targets()
        self.refresh_controls()
        self.refresh_hints()

    def refresh_hints(self) -> None:
        # One colour for keys and descriptions alike (set in CSS); bold marks the
        # keys without splitting the line into two colours.
        self.query_one("#hints", Static).update(
            "[b]1-6,0[/b] effect (again: color)  │  [b]←,→[/b] speed  │  "
            "[b]↑,↓[/b] select  │  [b]u[/b] link/unlink  │  [b]d[/b] display"
        )

    def refresh_targets(self) -> None:
        table = self.query_one("#targets", DataTable)
        cursor = table.cursor_row
        table.clear()
        for target in self.targets:
            st = self.state.for_target(self._state_key(target))
            label = (LINKED_LABEL if len(self._members(target)) > 1
                     else LABELS.get(target, target))
            colour = (_colour_name(st.colour, st.colour_role)
                      if st.effect in COLOUR_EFFECTS else "—")
            speed = "—" if st.effect in ("static", "off") else f"{st.speed:.1f}"
            notes = []
            if len(self._members(target)) > 1:
                notes.append("(linked)")
            if self._on_firmware(target):
                # Escaped, because table cells are parsed as markup and
                # `[firmware]` is indistinguishable from a style tag — it was
                # silently swallowed, leaving the row untagged while the state
                # said otherwise. The brackets are wanted in the output, so the
                # escape is the fix rather than a different label.
                notes.append(r"\[firmware]")
            table.add_row(label, st.effect, colour, speed, "  ".join(notes))
        if table.row_count:
            table.move_cursor(row=min(cursor, table.row_count - 1))

    def refresh_controls(self) -> None:
        """The whole Controls panel: three option rows, then a status line.

        Every value is on screen at all times with the current one lit, rather
        than only the current one being shown. A filled bar or a lone number could
        say "about here", which is no help when the question is which stop you are
        on and what the alternatives are.
        """
        pal = self._palette
        st = self.state.for_target(self._state_key(self.selected))
        firmware = self._on_firmware(self.selected)

        def lit(text: str, on: bool) -> str:
            return (f"[b {pal.accent}]{text}[/]" if on
                    else f"[{pal.foreground}]{text}[/]")

        def label(text: str) -> str:
            return f"[{pal.accent}]{text:<{GUTTER}}[/]"

        numbered = numbered_effects()
        keys = [f"{i + 1} {e}" for i, e in enumerate(numbered)] + ["0 off"]
        marks = [e == st.effect for e in numbered] + [st.effect == "off"]
        # One row. It used to wrap after five, which put `6 unhinged` and `0 off`
        # on a second line and made them look like a separate category. The
        # "press again for colour" note that shared that line is in the hint bar
        # already, so dropping it costs nothing.
        lines = [label("effect") + "  ".join(lit(k, m)
                                             for k, m in zip(keys, marks))]

        # Colour applies only to effects that take one, and never to a zone the
        # firmware is animating — its modes all report `color_mode=0` and reject a
        # colour outright. Saying so beats letting the key quietly do nothing.
        colour_live = st.effect in COLOUR_EFFECTS and not firmware
        # Marked by role where there is one, so the theme entry lights up even
        # when its resolved accent happens to equal one of the fixed swatches.
        swatch = "  ".join(
            lit(name, colour_live and (
                st.colour_role == role if role is not None
                else st.colour_role is None
                and PALETTE_BY_NAME[name] == tuple(st.colour)))
            for name, role in COLOUR_CYCLE)
        if not colour_live:
            # "the card picks its own" is only true where the effect *has* a
            # colour that the firmware then ignores — chase. For rainbow the
            # colour row is dead because rainbow has no chosen colour at all, same
            # as spectrum, and saying otherwise implies a mismatch that is not
            # there: the card's wheel is the same wheel.
            why = ("gpu firmware picks its own"
                   if firmware and st.effect in COLOUR_EFFECTS
                   else f"{st.effect} has no color")
            swatch = (f"[{pal.footer}]"
                      + "  ".join(name for name, _ in COLOUR_CYCLE)
                      + f"    ({why})[/]")
        lines.append(label("color") + swatch)

        if st.effect in ("static", "off"):
            lines.append(label("speed")
                         + f"[{pal.footer}]"
                         + "  ".join(f"{s:.1f}" for s in SPEED_LEVELS)
                         + f"    ({st.effect} has no speed)[/]")
        else:
            current = _nearest_speed(st.speed)
            lines.append(label("speed") + "  ".join(
                lit(f"{s:.1f}", s == current) for s in SPEED_LEVELS))

        lines.append("")
        lines.append(self._status_line())
        if self._message:
            lines.append(f"[{pal.accent}]{self._message}[/]")
        self.query_one("#control-body", Static).update("\n".join(lines))

    def _status_line(self) -> str:
        pal = self._palette
        try:
            cpu, gpu, fan = read_cpu_temp(), read_gpu_temp(), read_cpu_fan_rpm()
            sensors = (f"cpu {_fmt(cpu, '°C')}   gpu {_fmt(gpu, '°C')}"
                       f"   cpu fan {_fmt(fan, 'rpm', 0)}")
        except Exception as exc:  # noqa: BLE001 — surfaced, not swallowed
            sensors = f"sensors unavailable: {exc}"
        on = _unit_active(DISPLAY_UNIT)
        # "off" means the telemetry stream stopped, not that the panel went dark:
        # the firmware holds its last frame and blanks on its own timer roughly a
        # minute later. Saying "off" without that is the single most confusing
        # thing this tool can claim, because the panel is visibly still lit.
        state = ("on" if on else
                 f"off [{pal.footer}](panel blanks ~50 s after the last frame)[/]")
        return (f"[{pal.foreground}]{sensors}[/]   "
                f"[{pal.accent}]display:[/] [{pal.foreground}]{state}[/]")

    # ---- actions ---------------------------------------------------------

    def _after_change(self) -> None:
        self._commit()
        self.refresh_targets()
        self.refresh_controls()

    def on_data_table_row_highlighted(self) -> None:
        """Follow the table's own cursor instead of moving it.

        The Controls panel acts on the selected row, so it has to repaint whenever
        the selection changes — and the arrow keys belong to the focused table, not
        to this app. Reacting to the table's message covers every way the cursor
        can move (keys, mouse, a programmatic jump after a relink) with one hook.

        Deliberately does *not* clear `_message`. Rebuilding the table moves its
        cursor, which posts this message asynchronously — so clearing here wiped
        every message the action that triggered the rebuild had just set, and
        `sync` reported nothing at all. Messages are cleared when the next action
        starts instead, which is the only point where "the user has moved on" is
        actually known.
        """
        self.refresh_controls()

    def action_toggle_link(self) -> None:
        self._message = ""
        self.linked = not self.linked
        if not self.linked and {"cpu_fans", "gpu"} <= set(self.config.zones or
                                                          {"cpu_fans", "gpu"}):
            # Splitting: seed the GPU from the shared entry so it starts where it
            # visibly was, rather than jumping to a default. Guarded on both
            # zones existing, so a rig configured without a `gpu` zone does not
            # get a phantom target conjured out of the split.
            #
            # `colour_role` travels with the colour: seeding the resolved RGB
            # alone would leave a GPU that looked identical but had quietly
            # stopped following the theme, and the difference would only show up
            # at the next theme switch.
            shared = self.state.for_target("cpu_fans")
            gpu = self.state.for_target("gpu")
            gpu.effect, gpu.colour, gpu.speed, gpu.colour_role = (
                shared.effect, shared.colour, shared.speed, shared.colour_role)
        self.config.save_link_state(self.linked)
        self._after_change()
        self.refresh_hints()

    def action_effect(self, index: int) -> None:
        numbered = numbered_effects()
        if index < len(numbered):
            self._set_effect(numbered[index])

    def action_effect_off(self) -> None:
        self._set_effect("off")

    def _set_effect(self, effect: str) -> None:
        """A number key picks its effect; pressing it again advances the colour.

        Colour used to sit on its own key, which meant a second control for
        something that only ever applies to the effect just chosen — and one that
        did nothing at all for most effects. Folding it into the key already under
        the finger keeps the palette one keystroke away and shortens the hint bar.
        Effects that pick their own hues do nothing on a repeat press, which the
        Controls panel says rather than leaving to be discovered.
        """
        self._message = ""
        st = self.state.for_target(self._state_key(self.selected))
        if st.effect != effect:
            st.effect = effect
        elif effect in COLOUR_EFFECTS and not self._on_firmware(self.selected):
            _advance_colour(st)
        else:
            return
        self._after_change()

    def action_adjust(self, step: int) -> None:
        self._message = ""
        st = self.state.for_target(self._state_key(self.selected))
        if st.effect in ("static", "off"):
            self._message = f"{st.effect} has no speed"
            self.refresh_controls()
            return
        i = SPEED_LEVELS.index(_nearest_speed(st.speed))
        st.speed = SPEED_LEVELS[max(0, min(len(SPEED_LEVELS) - 1, i + step))]
        self._after_change()

    def action_toggle_display(self) -> None:
        self._message = ""
        self._message = set_unit(DISPLAY_UNIT, not _unit_active(DISPLAY_UNIT)) or ""
        self.refresh_controls()


#: Aliased rather than imported under their own names so the module-level
#: symbols the tests patch (`tui._unit_active`) keep working, and so the calls
#: below read the same as they always did. The implementations live in
#: `service` and `effects` because `mimarchy-ctl` drives the same units and
#: walks the same ladder, and two copies would eventually disagree.
_unit_active = unit_active
_nearest_speed = nearest_speed


def _advance_colour(st) -> None:
    """Step to the next entry in the colour cycle.

    Located by *role* first and by RGB only as a fallback, because the theme
    entry has no fixed value to match on — its resolved colour is whatever the
    current theme's accent happens to be, and on some themes that will coincide
    with one of the fixed swatches. Matching on role keeps "which entry is
    selected" a question about intent rather than about the current hue.
    """
    index = 0
    if st.colour_role:
        index = next((i for i, (_, role) in enumerate(COLOUR_CYCLE)
                      if role == st.colour_role), 0)
    else:
        index = next((i for i, (_, role) in enumerate(COLOUR_CYCLE)
                      if role is None and PALETTE_BY_NAME.get(COLOUR_CYCLE[i][0])
                      == tuple(st.colour)), -1)

    for step in range(1, len(COLOUR_CYCLE) + 1):
        name, role = COLOUR_CYCLE[(index + step) % len(COLOUR_CYCLE)]
        if role is None:
            st.colour, st.colour_role = PALETTE_BY_NAME[name], None
            return
        rgb = led_colour(role)
        # A theme that does not define the role, or no theme at all, means the
        # entry simply is not available right now — so it is stepped over rather
        # than selected into a colour that would not change. Skipping keeps the
        # key responsive on a machine with no Omarchy instead of appearing dead.
        if rgb is not None:
            st.colour, st.colour_role = rgb, role
            return


def _colour_name(colour, role: str | None = None) -> str:
    if role:
        return f"theme {role}"
    for name, rgb in PALETTE:
        if tuple(colour) == rgb:
            return name
    r, g, b = colour
    return f"#{r:02x}{g:02x}{b:02x}"


def _fmt(value: float | None, unit: str, places: int = 1) -> str:
    return "—" if value is None else f"{value:.{places}f} {unit}"


def main() -> None:
    MimarchyApp().run()


if __name__ == "__main__":
    main()
