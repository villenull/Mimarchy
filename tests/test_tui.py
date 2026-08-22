"""Pilot-driven tests for the Mimarchy screen.

Run headless via Textual's `run_test()`, so these exercise the real widget tree
and the real key bindings rather than calling handlers directly.

Two things are deliberately never touched here: `/` (which would start the cooler
display service) and anything that opens an OpenRGB connection. The TUI writes
state to a file and nothing else, which is what makes it testable without
hardware — and these tests point at a temporary state file so a run cannot
disturb the user's actual lighting.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from mimarchy import lightstate, tui  # noqa: E402
from mimarchy.effects import SPEED_LEVELS  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Point the state file at a temp dir, and stop the app shelling out.

    `systemctl is-active` is called on every repaint to show display status; left
    alone it makes every test depend on the machine's unit state, and a stray
    toggle would start a real service.
    """
    monkeypatch.setattr(lightstate, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(lightstate, "PERSIST_PATH", tmp_path / "persist.json")
    monkeypatch.setattr(tui, "_unit_active", lambda unit: False)
    # Sensor reads shell out to `sensors`; keep them deterministic and quiet.
    monkeypatch.setattr(tui, "read_cpu_temp", lambda: 52.2)
    monkeypatch.setattr(tui, "read_gpu_temp", lambda: 40.0)
    monkeypatch.setattr(tui, "read_cpu_fan_rpm", lambda: 768.0)
    monkeypatch.setattr(tui, "load_config", _NoWrite)
    yield


class _NoWrite:
    """Stands in for Config so `save_link_state` cannot rewrite the real file.

    `zones` carries the developer rig's pair because that is what these tests
    assert about — two rows, collapsing to one when linked. The TUI now draws a
    row per configured zone rather than a fixed pair, so this is the shape of
    the config under test rather than an incidental attribute.
    """

    link_cpu_gpu = True
    zones = {"cpu_fans": None, "gpu": None}

    def save_link_state(self, linked: bool, path=None) -> None:
        self.link_cpu_gpu = linked


def _rows(app) -> list[list[str]]:
    table = app.query_one("#targets")
    return [[str(c) for c in table.get_row_at(r)]
            for r in range(table.row_count)]


def _painted(app, selector: str, lines: int = 8) -> str:
    """What a widget actually puts in the terminal's cells.

    The layer that matters for anything the user is meant to read: cell text is
    post-markup, so a value that parses as a style tag shows up here as missing
    rather than as present-but-invisible.
    """
    widget = app.query_one(selector)
    out = []
    for y in range(lines):
        try:
            out.append(widget.render_line(y).text)
        except Exception:  # noqa: BLE001 — past the end of the widget
            break
    return "\n".join(out)


def _body(app) -> str:
    """The Controls panel's text.

    `.content`, not `.renderable`: Static exposes the assigned content under that
    name in this Textual version, and `.renderable` does not exist at all.
    """
    from textual.widgets import Static
    return str(app.query_one("#control-body", Static).content)


@pytest.mark.asyncio
async def test_two_panels_and_a_hint_bar() -> None:
    """The layout is two boxes; the four old side panels must not come back."""
    app = tui.MimarchyApp()
    async with app.run_test() as pilot:
        assert app.query_one("#lights").border_title == "Lights"
        assert app.query_one("#controls").border_title == "Controls"
        for gone in ("#sensors", "#display", "#effects", "#options"):
            assert len(app.query(gone)) == 0, f"{gone} came back"
        await pilot.pause()


@pytest.mark.asyncio
async def test_starts_linked_as_one_row() -> None:
    app = tui.MimarchyApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        rows = _rows(app)
        assert len(rows) == 1
        assert rows[0][0] == tui.LINKED_LABEL
        assert "(linked)" in rows[0][4]


@pytest.mark.asyncio
async def test_enter_unlinks_into_two_rows() -> None:
    app = tui.MimarchyApp()
    async with app.run_test() as pilot:
        await pilot.press("u")
        await pilot.pause()
        rows = _rows(app)
        assert [r[0] for r in rows] == ["cpu fans", "gpu"]
        assert all("(linked)" not in r[4] for r in rows)


@pytest.mark.asyncio
async def test_a_third_configured_zone_gets_its_own_row(monkeypatch) -> None:
    """The TUI draws a row per configured zone, not a fixed pair.

    The daemon, `mimarchy-ctl` and the bar widget all handled an extra
    `[rgb.zones.*]` block; the TUI was the last place still drawing exactly two,
    so a third strip was invisible in the one place you would go to drive it.
    The linked pair still collapses into a single row — linking is *defined* as
    cpu_fans + gpu — and everything else stands alone.
    """
    class _ThreeZones(_NoWrite):
        zones = {"cpu_fans": None, "gpu": None, "case": None}

    monkeypatch.setattr(tui, "load_config", _ThreeZones)

    app = tui.MimarchyApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert [r[0] for r in _rows(app)] == [tui.LINKED_LABEL, "case"]

        await pilot.press("u")
        await pilot.pause()
        assert [r[0] for r in _rows(app)] == ["cpu fans", "gpu", "case"]


@pytest.mark.asyncio
async def test_unlinking_carries_the_theme_role_across(monkeypatch) -> None:
    """Splitting must not quietly stop the GPU following the theme.

    The seed copies the shared entry so the GPU starts where it visibly was.
    Copying the resolved RGB alone left a GPU that looked identical and had
    silently become a fixed colour — a difference that would not show until the
    next theme switch moved one and not the other.
    """
    app = tui.MimarchyApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        shared = app.state.for_target("cpu_fans")
        shared.colour, shared.colour_role = (1, 2, 3), "accent"

        await pilot.press("u")
        await pilot.pause()

        gpu = app.state.for_target("gpu")
        assert gpu.colour_role == "accent"
        assert gpu.colour == (1, 2, 3)


@pytest.mark.asyncio
@pytest.mark.parametrize("key,effect", [
    ("1", "static"), ("2", "rainbow"), ("3", "spectrum"),
    ("4", "chase"), ("5", "breathing"), ("6", "unhinged"), ("0", "off"),
])
async def test_number_keys_match_the_legend(key: str, effect: str) -> None:
    """The key, the stored effect and the on-screen legend must agree.

    These drifted apart once before, leaving a legend that disagreed with the
    handler, so all three are checked together.
    """
    app = tui.MimarchyApp()
    async with app.run_test() as pilot:
        await pilot.press(key)
        await pilot.pause()
        assert app.state.for_target("cpu_fans").effect == effect
        assert _rows(app)[0][1] == effect
        assert f"{key} {effect}" in _body(app)


@pytest.mark.asyncio
async def test_pressing_the_same_number_again_cycles_colour() -> None:
    app = tui.MimarchyApp()
    async with app.run_test() as pilot:
        await pilot.press("5")           # breathing takes a colour
        first = tuple(app.state.for_target("cpu_fans").colour)
        await pilot.press("5")
        await pilot.pause()
        assert tuple(app.state.for_target("cpu_fans").colour) != first


@pytest.mark.asyncio
async def test_repeat_press_does_nothing_for_a_colourless_effect() -> None:
    app = tui.MimarchyApp()
    async with app.run_test() as pilot:
        await pilot.press("6")           # unhinged picks its own colours
        before = tuple(app.state.for_target("cpu_fans").colour)
        await pilot.press("6")
        await pilot.pause()
        assert tuple(app.state.for_target("cpu_fans").colour) == before
        assert "has no color" in _body(app)


@pytest.mark.asyncio
@pytest.mark.parametrize("down,up", [("left", "right"), ("minus", "plus")])
async def test_speed_keys_walk_the_ladder(down: str, up: str) -> None:
    """Both bindings, and both clamp at the ends rather than wrapping.

    Left/right are the primary keys; -/+ are kept as aliases. The arrows are only
    free because the Lights table claims up/down for its cursor and nothing else.
    """
    app = tui.MimarchyApp()
    async with app.run_test() as pilot:
        await pilot.press("5")           # breathing has a speed
        for _ in range(len(SPEED_LEVELS) + 2):
            await pilot.press(down)
        await pilot.pause()
        assert app.state.for_target("cpu_fans").speed == SPEED_LEVELS[0]
        for _ in range(len(SPEED_LEVELS) + 2):
            await pilot.press(up)
        await pilot.pause()
        assert app.state.for_target("cpu_fans").speed == SPEED_LEVELS[-1]


@pytest.mark.asyncio
async def test_static_reports_having_no_speed() -> None:
    app = tui.MimarchyApp()
    async with app.run_test() as pilot:
        await pilot.press("1")
        await pilot.press("minus")
        await pilot.pause()
        assert "has no speed" in _body(app)


@pytest.mark.asyncio
async def test_gpu_row_is_tagged_when_the_firmware_drives_it() -> None:
    """Unlinked rainbow routes the GPU to its own Rainbow Wave, so say so."""
    app = tui.MimarchyApp()
    async with app.run_test() as pilot:
        await pilot.press("u")       # unlink
        await pilot.press("down")        # select gpu
        await pilot.press("2")           # rainbow
        await pilot.pause()
        gpu_row = _rows(app)[1]
        assert gpu_row[0] == "gpu"
        assert "firmware" in gpu_row[4]
        # Not "the card picks its own colours": rainbow has no chosen colour, so
        # the reason the palette is dead here is the same as for spectrum.
        assert "rainbow has no color" in _body(app)
        # Checked against the *rendered* cells, not just the stored value. The
        # stored form has to be escaped — a bare `[firmware]` parses as a style
        # tag and vanishes — and asserting on state alone passed happily while
        # nothing showed on screen.
        assert "[firmware]" in _painted(app, "#targets")


@pytest.mark.asyncio
async def test_linked_chase_is_not_tagged_firmware() -> None:
    """Chase carries a colour the card ignores, so the link keeps it rendered."""
    app = tui.MimarchyApp()
    async with app.run_test() as pilot:
        await pilot.press("4")
        await pilot.pause()
        assert app.state.for_target("cpu_fans").effect == "chase"
        assert "firmware" not in _rows(app)[0][4]
        assert "gpu firmware picks its own" not in _body(app)


@pytest.mark.asyncio
async def test_linked_rainbow_is_tagged_firmware() -> None:
    """Rainbow is the exception: no chosen colour, so the card runs it either way.

    Rendering it on the GPU's single LED put one flat hue on a multi-segment bar,
    which reads as spectrum — the "rainbow is broken" report.
    """
    app = tui.MimarchyApp()
    async with app.run_test() as pilot:
        await pilot.press("2")
        await pilot.pause()
        assert app.state.for_target("cpu_fans").effect == "rainbow"
        assert "firmware" in _rows(app)[0][4]
        assert "[firmware]" in _painted(app, "#targets")


@pytest.mark.asyncio
async def test_unlinked_chase_says_the_card_ignores_the_colour() -> None:
    """The one case where the warning is true: chase carries a colour, Runway
    ignores it, and the two devices visibly disagree."""
    app = tui.MimarchyApp()
    async with app.run_test() as pilot:
        await pilot.press("u")       # unlink
        await pilot.press("down")        # select gpu
        await pilot.press("4")           # chase
        await pilot.pause()
        assert "gpu firmware picks its own" in _body(app)


@pytest.mark.asyncio
async def test_link_and_display_are_on_letters() -> None:
    """`u` and `d`, with the old `enter` / `/` kept as aliases."""
    keys = {k for b in tui.MimarchyApp.BINDINGS for k in b.key.split(",")}
    assert {"u", "enter"} <= keys
    assert {"d", "slash"} <= keys
    assert "full_stop" not in keys, "sync was removed"


@pytest.mark.asyncio
async def test_every_effect_is_on_one_row() -> None:
    """All seven keys on a single line.

    They used to wrap after five, which put `6 unhinged` and `0 off` on a second
    line and made them read as a separate category.
    """
    app = tui.MimarchyApp()
    async with app.run_test(size=(110, 24)) as pilot:
        await pilot.pause()
        first = _body(app).splitlines()[0]
        for token in ("1 static", "2 rainbow", "3 spectrum", "4 chase",
                      "5 breathing", "6 unhinged", "0 off"):
            assert token in first, f"{token} not on the effect row"


@pytest.mark.asyncio
async def test_both_panels_share_a_border_colour() -> None:
    """bluetui draws its two panels in one colour; so does this."""
    css = tui.MimarchyApp.CSS
    assert "#lights {\n        border: round $mim-frame" in css
    assert "#controls {\n        border: round $mim-frame" in css


@pytest.mark.asyncio
async def test_no_quit_binding() -> None:
    """No quit key: this is an overlay, closed by closing its window."""
    keys = {k for b in tui.MimarchyApp.BINDINGS for k in b.key.split(",")}
    assert "q" not in keys
    assert "escape" not in keys
    assert all("quit" not in b.action for b in tui.MimarchyApp.BINDINGS)


@pytest.mark.asyncio
async def test_status_line_explains_that_off_is_not_instant() -> None:
    app = tui.MimarchyApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        body = _body(app)
        assert "display:" in body
        assert "blanks" in body


@pytest.mark.asyncio
async def test_state_survives_a_restart() -> None:
    app = tui.MimarchyApp()
    async with app.run_test() as pilot:
        await pilot.press("u")
        await pilot.press("6")
        await pilot.pause()
    again = tui.MimarchyApp()
    async with again.run_test() as pilot:
        await pilot.pause()
        assert again.linked is False
        assert again.state.for_target("cpu_fans").effect == "unhinged"
