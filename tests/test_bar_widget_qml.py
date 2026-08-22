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
