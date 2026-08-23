"""Checks on the bar widget's QML that a symbol-resolution pass cannot make.

Every type and property this widget instantiates was verified against the
Omarchy 4 shell source before it was ever loaded, and all of it resolved. It
still did not draw: the root bound no implicit size, the `BarIconButton`
anchored itself to that zero-sized root, and the bar allocated it a 0x0 slot.
Nothing was misspelled, no QML error was logged, `omarchy plugin validate`
passed, and `omarchy plugin list` reported the widget as enabled and in the
bar — it was simply invisible.

That is the gap these tests cover. They are not a QML engine and cannot tell
whether the widget looks right; they assert the two or three structural facts
whose absence is silent, which is the property that made the sizing bug cost a
session to find rather than a run of the suite.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
WIDGET = REPO / "quickshell" / "Panel.qml"

#: Bindings written at the root object's own indentation, which in this file is
#: two spaces. Anything deeper belongs to a nested item and says nothing about
#: what size the bar will give the widget.
ROOT_BINDING = "^  {name}:"


@pytest.fixture(scope="module")
def source() -> str:
    return WIDGET.read_text()


def _has_root_binding(source: str, name: str) -> bool:
    return re.search(ROOT_BINDING.format(name=re.escape(name)), source,
                     re.MULTILINE) is not None


@pytest.mark.parametrize("binding", ["implicitWidth", "implicitHeight"])
def test_root_declares_an_implicit_size(source: str, binding: str) -> None:
    """The bar sizes a widget from its root's implicit size.

    `Ui/Panel` is an `Item` and brings none of its own, so without this the
    widget measures 0x0 and is laid out but never drawn. Every first-party bar
    widget binds both — `bluetooth/Panel.qml` takes them from its button, which
    is where this one now takes them too.
    """
    assert _has_root_binding(source, binding), (
        f"the root object binds no {binding}, so the bar will lay this widget "
        f"out at zero size and it will not be drawn"
    )


def test_the_anchored_button_is_what_the_root_is_sized_from(source: str) -> None:
    """The two halves of the sizing contract have to name the same object.

    `anchors.fill: parent` makes the button take the root's size, and the root
    takes its size from the button — which only terminates because the button's
    own implicit size comes from `fixedWidth`/`fixedHeight` on `BarIconButton`,
    not from its children. Binding the root to some *other* id would leave the
    button filling a size unrelated to the icon it draws.
    """
    button_id = re.search(r"BarIconButton\s*\{\s*\n\s*id:\s*(\w+)", source)
    assert button_id, "no BarIconButton with an id — the bar icon is the widget"
    name = button_id.group(1)

    for binding in ("implicitWidth", "implicitHeight"):
        match = re.search(rf"^  {binding}:\s*(.+)$", source, re.MULTILINE)
        assert match, f"no root {binding}"
        assert match.group(1).strip().startswith(f"{name}."), (
            f"root {binding} is bound to {match.group(1).strip()!r} rather than "
            f"to the {name} it anchors, so the icon's size and the slot the bar "
            f"reserves for it can drift apart"
        )


def test_the_button_fills_the_root(source: str) -> None:
    """Stated so that removing it has to be deliberate.

    With the root sized from the button and the button anchored to the root,
    either half alone is wrong: drop the anchor and the button keeps its own
    size while the root reports it, which happens to work; drop the root
    binding and nothing is drawn at all.
    """
    assert re.search(r"BarIconButton\s*\{(?:[^{}]|\{[^{}]*\})*anchors\.fill:\s*parent",
                     source), "the bar icon no longer fills the widget root"


def test_a_backend_that_never_starts_is_detected(source: str) -> None:
    """`onExited` alone cannot see a missing `mimarchy-ctl`.

    Quickshell does not run the command through a shell, so there is no exit
    127 to catch: a binary that is not on PATH emits neither `started` nor
    `exited`. All it produces is `running` dropping back to false with a null
    processId, which is why the flag has to be raised from `onRunningChanged`
    and disambiguated by whether `started` ever fired.

    Getting this wrong is not a blank panel — it falls through to "Lighting
    daemon stopped", which points at `systemctl` for a program that was never
    installed. That is worth a test precisely because both spellings look
    right.
    """
    assert "onStarted:" in source, (
        "nothing records that a poll actually started, so a command that is "
        "missing cannot be told apart from one that ran"
    )
    assert re.search(r"onRunningChanged:\s*\{", source), (
        "backendMissing is never raised for a command that fails to launch"
    )
    assert re.search(r"onRunningChanged:\s*\{[^}]*backendMissing\s*=\s*true", source,
                     re.S), (
        "onRunningChanged does not set backendMissing — the missing-backend "
        "message stays unreachable"
    )


def test_the_state_file_is_still_watched(source: str) -> None:
    """The half of the state story that does not poll.

    A TUI keypress writes the lighting state atomically; the widget follows it
    because a `FileView` with `watchChanges` re-runs `refresh()`. Lose the
    watch and the panel still works — it just goes stale for up to
    `pollIntervalSec`, which is exactly the kind of regression that reads as
    "the bar is a bit laggy" rather than as a bug with a cause.
    """
    watch = re.search(r"FileView\s*\{(?:[^{}]|\{[^{}]*\})*\}", source, re.S)
    assert watch, "the lighting state file is no longer watched at all"
    body = watch.group(0)
    assert "watchChanges: true" in body, "the FileView no longer watches for changes"
    assert "root.refresh()" in body, (
        "a state-file change no longer re-reads status, so TUI-side changes "
        "will not reach the bar until the next poll"
    )


# --------------------------------------------------------------------------
# The rewritten body. Same rule as above: only facts whose absence is silent.
# A wrong `--zone`, a missing hide rule or an animation that never stops all
# render perfectly and are wrong, which is the property that earns a test.
# --------------------------------------------------------------------------

def test_linked_issues_commands_without_a_zone(source: str) -> None:
    """Linked is the every-target default, not a loop over the zones.

    `zoneArgs` returning `[]` is what makes a linked click land on every
    zone's stored state at once, so unlinking afterwards shows what was on
    screen rather than two zones that were never written to. Returning
    `["--zone", <first key>]` instead would look identical in the panel and
    leave the other zones untouched until something else moved them.
    """
    fn = re.search(r"function zoneArgs\(block\)\s*\{(.*?)\n  \}", source, re.S)
    assert fn, "zoneArgs is gone — nothing decides whether --zone is passed"
    assert re.search(r'block\.key\s*!==\s*""', fn.group(1)), (
        "zoneArgs no longer treats an empty key as 'omit --zone', so the "
        "linked block stops writing every zone"
    )
    assert "[]" in fn.group(1), (
        "zoneArgs has no empty-argument branch, so every command now names a "
        "single zone and the linked block updates only one of them"
    )


def test_speed_is_set_absolutely_rather_than_stepped(source: str) -> None:
    """Clicking the fourth stop has to land on four.

    `mimarchy-ctl speed +` was the only speed verb the panel had, and it is
    still the right one for the +/- keys and the icon's wheel. Wiring the
    stops to it as well would make a click on stop 4 move one notch — a
    control that visibly disagrees with the click that drove it.
    """
    assert re.search(r'"speed",\s*"set",\s*String\(index \+ 1\)', source), (
        "the speed stops no longer issue an absolute `speed set N`"
    )
    for relative in ('"speed", "+"', '"speed", "-"'):
        assert relative in source, (
            f"the global {relative} shortcut is gone; +/- and the icon wheel "
            f"are the coarse every-zone path and were meant to survive"
        )


def test_colour_and_speed_rows_hide_on_the_backends_word(source: str) -> None:
    """`takes_colour`/`takes_speed` are computed in `ctl.cmd_status`.

    Re-deriving them here would mean two copies of `effects.COLOUR_EFFECTS`,
    and the copy in QML is the one nobody would remember to update — a new
    colour-taking effect would simply have no swatches and no error.
    """
    assert re.search(r"visible:.*takes_colour", source), (
        "the swatch row no longer hides itself on takes_colour"
    )
    assert re.search(r"visible:.*takes_speed", source), (
        "the speed row no longer hides itself on takes_speed"
    )
    # Both flags have to come off the polled target rather than off a list of
    # effect names kept here, which is the form the duplicate would take.
    for flag in ("takes_colour", "takes_speed"):
        assert re.search(rf"visible:.*target.*{flag}", source), (
            f"{flag} is no longer read from the status payload's target, so "
            f"the widget is deciding this for itself"
        )


def test_the_previews_stop_when_the_panel_closes(source: str) -> None:
    """Twenty-one animated strips behind a shut popover is a battery bug.

    Nothing about a running animation is visible when the panel is closed, so
    this cannot be noticed by looking — only by a laptop getting warm. The
    poll `Timer` is gated the same way and for the same reason.
    """
    assert re.search(r"animate:\s*root\.opened", source), (
        "the effect previews are no longer gated on the panel being open"
    )
    running = re.search(r"NumberAnimation on clock\s*\{[^}]*running:\s*([^\n]+)",
                        source, re.S)
    assert running, "the preview clock has no NumberAnimation driving it"
    assert "animate" in running.group(1), (
        "the preview clock runs regardless of whether the panel is open"
    )


def test_every_effect_has_a_cell_and_every_number_key_an_effect(source: str) -> None:
    """`effects.EFFECTS` order is load-bearing in two places at once.

    It is the order the cells are drawn in and the order 1-6/0 number, which
    is only one fact as long as both read the same list. Splitting them — a
    literal in the shortcut handler, say — renumbers the keyboard away from
    the panel silently, and `3` starts meaning a different effect than the
    third cell.
    """
    effects = re.search(r"readonly property var effects:\s*\[(.*?)\]", source, re.S)
    assert effects, "the widget no longer names the effects it can set"
    names = re.findall(r'"([a-z]+)"', effects.group(1))
    assert names == ["static", "rainbow", "spectrum", "chase", "breathing",
                     "unhinged", "off"], (
        f"the effect list is {names}, which is not effects.EFFECTS in its "
        f"order — the 1-6/0 shortcuts number this list positionally"
    )
    assert re.search(r'key >= "1" && key <= "6"[\s\S]{0,120}?'
                     r'chooseEffect\(root\.cursorBlock, parseInt\(key\) - 1\)', source), (
        "the number keys no longer index the same effect list the cells draw"
    )
    assert re.search(r'key === "0"[\s\S]{0,120}?'
                     r'chooseEffect\(root\.cursorBlock, root\.effects\.length - 1\)',
                     source), "0 no longer selects the last effect (off)"


def test_the_letter_shortcuts_stay_global(source: str) -> None:
    """d and u act on everything, cursor or no cursor.

    They are the same commands the icon's right-click and middle-click send,
    and scoping them to the cursor's zone would make one keystroke mean two
    things depending on a highlight the user may not have summoned.
    """
    for key, command in (("d", '["display", "toggle"]'), ("u", '["link", "toggle"]')):
        assert f'key === "{key}") root.run({command})' in source, (
            f"the global `{key}` shortcut no longer runs {command}"
        )


def test_the_palette_survived_the_tui(source: str) -> None:
    """These seven values are about to exist nowhere else.

    `tui.PALETTE` is deleted with the TUI, and it is the only place these
    exact hexes have ever been written down. A swatch quietly becoming a
    different green is not something a screenshot review catches.
    """
    for hex_value in ("#ffffff", "#ff0000", "#ff5a00", "#00ff3c", "#00c8ff",
                      "#1e64ff", "#ff00a0"):
        assert hex_value in source, (
            f"{hex_value} is no longer among the swatches — that value came "
            f"from tui.PALETTE and has no other home"
        )
    assert '{ arg: "accent"' in source, (
        "the theme chip is gone; `accent` is the LED_ROLES entry that means "
        "'follow the desktop' and it is the swatch row's whole first cell"
    )


def test_the_cursor_uses_the_shells_own_highlight(source: str) -> None:
    """One highlight on screen, whichever hand is driving.

    `CursorSurface`'s contract is that visuals come from `hasCursor`/`current`
    and never from `containsMouse`; a hand-rolled Rectangle would draw a
    second highlight next to the keyboard's whenever the mouse moved.
    """
    assert "CursorSurface" in source, (
        "the cursor is no longer drawn with the shell's CursorSurface"
    )
    assert re.search(r"onMoveRequested:\s*function\s*\(dx, dy\)\s*\{[^}]*moveCursor",
                     source), "PanelKeyCatcher's arrows no longer move the cursor"
    assert re.search(r"onActivateRequested:.*activateCursor", source), (
        "Enter/Space no longer commits what the cursor is on"
    )


def test_the_cursor_is_clamped_rather_than_wrapped(source: str) -> None:
    """Matching bluetooth and tailscale, which both stop at the ends.

    Wrapping is a one-character change away from this and reads as correct;
    it would just make this panel behave differently from the two beside it
    on the same bar.
    """
    move = re.search(r"function moveCursor\(dx, dy\)\s*\{(.*?)\n  \}", source, re.S)
    assert move, "moveCursor is gone"
    body = move.group(1)
    assert "Math.max(0, Math.min(rows.length - 1" in body, (
        "vertical movement no longer clamps at the panel's ends"
    )
    assert "%" not in body, (
        "moveCursor has grown a modulo — the reference panels clamp at the "
        "boundary rather than wrapping"
    )


def test_the_widget_no_longer_launches_a_window(source: str) -> None:
    """The panel is the whole surface now.

    `omarchy-launch-mimarchy` still exists and is removed in its own phase;
    what matters here is that the widget stopped being a launcher for it, so
    the button cannot come back by accident along with the Hyprland float
    rule that framed the window it opened.
    """
    assert "omarchy-launch-mimarchy" not in source, (
        "the panel still launches the TUI window it is meant to replace"
    )


def test_sensors_left_the_panel_but_not_the_tooltip(source: str) -> None:
    """Two different questions with two different answers.

    Temperatures leave the panel body — the design's own decision, since
    nothing in the panel acts on them. The tooltip keeps its line because
    hover is the only place they are still reachable at all, and deleting
    both at once is how that gets lost without anyone deciding it.
    """
    assert "showSensorsInTooltip" in source and "formatTemp" in source, (
        "the tooltip's sensor line went with the panel's — that was out of "
        "scope and is the last place temperatures are shown"
    )
    body = source[source.index("Column {"):]
    assert "cpu_fan_rpm" not in body, (
        "the fan reading is back in the panel body"
    )
