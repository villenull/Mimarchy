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

## Still open — needs hardware

1. **Load the widget.** Nothing here proves the QML *runs*; it proves nothing
   is misspelled. Watch the icon's lit/dim states, the panel at both bar
   orientations, wheel and right-click, and the backend-missing message.
2. **Both bar orientations.** `vertical` changes `BarIconButton`'s sizing
   (`fixedWidth` vs `fixedHeight`) and the panel's anchoring.
3. **The theme hook end to end** — switch themes and watch the LEDs follow.
4. **`colors.toml` on this machine's themes**, including a light one with
   `mode`. The 22 stock themes were parsed in Phase 1; a user-installed theme
   has not been.
5. **The whole lighting path**, which no amount of source reading covers.

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
