# Omarchy 4 verification notes

Phase 0 of [the upgrade plan](omarchy-4-plan.md) is "confirm the v4 facts on a
live machine". Most of it turned out to be answerable from the Omarchy 4.0
source itself, which is better than answering it from a running desktop: the
source is the thing the desktop is built from, and checking against it catches
mistakes before they cost a session. What is recorded here is what was checked
and what it said. The items that genuinely need hardware are marked
**needs hardware** and are still open.

## Settled from source

### The plugin passes the real validator

`omarchy-plugin-validate` was run against this repo, not reimplemented:

    bash omarchy/bin/omarchy-plugin-validate .   # exits 0

That covers `schemaVersion`, the required fields, a non-reserved id, entry
points that are safe relative paths and exist, an entry point for every
declared kind, and — the one that dictated where the virtualenv lives — no
symlinks anywhere under the folder except beneath `.git`.

### Every QML symbol resolves

The widget was checked component by component against `shell/Ui/` and the
first-party plugins in `shell/plugins/panels/`. All fifteen instantiated types
resolve (`BarIconButton`, `Button`, `KeyboardPanel`, `Panel`,
`PanelKeyCatcher`, `PanelSectionHeader`, `PanelSeparator`, `Toggle` from
`qs.Ui`; `Column`, `Repeater`, `Row`, `Text`, `Timer` from QtQuick; `FileView`,
`Process` from `Quickshell.Io`), as do every `Style.*`, `Color.*` and `Util.*`
token used.

Three idioms were confirmed against working first-party code rather than
inferred:

- **The bar icon.** `BarIconButton` extends `WidgetButton`, which is where
  `bar`, `tooltipText`, `signal pressed(int)` and `signal wheelMoved(int)`
  actually live. `audio`, `bluetooth`, `monitor`, `network` and `power` all
  instantiate it identically to this widget.
- **The panel.** `KeyboardPanel` declares `anchorItem` and `bar` as *required*
  properties; the `PanelKeyCatcher`-wrapping-a-`Column` structure matches
  `bluetooth/Panel.qml` line for line. `fittedContentWidth(width, cap)` takes
  an optional cap, so the single-argument call is correct.
- **Launching the TUI.** `root.bar.run("omarchy-launch-mimarchy")` is exactly
  what `shell/plugins/agents/Panel.qml` does; `Bar.run(command)` takes a
  string and hands it to `Util.execDetached`.

`FileView` (`watchChanges` / `printErrors` / `onFileChanged` / `onLoaded`)
matches `weather/Panel.qml`, and the `Process` + `StdioCollector`
(`waitForEnd` / `onStreamFinished`) pairing matches `audio` and `monitor`.

### `qs.Ui` resolves from a third-party plugin directory

This was the load-bearing unknown — the widget is unwritable without it, and
plugins live outside the shell tree. Settled by `omarchy plugin clone`, which
copies a built-in *verbatim* into `~/.config/omarchy/plugins/<user>.<name>/`
and runs it there. Those built-ins import `qs.Ui`, so the import path is the
engine's, not the file's.

### The theme hook contract

`~/.config/omarchy/hooks/theme-set.d/` is correct, and the runner passes the
theme slug as `$1` (`default/agents/skills/omarchy/hooks.md`). Our hook ignores
the argument deliberately — see the comment in `omarchy/theme-set.d/mimarchy`.
Omarchy also ships `omarchy hook install <name> <script>`, which is the same
copy-and-chmod `install.sh` does inline.

### The launcher chain

`omarchy-launch-or-focus-tui` exists in v4 and derives its app-id as
`org.omarchy.$(basename "$1")` — so `omarchy-launch-or-focus-tui mimarchy-tui`
yields `org.omarchy.mimarchy-tui`, which is the id the Hyprland rule matches.

### The Hyprland rule and the menu entry

`o.window(match, rules)` is defined in `default/hypr/helpers.lua`, and
`default/hypr/apps/system.lua` uses `{ tag = "+floating-window" }` in exactly
the form the snippet does. The `floating-window` tag carries `float`, `center`
and `size = { 875, 600 }`, which is why the snippet tags rather than setting
`float` directly.

For the menu entry, `icon` / `label` / `action` / `when` are all real keys, and
`description` is read by `MenuModel.js` and fed into search scoring — so it
does the job it was added for.

## One bug this found

The bar icon was drawn through an `iconComponent` holding a hand-placed
`OpticalGlyph`. That bypassed the base class in two ways that only show up on a
live bar: the glyph took `Style.font.family` and `Style.space(12)` instead of
the bar's own family and `Style.bar.iconFont`, so it would not have followed
`omarchy font set` and would not have matched the size of its neighbours.

Worse, "frozen" was drawn as `Qt.darker(barForeground, …)`. On a light theme
`barForeground` is near-black, so darkening it *raises* contrast against the
bar — the frozen icon would have read as louder than the live one, inverting
the only signal the icon carries. It now sets `text` and `dimmed` and lets
`WidgetButton` do both, which dims by opacity and is therefore right under
either polarity. (Same class of light-theme inversion as the role collapse
Phase 3 hit; worth assuming it recurs anywhere a colour is derived by
darkening.)

## The live session — Omarchy 4.0.0-1, 2026-08-22

Run on the development rig itself (ASUS PRIME X870-P WIFI, RX 9070 XT Nitro+,
Omarchy 4.0.0-1, Python 3.14.7), with `tools/fake-openrgb-server.py` standing in
for OpenRGB so the lighting path could be exercised before the one-shot detection
pass that #4888 makes expensive to get wrong.

### The widget loaded, and was invisible

This is the item Phase 0 said was the largest remaining unknown, and it was
right to say so. Every check that could be made without running it passed —
`omarchy plugin validate` exits 0, the shell logged `Local plugin changed,
reloading` with no QML error of any kind, and `omarchy plugin list` reported
`enabled`. `omarchy-shell shell debugBarGeometry` is what actually told the
truth:

    io.github.villenull.mimarchy   x=3216  w=0  h=0  visible=false  itemVisible=true

The component instantiated; it was laid out at zero size. `Ui/Panel` is a bare
`Item` and has no implicit size of its own, and the root bound none — so the
`BarIconButton` anchored to it with `anchors.fill: parent` filled nothing. Every
first-party widget binds both; `bluetooth/Panel.qml` takes them from its button.
Fixed by doing the same, and `tests/test_bar_widget_qml.py` now asserts it,
because the failure is completely silent: no error, no warning, and two separate
status commands reporting the widget as installed and enabled.

One thing to know when iterating: editing the file under
`~/.config/omarchy/plugins/` hot-reloaded it but did **not** re-evaluate the
root's implicit-size binding — the widget stayed at 0x0 until a full `omarchy
restart shell`. Hot reload is reliable for what a component *draws*; it is not
for what the bar *measures*.

### `active: false` is not a fault

`omarchy plugin list --json` reports `active: false` for this widget, and that
is correct rather than a symptom. `shell.qml`'s `listPlugins` computes
`active = isBarOption && isActiveBarOption(id)`, where `isBarOption` means the
manifest declares kind `bar` — a whole-bar replacement. A `bar-widget` is never
a bar option, so `active` is false for every one of them, first-party included.
The field to read is `enabled`, which for a widget is `inBar(id)`.

### What the live run confirmed

- **Both icon states.** Dimmed with `mimarchy-light.service` stopped, full
  brightness with it running, at the same size and family as its neighbours —
  which is the `text` + `dimmed` fix drawn through `WidgetButton` rather than an
  `iconComponent`.
- **The panel**, on a horizontal top bar: header, all three zones with effect
  and speed, the sensor row, both toggles and the launch button, in theme
  colours. It updates live — `mimarchy-ctl link toggle` and `effect chase` were
  both reflected without touching the panel.
- **Three zones through the whole stack.** `mimarchy-setup` wrote a config for a
  board zone, a GPU zone and a *third* strip at a different length (20 vs 15),
  the daemon resized each to its own length, and all three appear in the widget.
- **Graceful degradation, twice.** `openrgb.service` fails to start with no
  OpenRGB installed and `mimarchy-light.service` runs anyway — `Wants=` is
  correctly not `Requires=`. And with no `nct6687d`, fan RPM reads `null` from
  `mimarchy-ctl` and `fan —` in the panel rather than a zero.
- **The v4 theme reader, on live themes.** `theme_dirs()` puts the state path
  first and parses the active theme; all 22 stock themes on the machine parse as
  v4, five of them light with `mode`. The only `led_colour` gaps are `orange` on
  `last-horizon`, `solitude` and `white` — the three the code already predicts.

### The stub was looser than the hardware

`tools/fake-openrgb-server.py` accepted a resize on a zone declaring
`leds_min == leds_max == 1`, which real OpenRGB ignores. That is not a cosmetic
difference: it meant the one-LED GPU zone came up at 15 LEDs, so the daemon took
the *rendered* branch for it every time and the firmware hand-off in
`lightd._split_targets` — the subtlest thing Phase 4 added — had never once been
exercised. With the stub clamping to the zone's own bounds, the whole decision
tree is visible in its log:

| effect | the one-LED GPU zone | the other two |
|---|---|---|
| `rainbow` | firmware `Rainbow Wave`, speed matched, **no frame** | rendered |
| `chase` | `Static` + a rendered 1-LED frame | rendered |
| `static` / `spectrum` / `breathing` / `unhinged` | rendered 1-LED frame | rendered |

Which is exactly what the docstring claims: rainbow goes to firmware even while
linked, chase stays rendered while linked. The stub now also logs and applies
mode changes, since a stub that only logs frames shows silence precisely where
the interesting branch was taken.

## Still open — needs the real lighting

1. **The lighting path on real hardware.** Everything above ran against the
   stub. `openrgb` is not installed on the machine yet, and installing it means
   the one-shot detection pass in `install.sh` step 2 — the #4888 hazard this
   rig is known to reproduce.
2. **A vertical bar.** `vertical` changes `BarIconButton`'s sizing (`fixedWidth`
   vs `fixedHeight`) and the panel's anchoring, and the sizing bug above is a
   reminder that this is the kind of thing only a running bar answers.
3. **Wheel and right-click**, and the backend-missing message.
4. **The theme hook end to end** — switch themes and watch the LEDs follow.
   Needs the LEDs.
5. **A user-installed theme's `colors.toml`.** The machine has only the 22 stock
   themes.
6. **`omarchy-launch-mimarchy`** — that it opens in Foot and floats.

## Running the test session

`omarchy plugin add https://github.com/villenull/mimarchy` **does not work
yet**, and it is worth knowing why before trying it: `omarchy-plugin-add` runs
a bare `git clone` of the URL, which takes the repo's default branch. `main` is
still the pre-upgrade release — no `manifest.json` — so validation refuses it.
That resolves itself when this branch merges; until then, add from a local
checkout, which `git clone` accepts as a URL:

    git clone -b claude/omarchy-4-upgrade-plan-ezgh0h \
      https://github.com/villenull/mimarchy ~/mimarchy

    ~/mimarchy/install.sh          # venv, services, theme hook, symlinks
    mimarchy-ctl status            # backend works before the widget is involved

    omarchy plugin add ~/mimarchy --enable

`install.sh` needs `openrgb` present and stops with instructions if it is not.
It builds the virtualenv in `~/.local/share/mimarchy/`, never in the checkout.

Two things make iterating cheap once it is loaded. Saving any file under
`~/.config/omarchy/plugins/` hot-reloads the plugin, so QML fixes land without
restarting the shell — but note the plugin directory is a *separate clone*, so
edit there and port back, or work in the plugin directory from the start.
And `tools/fake-openrgb-server.py` runs the whole backend against three
invented devices, so widget work does not have to wait on the real rig.
