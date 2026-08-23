# Omarchy 4 upgrade & public release plan

Omarchy 4.0 "Quattro" (released 2026-08-14) replaced the entire Waybar-era
desktop shell with a single Quickshell process, moved the theme state, and
redesigned the theme palette. Two of Mimarchy's three Omarchy integration
surfaces break outright; the third needs a loader update. In exchange, v4
ships something 3.x never had: a first-party **plugin system** for the bar,
with a community marketplace behind it — which is a far better home for
Mimarchy than a merged-by-hand Waybar snippet ever was.

Decisions already made:

- **Omarchy 4-first.** v4 is the primary target. The Waybar snippets stay in
  the repo as a legacy path for 3.x holdouts, but new work is not dual-tested.
- **A native Quickshell widget is the deliverable**, not just a launcher icon.
- **Generalize the lighting path; keep the display niche.** OpenRGB lighting
  should work on any rig; the cooler-display driver stays specific to the
  `5131:2007` panel.
- **Distribution channel: the Omarchy Plugin Marketplace**
  ([omarchyplugins.com](https://omarchyplugins.com)).

Facts below were verified against the `basecamp/omarchy` source at v4.0.0+
(`docs/theming.md`, `docs/file-layout.md`, `shell/README.md`, manual chapters
05/15/21/32/43) and the marketplace registry
(`HANCORE-linux/omarchy-plugin-marketplace`), not just release coverage.

## 1. What Omarchy 4 changed, as it affects Mimarchy

- **Waybar is gone, not deprecated.** One Quickshell process
  (`omarchy-shell`) now provides the bar, launcher, notifications, OSDs,
  lock screen, and control panels. The v4 upgrade script *uninstalls* waybar
  and moves `~/.config/waybar` to a `.bak`. There is no fallback bar.
- **Bar extensions are now a supported feature.** A plugin is a public git
  repo with a `manifest.json` at its root declaring QML entry points, with
  kinds `bar-widget`, `panel`, `overlay`, `menu`, `service`, or `bar`.
  Installed with `omarchy plugin add <git-url>` into
  `~/.config/omarchy/plugins/<id>/`, managed from *Setup > Plugins*, edits
  hot-reload, and `omarchy plugin validate` checks the manifest. Plugins run
  unsandboxed inside the shell process.
- **Theme state moved and grew.** The active theme is now
  `~/.local/state/omarchy/current/theme` (was `~/.config/omarchy/current/theme`;
  no compat symlink is left behind). `colors.toml` went from the 16 ANSI
  slots + extras to **24 semantic keys**: `mode`, `accent`, `selection`,
  `muted`, four background and four foreground tiers, and *named* colors
  (`red`, `yellow`, `green`, `cyan`, `blue`, `magenta`, `orange`, `brown`)
  with `bright_*` variants. `color0`–`color15` are not the vocabulary anymore.
- **Theme switches are extensible.** `omarchy-theme-set` fires a `theme-set`
  hook (`~/.config/omarchy/hooks/theme-set.d/`), and any template dropped in
  `~/.config/omarchy/themed/<file>.tpl` is re-rendered with the new palette
  on every switch. Both are sanctioned ways for third-party tools to react
  to a theme change — Mimarchy no longer has to be "themed on next open".
- **TUI launch conventions shifted.** `omarchy-launch-or-focus-tui` and
  `omarchy-launch-tui` survive, but they now go through `xdg-terminal-exec`
  and the default terminal is **Foot**, not Alacritty. Hyprland config is now
  **Lua**; the stock rules float only a fixed list of `org.omarchy.*` classes
  plus the `TUI.float` class, so an arbitrary `org.omarchy.mimarchy-tui`
  window **tiles** by default on v4. The `bluetui`/`impala` floating-TUI
  pattern Mimarchy imitates was removed from stock (those jobs moved into
  shell panels) — but user-registered TUIs (*Install > TUI*) are a feature,
  so the pattern itself is still native to the culture.
- **The "store".** There is no official 37signals store; the thing that
  opened is the **Omarchy Plugin Marketplace** — a community-curated
  directory (launched late July 2026, ~880 listings as of Aug 22, past 500
  plugins per DHH on Aug 19) that the official manual points plugin authors
  to. Listings are git repos, submitted via a GitHub issue form, with an
  automated security-baseline scan and capability flags. Distribution is
  always `omarchy plugin add <git-url>` — the marketplace hosts no code.

## 2. Where Mimarchy touches Omarchy today

Everything else — the effect renderer, OpenRGB client, HID display protocol,
udev rule, systemd user units — is distro-version-agnostic and untouched.
(v4 launches apps in systemd scopes via `uwsm-app`; user services keep
working exactly as before.)

| Surface | Where | On Omarchy 4 |
|---|---|---|
| Theme palette | `theme.py` reads `~/.config/omarchy/current/theme/colors.toml`, keys `color0`–`15` | **Broken twice**: path moved, and the key vocabulary changed. Fails soft to the fallback palette — themed UI silently lost |
| Bar module | `waybar/mimarchy-module.jsonc` + CSS | **Gone** — no Waybar to merge into |
| Launcher | `omarchy-launch-mimarchy` → `omarchy-launch-or-focus-tui mimarchy-tui` | Script family survives, but the window now **tiles** (class isn't in v4's float list) and opens in Foot |
| Float rule | Hyprland `windowrule` line in `install.sh` step 5c | Wrong syntax — Hyprland config is Lua now (`o.window{…}`) |
| Install docs | `install.sh` step 5b: "merge into waybar config, `omarchy restart waybar`" | Wrong on v4 regardless |

## 3. Phase 0 — verify on a live Omarchy 4 machine — **mostly closed**

Findings are in [`docs/omarchy-4-notes.md`](omarchy-4-notes.md).

Most of this list turned out to be answerable from the Omarchy 4.0 source
rather than from a running desktop, which is the better place to answer it:
the source is what the desktop is built from, and a mistake caught there
costs a grep instead of a session. Settled that way: the real
`omarchy-plugin-validate` passes against this repo; every QML symbol the
widget uses resolves against `shell/Ui/`, with the bar-icon, panel and
launch idioms confirmed against working first-party plugins; `qs.Ui` does
resolve from a third-party plugin directory (`omarchy plugin clone` copies a
built-in verbatim into one and runs it); the `theme-set.d` path and its `$1`
contract are right; `omarchy-launch-or-focus-tui` derives the app-id the
Hyprland rule matches; and the Lua tag and menu keys are all real, including
`description`, which feeds menu search.

That pass also found a bug — the bar icon bypassed `WidgetButton`'s own
glyph rendering, so it ignored the bar's font and, on light themes, drew the
*frozen* state with more contrast than the live one. Fixed to `text` +
`dimmed`.

What is left needs hardware, and is genuinely the largest remaining unknown:

0. **Load the widget.** The checks above prove nothing is misspelled; they do
   not prove it runs. Icon lit/dim states, panel layout at both bar
   orientations, wheel and right-click, backend-missing message.
1. A user-installed theme's `colors.toml` (the 22 stock ones were parsed in
   Phase 1), including a light theme with `mode`.
2. `omarchy-launch-mimarchy` — that it opens in Foot and floats.
3. The theme hook end to end: switch theme, watch the LEDs follow.
4. The lighting path itself, which no amount of source reading covers.

## 4. Phase 1 — parity on v4 (ships first, as 0.2.0) — **done**

Goal: a v4 user is no worse off than a v3 user is today, before any widget
work. Small, and it unblocks everything else.

Shipped, with three findings worth carrying forward:

- **A bar icon is plugin-only.** Omarchy 4 draws bar widgets exclusively from
  shell plugins — there is no `shell.json` entry or drop-in that adds a
  standalone item. The Phase 1 stopgap is therefore a *menu* entry
  (`omarchy/mimarchy-menu.jsonc`), not an icon; the icon genuinely arrives
  with Phase 2 and cannot be pulled forward.
- **`muted` loses the legibility guard on 19 of 22 stock themes.** Measured
  across every shipped v4 theme, the `muted` key clears `MIN_CONTRAST` on
  only three (median ratio 2.25) — it is designed to sit low. The footer role
  therefore resolves to the plain foreground on most themes, exactly as v3's
  `color8` did. That is the guard working, not the mapping failing, and it is
  recorded in `theme.py` so it is not "fixed" later by mistake. Whether a hint
  bar deserves a lower bar than WCAG large-text is a real question, but it is
  a policy change and did not belong in a compatibility phase.
- **The fallback palette never worked.** `Palette.fallback()` used
  `bright_black` for the footer: valid in Rich, rejected by Textual (which
  wants `ansi_bright_black`, which Rich then rejects — as do `grey`, `grey50`,
  `dim`, `silver`). Any machine without an Omarchy theme hit a stylesheet
  error on startup, so the documented fail-soft path was itself a crash. It
  went unnoticed because a developer machine always has a theme to read. Fixed
  to a hex grey — the only vocabulary both engines share — with a test that
  parses every fallback value through both. This also un-broke 22 pre-existing
  test failures.

- **`theme.py`** — both palette dialects and both paths. `theme_dirs()` tries
  `$XDG_STATE_HOME/omarchy/current/theme` then the 3.x config path, reading
  the environment per call so it is testable. Dialect is detected from key
  presence (`muted`/`selection`/`bright_foreground` mark v4), canonical
  before numbered so a theme carrying both is read the way Omarchy reads it.
  Roles map onto `green`, `yellow`, `accent`, `muted`, and the documented
  `selection` / `bright_foreground` pairing; the v3 slot mapping is untouched.
  Verified against all 22 stock v4 themes: every role legible in every one,
  light and dark.
- **Float again** — `omarchy/mimarchy.lua` carries
  `o.window("org.omarchy.mimarchy-tui", { tag = "+floating-window" })`.
  Tagging rather than `float = true` keeps the standard centring and 875x600.
  The app-id is unchanged and still picks up v4's `terminal` tag for free
  (Omarchy matches `org\.omarchy\..*` for that one).
- **`install.sh` v3/v4 detection** — branches on which theme path exists,
  since 4.0 moved it and left no symlink, so the path *is* the version test.
  Prints matching instructions for v4, 3.x, or neither.
- **Launcher** — `bin/omarchy-launch-mimarchy` (moved out of `waybar/`) still
  prefers `omarchy-launch-or-focus-tui`, and now falls back to
  `xdg-terminal-exec` and then `$TERMINAL`, so it works off Omarchy too.
- **README** — install section split into "Omarchy 4" and "Omarchy 3.x
  (legacy)"; Waybar snippets moved to `legacy/waybar/`; no more Alacritty or
  `bluetui` references, neither of which is a v4 default.

## 5. Phase 2 — the native shell plugin (0.3.0) — **done**

The headline. Mimarchy is now an Omarchy shell plugin: `manifest.json` at the
repo root, id **`io.github.villenull.mimarchy`** (the marketplace's recommended
namespace; `omarchy.*` is reserved), kind **`bar-widget`**.

Shipped, with these decisions and findings:

- **One `Panel.qml`, not a widget/panel pair.** `panel` as a separate declared
  kind turned out to be the wrong read of the plugin API: Omarchy's own
  popup widgets (`omarchy.monitor`, `.audio`, `.network`, `.tailscale`) declare
  only `bar-widget` and use the `Panel` base type, which already owns the
  open/close/IPC lifecycle. Declaring `panel` too would have meant a second
  entry point for a floating window nothing summons independently.
- **The repo is the plugin.** `manifest.json` sits at the root, so
  `omarchy plugin add` on this URL clones a working checkout and
  `omarchy plugin update` fast-forwards it. Running `install.sh` from inside
  that directory leaves exactly one copy on disk instead of two; `install.sh`
  detects which case it is in and prints accordingly.
- **`mimarchy-ctl` came out better than expected.** It was specified as
  plumbing for the widget, but it is a good command in its own right —
  `status`, `speed`, `effect`, `display`, `link`, with `--json` for the widget
  and human output otherwise. It is now documented in the README as a
  first-class way to use the tool.
- **Shared code was extracted rather than duplicated.** `service.py` (the two
  systemd units and the calls that drive them) and `effects.nearest_speed` are
  now used by both the TUI and the CLI. Two copies of "which stop is this
  speed on" would have let the bar and the TUI disagree about the current
  speed, which is the kind of bug nobody reports precisely.
- **A second pre-existing crash, found by running the thing.**
  `hwmon._read_sensors_json` used `check=True` with no handler, so *any* machine
  without `lm_sensors` raised `FileNotFoundError` — on every TUI repaint, and
  now on every widget poll. Every consumer was already written to expect
  `None`, so the fix was to fail soft. Caught by running `mimarchy-ctl status`
  for real; the unit tests all stub the sensors, which is exactly why they
  missed it. `tests/test_hwmon.py` now exercises the real function.
- **The venv had to move out of the checkout.** `omarchy-plugin-validate`
  refuses any symlink under the plugin folder except beneath `.git`, and a
  virtualenv has four (even `--copies` leaves `lib64`). Since `omarchy plugin
  update` re-validates and rolls back, a `.venv` in the checkout would have
  made the plugin silently un-updatable — so `install.sh` now builds it in
  `~/.local/share/mimarchy/`. The symlink test mirrors the validator exactly,
  pruning only `.git`, so this cannot be reintroduced without a red test.
- **`mimarchy-ctl` was never on PATH.** `install.sh` symlinked `mimarchy-tui`
  and the launcher but not the CLI the widget invokes by bare name — which
  would have left the panel stuck on "backend not installed" on every stock
  install, with everything else appearing to work. It is symlinked now, and
  the installer warns if `~/.local/bin` is not on PATH.
- **Untested against a running shell.** No Quickshell, and no `qmllint`, exists
  in the development environment, so the QML is written against the real v4
  idioms and verified symbol-by-symbol against the shipped source — every
  component and property used is confirmed exported by `qs.Ui` / `qs.Commons`
  — but it has never been loaded. Two mistakes were caught this way already
  (`PanelActionButton` is a 22×22 *icon* button with no `text`, and the panel
  content needed `Toggle`/`Button` instead), and there may be more that only a
  running shell will show. `tests/test_manifest.py` stands in for
  `omarchy plugin validate`, which is the one part that *can* be checked here.

### Widget behaviour

- **At rest**: the 󰌵 glyph, tinted with the theme accent while lighting is
  animating, dimmed when `mimarchy-light.service` is stopped (LEDs frozen).
- **Click**: opens the **panel** — a small floating Quickshell popout in the
  bar's own style showing live state: effect + speed per target (or the
  linked pair), CPU/GPU temperature, fan RPM, cooler display on/off; with
  the two controls worth having outside the TUI: speed +/- and display
  toggle. A "open Mimarchy" row launches the floating TUI for everything
  else. (Scroll-on-icon for speed, right-click for display toggle as
  shortcuts.)
- **Settings schema** in the manifest (v4 surfaces these in the bar UI):
  show/hide temperatures, poll interval.

The TUI stays the full control surface; the panel is glanceable state plus
the two adjustments you want without opening anything. This mirrors how v4
itself treats Wi-Fi/Bluetooth (panel for the common case, TUI culture for
depth) — and it is exactly the split the competition (§7) doesn't have.

### How it talks to the daemons (no new IPC)

The architecture already has the right seam: the TUI never drives hardware,
it writes `$XDG_RUNTIME_DIR/mimarchy-lighting.json` atomically and
`mimarchy-lightd` polls it. The widget plugs into the same seam:

- **Read**: Quickshell `FileView` watching `mimarchy-lighting.json` for
  effect/speed/linked state; systemd unit state via a cheap
  `systemctl --user is-active` poll only while the panel is open.
- **Write**: never from QML. New **`mimarchy-ctl`** entry point
  (`mimarchy-ctl speed +`, `display toggle`, `effect <name>`,
  `status --json`) reusing `lightstate.py`'s atomic save and the existing
  systemctl wrappers. The widget shells out to it. One writer
  implementation, no atomicity logic reimplemented in QML — and the CLI is
  independently useful (Hyprland keybindings, scripting, the menu).
- **Sensors**: `mimarchy-ctl status --json` carries temps/RPM (reuses
  `hwmon.py`), polled ~2 s while the panel is open.

All logic lives in `mimarchy-ctl`, unit-tested next to the existing tests;
the QML stays declarative enough not to need a rig. Plugins run unsandboxed
inside the shell process, so QML-side minimalism is also a stability
courtesy: a broken widget is a broken shell.

### Theming and menu

- The widget/panel consume the shell's own theme tokens (the per-theme
  `shell.toml` machinery), so a theme switch restyles them live with the
  rest of the bar. No hardcoded colour, same rule as the TUI.
- A `~/.config/omarchy/extensions/omarchy-menu.jsonc` entry ("Mimarchy" under
  the appropriate submenu) comes along nearly for free via `mimarchy-ctl`.

### Repo layout and install split

One repo. `omarchy plugin add https://github.com/villenull/mimarchy` clones
everything into `~/.config/omarchy/plugins/io.github.villenull.mimarchy/`;
the manifest points at `quickshell/*.qml`. Plugin install deliberately runs
no hooks and no sudo, so the Python backend (venv, services, udev, OpenRGB)
stays on `install.sh` — the widget detects a missing backend and shows a
one-line "run install.sh" hint instead of erroring. The marketplace listing
carries this as its documented install step. `omarchy plugin validate` runs
in CI so the manifest can't rot.

## 6. Phase 3 — theme-following lighting (0.4.0) — **done**

Shipped. What it turned into, and the two judgement calls behind it:

- **`lightd` was not touched at all**, which was the goal. `TargetState` gained
  a `colour_role`, and the *resolved* RGB keeps being written to `colour`
  alongside it — so the daemon reads exactly what it always read and the
  rendering path took on no new failure mode. `mimarchy-ctl reload-theme`
  re-resolves roles and saves; the daemon's existing mtime poll does the rest.
  A theme switch reaches the LEDs in a frame, and nothing in the daemon knows
  a theme exists.
- **The brightness floor is a floor, not a scale.** Measured over the 8 LED
  roles across all 22 stock themes (173 defined colours): median HSV value
  0.70–0.87, with only 17 below 0.55 and 7 below 0.40. Scaling everything to a
  target brightness would therefore rewrite ~90% of colours to rescue ~10% —
  and that rewrite is what makes a carefully muted theme come back looking like
  a toy. Lifting only what falls under the floor leaves 156 of 173 exactly as
  authored. Hue and saturation are never touched.
- **A role that resolves to nothing keeps the last colour.** Three stock themes
  define no `orange`. Reverting to white, or going dark, would both be louder
  than simply leaving the previous orange in place until a theme with one comes
  back.
- **One theme entry in the TUI cycle, not eight.** The cycle is walked by
  repeated presses of one key, so each entry taxes everyone reaching the ones
  after it. `accent` is the entry that means "match my desktop"; the other
  seven roles are reachable from `mimarchy-ctl colour <role>`.
- **Found while testing: `accent` exists in *both* palette dialects.** v3 had it
  alongside the ANSI slots, so branching on dialect sent v3 themes to `color4`
  and silently ignored their real accent. LED roles now try the role's own name
  first and the ANSI slot only as a fallback, which is both simpler and correct.

### The light-theme colour policy, settled

Carried over from Phase 1, where it was deliberately left open. Resolved in
favour of **shifting a rejected colour along its own brightness** until it
clears `MIN_CONTRAST`, holding hue and saturation fixed — the "what a designer
would do by hand" option — rather than falling back to `accent` or to the plain
foreground.

The `accent` option was rejected on measurement: it keeps the palette themed
but makes `frame`, `header` and `accent` the same colour on the themes where it
fires, so catppuccin-latte still arrived as two distinct colours instead of
four. Shifting fixes both problems at once. Across all 22 stock themes:

| | before | after |
|---|---|---|
| roles collapsed to foreground, light themes | 1.6 of 4 | **0.0** |
| roles collapsed to foreground, dark themes | 1.0 of 4 | **0.1** |
| themes showing all four roles distinctly | 21 of 22 (latte showed 2) | **22 of 22** |
| roles failing the contrast bar | 0 | **0** |

The accessibility guarantee is unchanged — every role still clears the bar, and
the fallback to the foreground remains for a colour that cannot clear it at any
brightness (white on white). The shift is a binary search, so it is the
*smallest* change that works: latte's green moves `#40a02b` → `#3f9e2b`. The
remaining 0.1 is kanagawa, whose `accent` the theme itself sets equal to its
foreground — genuinely one colour, not a collapse.

## 6b. Phase 3 as originally specified

Today the *TUI* follows the theme but the *LEDs* show a fixed seven-colour
palette. v4's hooks make the obvious feature cheap, and the competition
(§7) makes it table stakes:

- **Theme as a colour source**: alongside white/red/…, a "theme" choice that
  resolves to the active theme's `accent` (and the named colors as a cycle).
  Static-on-accent makes the rig match the desktop; spatial effects can
  draw from the theme's named-colour ramp instead of the hue wheel.
- **Live re-theme**: `install.sh` drops a one-line script into
  `~/.config/omarchy/hooks/theme-set.d/` that calls
  `mimarchy-ctl reload-theme`; `lightd` re-resolves theme-sourced colours on
  its next frame. Switch themes, the LEDs follow within a frame — no TUI
  reopen. (The TUI keeps its read-at-startup behaviour; it's an overlay.)
- The persisted state stores the *role* ("theme accent"), not the resolved
  hex, so it keeps following across reboots and theme changes.
- **Settle the light-theme colour policy** — carried over from Phase 1, which
  deliberately did not decide it. The legibility guard is tuned for a dark
  background, and light themes pay for it: measured across the stock themes,
  a dark theme loses 1.0 of 4 roles to the plain foreground, a light theme
  1.6, and catppuccin-latte comes back in two colours instead of four because
  its `green` (2.98) and `yellow` (2.2) both just miss the 3.0 bar. The TUI
  stays readable, but it stops looking themed — on the one desktop whose
  selling point is that everything matches. Options, in preference order:
  fall back to `accent` (passes on 22 of 22, median 6.13) before the
  foreground, so a rejected role stays a theme colour; or darken the named
  colour toward `dark_foreground` until it clears the bar, which keeps the
  hue and is what a designer would do by hand. Either is a visible change to
  every light theme, which is why it wants deciding here rather than being
  slipped into a compatibility pass.

## 7. Phase 4 — generalize the lighting path — **done**

Shipped, developed in parallel with Phase 3 and merged. The substance:

- **`mimarchy-setup`** lists every detected device and zone, asks which to
  drive and how long each strip is, and writes `config.toml` — keeping a
  timestamped backup and carrying over the display ids and link toggle it has
  no opinion about. `--list` is a non-interactive listing, and is what a bug
  report should contain.
- **`detectors.py`** turns device names into OpenRGB detector names, which is
  guesswork by necessity: OpenRGB never says which detector produced which
  device, and the only way to ask is to run detection — the dangerous act
  itself. It refuses to guess rather than guessing wrong, and `detectors = [...]`
  in the config is the escape hatch.
- **`restrict-openrgb-detectors.py`** now derives its allowlist from the
  configured zones, keeps `--check`, and gained `--discover` (behind a typed
  confirmation) for the chicken-and-egg the narrowing creates: a list narrowed
  before your hardware was ever detected can never see your hardware.
  Deliberately does *not* fall back to the reference four when a config exists
  but derives nothing — that would enable one particular card's I2C detector
  because somebody else's rig needed it.
- **N zones** work end to end. Two places still assumed the pair and were
  fixed on merge: the TUI drew exactly two rows, and `lightd.plan` read
  `state.linked` as a global — so while CPU and GPU were linked (the default) a
  *third* one-LED zone running chase was rendered flat instead of being handed
  to its firmware. Linking is defined as that pair, and `_linked_pair` is now
  what both places ask.
- **Config robustness**: a malformed `[rgb.zones.*]` table is skipped with a
  message rather than raising, per-zone `leds` lengths are supported, and
  `rgb.py` resizes only the zones actually configured.

Two things worth carrying forward:

- **The agent's own first cut had a real bug, caught by running it.** Its
  ambiguity guard was "more than the largest real family", which let a
  four-card vendor line through — four I2C probes for cards the machine does
  not have, precisely the failure the guard exists to prevent. Tightened to
  "one match, or an exact one".
- **`install.sh` step 2 is still the one unavoidable #4888 exposure.** It runs
  `openrgb --list-devices` with every detector enabled, because the allowlist
  cannot be written into a config that does not exist yet. The old comment
  claimed this was safe because it is one-shot, which misstates the risk; it is
  now documented honestly. Only skippable on a machine that has run OpenRGB
  before, which is most of them.

## 7b. Phase 4 as originally specified

What "works on my exact rig" assumes today, and what marketplace users need:

1. **Zone discovery instead of hand-editing.** `mimarchy-setup`: connect to
   OpenRGB, list devices/zones, pick zones and zone size interactively,
   write `config.toml`. The substring-match config already supports
   arbitrary hardware; this removes the read-the-comments-and-guess step.
2. **Detector restriction beyond one board and one card.**
   `restrict-openrgb-detectors.py` currently applies a fixed four-detector
   allowlist. Generalize: derive the allowlist from the devices selected in
   setup, keep `--check`, keep the freeze warning (OpenRGB #4888) front and
   centre — it is the sharpest edge in the whole install, and handling it
   well is a credibility feature, not an embarrassment.
3. **Graceful cooler-display absence.** Mostly true already
   (`display.known`); finish it: hide the display row in the TUI and panel,
   skip the udev step in `install.sh`, when no `5131:2007` panel exists.
4. **N zones, not two.** `cpu_fans`/`gpu` are just config keys; audit that
   nothing hardcodes the pair, so a third strip is one more `[rgb.zones.*]`
   block with its own row.
5. **Honest support tiers.** README keeps a "verified on / should work on /
   won't work on" table. Overpromising is how a hardware listing collects
   one-star bug reports; the issue template asks for
   `openrgb --list-devices` output.

Explicitly out of scope: other cooler LCDs. The display driver stays
`5131:2007`-specific; a pluggable display-driver interface is a fast-follow
*if* the release attracts contributors with other panels — it is also the
moat (§8), so the protocol notes in `docs/hardware-notes.md` are the
recruiting poster.

## 8. Phase 5 — public release on the Omarchy Plugin Marketplace

### The competitive landscape (checked against the full registry, Aug 22)

The niche is no longer empty — two OpenRGB plugins appeared *within the last
week*:

- **OmaRGB** (`io.github.vonsensey.omargb`, listed Aug 21) — the closest and
  most ambitious: paints *every* OpenRGB device (keyboards, mice, coolers,
  RAM, boards) with the resolved theme palette via per-zone semantic roles,
  repaints on theme switch, dims on lock, flashes urgent windows, ships a
  diagnostics "Doctor" and a scriptable CLI.
- **RGB Lighting** (`didlix.case-rgb`, listed Aug 15) — bar widget + popout:
  on/off, preset swatches, hue/brightness sliders, follow-theme-accent
  toggle, bundled CLI, OpenRGB as a user service, theme-hook wiring.
  Architecturally the same surface Mimarchy occupies.
- Adjacent: vendor keyboard-RGB plugins (ASUS, Ajazz, Acer), WLED strips,
  Elgato lights, and a dozen hwmon/fan bar widgets. First-party Omarchy RGB
  remains one accent colour for two laptop keyboards.
- **Nobody** drives an AIO/cooler LCD, and no CoolerControl/liquidctl
  integration exists anywhere in the registry.

### Differentiation — what the listing leads with

Don't out-broaden OmaRGB; be the thing it isn't:

1. **The cooler display.** Live CPU telemetry on the cooler's own panel over
   a reverse-engineered HID protocol. Unique in the ecosystem; the demo GIF
   and the listing preview open with it.
2. **Animated, phase-locked effects.** Everyone else sets colours; Mimarchy
   *renders* rainbow/spectrum/chase/breathing from one clock so CPU and GPU
   stay in phase — with measured receipts in `hardware-notes.md` for why
   controller-native effects can't do this (no speed control, free-running
   clocks). Theme-following (Phase 3) closes the one gap where OmaRGB is
   ahead.
3. **Keyboard-driven, not mouse-only.** The bar panel takes a cursor —
   hjkl/arrows, direct 1-6/0 effect selection — so lighting is drivable start
   to finish without a mouse, in the `btop`/`lazygit` tradition Omarchy
   celebrates, with no second window to open for it.
4. **The safety story.** The detector-restriction tooling that prevents a
   documented hard freeze (OpenRGB #4888) — worth a "why is this safe"
   section in the listing, since the security-baseline flags (§below) will
   make reviewers look.
5. **Craft details as brand**: WCAG-checked palette legibility, measured
   display-blanking timings, comments that cite experiments. The docs *are*
   marketing in this community.

Positioning line to the effect of: *"OmaRGB paints your devices to match
your theme. Mimarchy makes your cooler and GPU perform it — synced animated
effects, live telemetry on the cooler's display, all driven from the bar."*

### Submission mechanics

1. Pre-flight: `manifest.json` (`schemaVersion: 1`, permanent id
   `io.github.villenull.mimarchy`), README with install *and removal*
   instructions, MIT license (have), documented external deps (openrgb,
   python, optional nct6687d), root `preview.png`, `omarchy plugin
   validate` green in CI.
2. Listing metadata: category **Hardware**; tags from the allowed set —
   `bar`, `quickshell`, `system`.
3. Submit via the marketplace's GitHub issue form
   (`HANCORE-linux/omarchy-plugin-marketplace`, `SUBMISSION.md`).
4. Expect the automated security baseline to flag capabilities:
   `service-management` (systemd user units), `installer` (install.sh),
   udev/sudo steps → outcome `review-required`, which is normal for the
   Hardware category. Make the reviewer's job easy: a SECURITY section in
   the README explaining exactly what needs root (one udev rule, copied by
   the *user*, never by the plugin) and what runs unprivileged.
5. Installs track the repo's HEAD (updates are fast-forward pulls), so
   `main` must always be shippable from listing day: tagged releases for
   humans, boring `main` for the installer.
6. Note: a marketplace plugin competition ran Aug 19–24 with more likely to
   follow; a polished listing positions Mimarchy for the next one.

### Launch sequence

1. 0.4.x on `main`, validated, preview media recorded (GIF of a theme
   switch restyling bar + panel + LEDs + cooler display in one cut — the whole
   pitch in five seconds).
2. Submit listing; answer review.
3. Announce where the ecosystem actually looks: the omarchy subreddit,
   omarchynews.com, an X post tagging the plugin account DHH boosts from.
4. Post-launch: watch issues for two weeks with the hardware-report
   template; the first three "works on my rig too" reports go into the
   support-tier table.

## 9. Risks

| Risk | Mitigation |
|---|---|
| Plugin API is a week old and moving (`schemaVersion: 1`) | Keep QML thin, logic in `mimarchy-ctl`; pin what the manifest allows; CI runs `omarchy plugin validate` |
| Unsandboxed plugin: a widget bug degrades the user's whole shell | Minimal QML, no busy polling while closed, fail-hidden when backend absent |
| OmaRGB velocity — it could add cooler-LCD or effects | Ship §6 fast (it's the cheap phase); the HID display and the measured-effects engine are the defensible parts; be gracious neighbours, not clones |
| v3 palette dialect lingers in the wild | Dual-dialect loader with fixtures for both; fallback palette already fails soft |
| Store review balks at udev/sudo-adjacent install | Root steps are user-run and documented, never plugin-run; lead with the safety story |
| Omarchy updates overwrite integration points | Everything lands in user-owned paths v4 designates for exactly this (`plugins/`, `hooks/`, `extensions/`, `themed/`) |
| Wider hardware audience hits OpenRGB quirks unseen here | Setup wizard, honest tiers, issue template with `--list-devices` output |

## 10. Sequencing

| Milestone | Contents | Ships as |
|---|---|---|
| Phase 0 | v4 facts confirmed on hardware | `docs/omarchy-4-notes.md` |
| Phase 1 | Theme loader (both dialects/paths), float rule, install detection, legacy split | **0.2.0** — "works on Omarchy 4" |
| Phase 2 | `manifest.json`, bar widget + panel, `mimarchy-ctl`, menu entry | **0.3.0** — the visible upgrade |
| Phase 3 | Theme-following lighting + `theme-set` hook | **0.4.0** |
| Phase 4 | Setup wizard, generalized detector restriction, N-zone audit | 0.4.x |
| Phase 5 | Marketplace submission + launch | listing |

Phases 3 and 4 can proceed in parallel once `mimarchy-ctl` exists; nothing
is submitted until the wizard is in, because the first cohort of marketplace
installs onto unknown hardware is the moment the generalization work either
exists or turns into bug reports.
