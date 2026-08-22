# Panel-only plan — the widget becomes the whole product

Decided 2026-08-22, after the first live run on Omarchy 4 (see
[`omarchy-4-notes.md`](omarchy-4-notes.md)):

- **Everything moves into the bar panel.** Clicking the lightbulb is the whole
  interface; there is no second window to open.
- **The TUI retires.** Not deprecated in place — removed.
- **Sensors leave the panel.** Temperatures and fan RPM are no longer part of
  what the widget shows.
- **Colour stays a swatch row**, with the theme chip first.
- **Size is not a constraint.** The panel may be taller and wider than it is.
- **Layout is *always open*.** Every zone carries its own effect strip, swatch
  row and speed card, all visible at once. No selection state, no accordion.
- **Link means every zone**, not `cpu_fans` + `gpu`.

The two decisions settle each other. The one real objection to *always open* was
that its height grows with the rig — but linking is on by default, and a linked
panel collapses to a single set of controls for all zones, because three
identical blocks that all change together are redundant and actively misleading.
So the default state is short whatever the zone count, and the tall layout is
what you opted into by unlinking.

## 0. Two things in the backend block the panel

This is not a QML problem. `mimarchy-ctl` cannot address a single zone:

| command | today | the panel needs |
|---|---|---|
| `effect <name>` | sets it on **every** target | one named zone |
| `colour <value>` | sets it on **every** target | one named zone |
| `speed <+/->` | steps the ladder, all targets | set a stop directly, one zone |

The TUI never needed this because it is a Python process that mutates
`lightstate` in memory and calls `save()`. The widget's only channel is the
command line, so per-zone control has to exist there before the panel can be
built against it.

That ordering is the plan's one hard dependency, and it is worth stating plainly
because the QML is the visible part and would otherwise look like the place to
start.

### And the link is hard-coded to two names

`lightd._linked_pair` decides membership with `key in ("cpu_fans", "gpu")`.
Those are only what `mimarchy-setup` *suggests* from the device kind — the user
names their own zones, and a board-plus-strip rig never has a `gpu` at all. On
any such machine the Link toggle flips, saves, and changes nothing:

    A rig whose zones are named 'board' and 'strip', link ON:
      board   in the link? False   follows -> chase
      strip   in the link? False   follows -> static

So "link every zone" is not only the behaviour asked for; it removes the last
place where the code still assumes the development rig's own zone names. Phase 4
generalized the lighting path and left this behind.

Two consequences to take deliberately:

- **One-LED zones lose their firmware effects while linked.** `plan()` keeps a
  colour-carrying spatial effect rendered rather than handed to the card when a
  zone is in the link, because the card's Runway ignores the chosen colour —
  filmed running yellow while the strip ran red. That applied to two zones; now
  it applies to every one-LED zone in the group. It is the price of "make these
  match", and unlinking buys it back. The docstring on `_linked_pair` describes
  this as the bug that motivated narrowing the link — under the new definition
  it is the intended behaviour, and that docstring has to be rewritten rather
  than deleted.
- **Something has to be the shared entry.** `cpu_fans` is the wrong answer for
  exactly the reason above. Use the first zone in config order: always present,
  stable across restarts, and independent of what anyone named anything.

## Phase A — per-zone `mimarchy-ctl` (ships alone, no UI change)

Add zone addressing to the three commands that change lighting:

    mimarchy-ctl effect chase --zone gpu
    mimarchy-ctl colour '#ff0044' --zone cpu_fans
    mimarchy-ctl colour accent --zone strip      # role: follows the theme
    mimarchy-ctl speed set 3 --zone gpu          # absolute, 1..speed_stops
    mimarchy-ctl speed + --zone gpu              # the existing relative form

Notes that decide the shape:

- **Omitting `--zone` keeps today's meaning** — every target. That is what the
  bar icon's wheel and the middle-click already do, and what anyone's scripts
  do; changing the default would break both silently.
- **`speed set N`** is new. The panel clicks a stop directly, and walking there
  with repeated `+` is both slow and wrong at the ends of the ladder.
- **An unknown zone is an error**, not a no-op. A typo that silently does
  nothing is the worst outcome for a CLI that a GUI drives.
- `--zone` validates against the config's zone keys, which `status --json`
  already publishes — so the panel never has to guess a name.

And redefine the link, in the same phase because it is the same file and the
same tests:

- `_linked_pair` becomes `_linked` — membership is `state.linked`, with no name
  check. Rewrite the docstring: what it documents as a bug is now the definition.
- `_source_target` follows the first zone in config order rather than
  `cpu_fans`, so `lightd` needs the config's key order where it currently needs
  only a hard-coded pair.
- `mimarchy-ctl link` keeps `on`/`off`/`toggle`; only its meaning widens. No CLI
  change, which is worth noting — this is a behaviour change with no syntax to
  deprecate.
- `status --json` should say which zones are in the link, so the panel can label
  the collapsed block with its members rather than re-deriving the rule.

Ships on its own: the TUI still works, the current widget still works, and the
new surface is testable without any QML. Tests go in `tests/test_ctl.py` and
`tests/test_lightd_plan.py` alongside the existing ones — the latter already
covers the linked/firmware interaction and will need the widened definition.

## Phase B — the panel, rebuilt

Layout is *always open*, in two states that share every component.

Linked — the default, and short whatever the zone count:

    MIMARCHY                                   Settings
    Link all zones            driven together   [x]
    ───
    All zones                              rainbow
    cpu fans · gpu · chassis fans
    [ effect strip — 7 cells ]
    [ SPEED  5/5  ▮▮▮▮▮ ]
    ───
    Cooler display                             [ ]

Unlinked — one full block per zone:

    MIMARCHY                                   Settings
    Link all zones      driven independently   [ ]
    ───
    cpu fans                              spectrum
    [ effect strip ]  colour ◍ ● ● ● ● ● ● ●
    [ SPEED  4/5  ▮▮▮▮▯ ]
    ───
    gpu                                      chase
    ...
    ───
    Cooler display                             [ ]

The link toggle moves to the top, above the zones: it decides how many blocks
follow, so it reads as the thing that shapes the panel rather than a footnote
under it.

The parts worth specifying now:

- **Effect cells preview the effect.** Each of the seven cells holds a
  three-LED strip running that effect in the zone's own colour. Seven names do
  not distinguish themselves in words — *breathing* and *spectrum* both mean
  "one colour, changing" — and a preview is the shortest description available.
  These are hand-authored approximations in QML, **not** the real renderer:
  reusing `effects.py` would mean a Python round trip per frame per cell. With
  every zone open, that is seven cells per zone — the reason previews must stop
  when the panel closes.
- **Animation stops with the panel.** The previews only run while the panel is
  open. A bar widget that animates 21 strips behind a closed popover is a
  battery bug on a laptop.
- **Switching link re-lays out the whole panel.** Collapsing three blocks into
  one is a large, deliberate change of shape, so it should animate rather than
  jump — and the panel's own height changing under the pointer is the thing to
  watch for in review.
- **The collapsed block is labelled with its members** (`cpu fans · gpu ·
  chassis fans`), read from `status --json`, so "All zones" is never a claim the
  user has to take on trust.
- **The colour row hides when the effect ignores it** — the same rule as the
  TUI's "spectrum has no color" note.
- **The swatch set is `theme` plus the seven fixed colours** already in
  `tui.PALETTE` (white, red, orange, green, cyan, blue, magenta). `theme` is the
  `accent` role. Moving that list out of `tui.py` before the TUI is deleted is
  part of this phase, not Phase C — the swatches outlive the window they were
  written for.
- **Sensors come out**, including the temperature half of the tooltip.
- **The panel has to be keyboard-drivable**, because retiring the TUI is
  otherwise where keyboard operation goes. Omarchy 4 supports this properly and
  the widget already sits on the right primitives — `KeyboardPanel` with
  `focusTarget: keyCatcher`, which is what every first-party panel does, and
  `KeyboardPanel` exists specifically so a panel can be summoned by key at all
  (an xdg-popup only gets keys after a click routes focus to it). What is
  missing is a cursor: today the panel has nothing to select, so it only uses
  `PanelKeyCatcher`'s `textKey`. Always-open turns it into a 2D field — N zones
  by seven effects, eight swatches and five stops — which needs
  `moveRequested`/`activateRequested` wired the way `bluetooth/Panel.qml` drives
  `moveCursor`/`activateCursor`. Keep the existing letters (`d`, `u`, `+`, `-`)
  and add `1`–`6`/`0` for effects, so the TUI's muscle memory survives it.

## Phase C — remove the TUI

Only after B is working, so there is never a commit where neither surface is
complete.

- Delete `src/mimarchy/tui.py` and `tests/test_tui.py` (≈940 lines).
- Drop the `mimarchy-tui` entry point from `pyproject.toml`, and `textual` from
  the dependencies — it is the TUI's alone.
- Remove `bin/omarchy-launch-mimarchy`, `omarchy/mimarchy.lua` (the float rule),
  and the launcher symlink and Hyprland step from `install.sh`.
- Rewrite the README: the install section, the key table, and the framing. The
  current opening — "a single-screen TUI" — becomes a bar widget, and the
  "runs anywhere, falls back to your terminal's colours" story goes with it.
- `omarchy/mimarchy-menu.jsonc` currently launches the TUI. Point it at the
  panel instead.
- **Ship a keybinding suggestion in its place.** The stock bindings are all
  `o.bind("SUPER + CTRL + B", "Bluetooth", "omarchy-shell shell toggle
  omarchy.bluetooth")`, and the generic form works for a third-party id —
  verified with `omarchy-shell shell toggle io.github.villenull.mimarchy`. That
  is the direct replacement for `omarchy-launch-mimarchy`, and it means the
  Hyprland float rule retires with the window it was floating.

**What survives, and should be said out loud:** `mimarchy-ctl` becomes the only
interface that works over SSH or on a machine without Omarchy. Phase A is what
makes that acceptable rather than a regression — after it, the CLI can do
everything the TUI could.

`hwmon.py` stays regardless: the cooler display streams temperature, so reading
it is not the TUI's dependency.

## Phase D — manifest, docs, release

- `manifest.json`: bump version in step with `pyproject.toml` (asserted by
  `tests/test_manifest.py`), rewrite `description` — it currently ends "as a bar
  widget, with a TUI behind it".
- Remove `idlePollIntervalSec` from the manifest schema if the idle poll goes.
  With sensors gone, the only thing the closed icon tracks is lit-vs-dim, which
  the state file's own watch already reports — so the idle poll may be
  deletable rather than merely retuned. Worth measuring before removing a
  setting, since removing one is user-visible.
- New screenshots for the README and the marketplace listing. `docs/demo.gif`
  shows the TUI and will be wrong.
- Update `omarchy-4-plan.md` — Phase 5's listing copy leads on the TUI.

## Sequencing

A → B → C → D, and A genuinely gates B. A is small, fully testable without a
desktop, and useful on its own; B is the bulk of the work and the only part that
needs a running shell; C is deletion, which is only safe once B is real; D is
what makes it a release rather than a working tree.

## Open questions

1. **The tooltip.** Sensors leave the panel — do they leave the tooltip too, or
   is a temperature on hover still worth having when nothing else shows it?
2. **The idle poll**, per Phase D — measure before deleting the setting.
3. **Wheel and right-click on the icon** stay as they are, and the wheel acts on
   every zone. That now agrees with what Link means while linked — but should it
   still change everything at once when the zones are independent?
