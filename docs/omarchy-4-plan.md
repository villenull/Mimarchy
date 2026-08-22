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

## 3. Phase 0 — verify on a live Omarchy 4 machine

The research above is from source; spend an hour confirming behaviour on the
actual dev machine after `omarchy-upgrade-to-quattro`:

1. `cat ~/.local/state/omarchy/current/theme/colors.toml` — confirm the key
   set across two or three themes (incl. one light theme with `mode`).
2. Launch `omarchy-launch-mimarchy` — confirm it opens (in Foot), tiles, and
   that the TUI's fallback palette path behaves.
3. `omarchy plugin clone omarchy.clock` — read a built-in bar widget to learn
   the house QML style, how widgets consume shell theme tokens
   (`shell.toml`), and the popout/panel idioms.
4. Confirm the `theme-set` hook fires with the theme name in `$1`.
5. Record findings in `docs/omarchy-4-notes.md`.

## 4. Phase 1 — parity on v4 (ships first, as 0.2.0)

Goal: a v4 user is no worse off than a v3 user is today, before any widget
work. Small, and it unblocks everything else.

- **`theme.py`**: support both palette dialects and both paths. Try
  `$XDG_STATE_HOME/omarchy/current/theme` first, then the old config path
  for 3.x. Detect dialect by key presence; map roles onto the new semantic
  keys directly — `frame`→`green`, `header`→`yellow`, `accent`→`accent` (a
  real key at last), `footer`→`muted`, selection pair from `selection` — and
  keep the slot mapping for v3 files. The legibility (`_legible`) and
  fail-soft logic is dialect-independent and stays. This module is where the
  v4 work has tests: fixture `colors.toml` files in both dialects.
- **Float again**: keep the `org.omarchy.mimarchy-tui` app-id (it still gets
  v4's `terminal` tag for opacity/theming), and have `install.sh` print the
  v4 Lua rule for `~/.config/hypr/hyprland.lua`
  (`o.window({ tag = "+floating-window", match = { class = "org.omarchy.mimarchy-tui" } })`
  — exact form confirmed in Phase 0) alongside the old `.conf` line for 3.x.
- **`install.sh` v3/v4 detection**: `~/.local/state/omarchy/current/theme`
  present → v4 instructions; `~/.config/waybar/` present → legacy
  instructions. Move `waybar/` to `legacy/waybar/` with a note.
- **Terminal-agnostic docs**: stop saying Alacritty anywhere; the launcher
  already inherits whatever `xdg-terminal-exec` resolves.
- **README**: install section split into "Omarchy 4" and "Omarchy 3.x
  (legacy)".

## 5. Phase 2 — the native shell plugin (0.3.0)

The headline. Mimarchy becomes a proper Omarchy shell plugin: `manifest.json`
at the repo root, id **`io.github.villenull.mimarchy`** (the marketplace's
recommended namespace; `omarchy.*` is reserved), kinds **`bar-widget`** +
**`panel`**.

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

## 6. Phase 3 — theme-following lighting (0.4.0, differentiation-driven)

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

## 7. Phase 4 — generalize the lighting path

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
3. **TUI + widget, not widget-only.** A real keyboard-driven control surface
   in the `btop`/`lazygit` tradition Omarchy celebrates, with the bar panel
   for the glanceable 90%.
4. **The safety story.** The detector-restriction tooling that prevents a
   documented hard freeze (OpenRGB #4888) — worth a "why is this safe"
   section in the listing, since the security-baseline flags (§below) will
   make reviewers look.
5. **Craft details as brand**: WCAG-checked palette legibility, measured
   display-blanking timings, comments that cite experiments. The docs *are*
   marketing in this community.

Positioning line to the effect of: *"OmaRGB paints your devices to match
your theme. Mimarchy makes your cooler and GPU perform it — synced animated
effects, live telemetry on the cooler's display, and a TUI to drive it."*

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
   switch restyling bar + TUI + LEDs + cooler display in one cut — the whole
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
