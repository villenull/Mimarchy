# Mimarchy

**Omarchy-native LED control for CPU cooler fans and GPU.**

A bar panel for the ARGB lighting on a CPU cooler's fans and a graphics card,
plus the cooler's built-in temperature display. It is built for
[Omarchy](https://omarchy.org/) specifically: it takes every colour from your
active Omarchy theme at startup, and it lives in the bar the way Omarchy's own
first-party panels do — click the icon, get the panel, nothing else to open.
Lighting reaches the motherboard headers over USB HID and the card over I2C on
the card itself, driven directly with no daemon in between; the cooler's
display is driven directly over HID with a protocol reverse-engineered for it.

Runs on **Omarchy 4**.

![Mimarchy panel, linked](docs/panel-linked.png)
![Mimarchy panel, unlinked](docs/panel-unlinked.png)

Above: every zone linked, one shared set of controls. Below: unlinked, one
block per zone. Both are the panel itself — there is nothing behind it.

## What it does

- **Effects** — static, rainbow, spectrum, chase, breathing, and *unhinged*,
  all rendered in software from one clock so both devices stay in phase.
- **Linked or independent** — CPU and GPU move together by default; unlink to
  drive them separately.
- **Theme-driven, including the LEDs** — no colour is hardcoded. The panel
  takes its palette from your Omarchy theme, and the lighting itself can too:
  pick `theme` as a colour and the strips follow every theme switch, live.
- **Cooler display** — streams live temperature and fan speed to the panel.

## Hardware

Developed against an ASUS PRIME X870-P WIFI, a Sapphire RX 9070 XT Nitro+, and
a Balam Rush Heliux Pro HEX75 cooler. `mimarchy-setup` finds your devices and
writes the config for you — it lists every detected zone, asks which ones to
drive and how long each strip is. `mimarchy-setup --list` prints what it sees
without changing anything, which is also the right thing to paste into a bug
report. The cooler display is specific to that USB device (`5131:2007`).

| Tier | What |
|---|---|
| Verified | ASUS PRIME X870-P WIFI headers, Sapphire RX 9070 XT Nitro+, Balam Rush HEX75 cooler display |
| Should work | several strips of different lengths; boards other than ASUS, given the same Aura USB protocol |
| Won't work | any cooler LCD other than `5131:2007`; firmware effects on hardware whose speed curve has not been measured (they run, at approximately the right rate) |

## Install

```bash
omarchy plugin add https://github.com/villenull/mimarchy --enable
```

That is the entire install: the panel starts the backend itself, from the
checkout Omarchy just cloned. `omarchy plugin update` keeps that checkout
current, so there is a single copy on disk.

The cooler display and the Aura controller sit on root-only hidraw nodes, the
one thing the plugin cannot do for itself. Root writes exactly the two lines
you can read here — not a file from this user-writable checkout
(`udev/99-mimarchy.rules` keeps the same two lines for reference; a test
asserts they never drift). The card needs no rule: its LED controller hangs
off I2C adapters that already carry uaccess.

```bash
printf '%s\n' \
  'SUBSYSTEM=="hidraw", ATTRS{idVendor}=="5131", ATTRS{idProduct}=="2007", TAG+="uaccess", RUN{builtin}+="uaccess", GROUP="input", MODE="0660"' \
  'SUBSYSTEM=="hidraw", ATTRS{idVendor}=="0b05", ATTRS{idProduct}=="19af", TAG+="uaccess", RUN{builtin}+="uaccess", GROUP="input", MODE="0660"' \
  | sudo tee /etc/udev/rules.d/99-mimarchy.rules >/dev/null
sudo udevadm control --reload-rules
sudo udevadm trigger --action=add --subsystem-match=hidraw
```

Then unplug and replug the cooler, or reboot. The panel surfaces this itself
when the daemon reports it cannot open a device.

On hardware other than the reference set below, run the config wizard before
expecting light. `bin/` is not on PATH, so it is invoked through the system
interpreter with the plugin path spelled out:

```bash
/usr/bin/python3 ~/.config/omarchy/plugins/io.github.villenull.mimarchy/bin/mimarchy-setup
```

It lists every detected zone, asks which ones to drive and how long each
strip is, and writes the config; the lighting daemon picks the new file up
on its next poll. `/usr/bin/python3 .../bin/mimarchy-setup --list` prints
what it sees without changing anything, which is also the right thing to
paste into a bug report.

Headless or no Omarchy desktop: run the backend directly from the checkout —
`/usr/bin/python3 bin/mimarchy-lightd` renders the lighting,
`/usr/bin/python3 bin/mimarchy-displayd` streams the cooler display, and
`/usr/bin/python3 bin/mimarchy-ctl status` (or `--json`) reports both.

Uninstall: `omarchy plugin remove io.github.villenull.mimarchy` stops the
panel and its daemons and removes the checkout. Optionally also remove the
rule (`sudo rm /etc/udev/rules.d/99-mimarchy.rules`, then reload udev);
nothing else was ever installed outside the plugin folder.

Requires Python 3.11+ and a running Wayland session; the backend itself is
stdlib-only, so there is nothing else to install. Fan RPM additionally needs
the out-of-tree `nct6687d` driver — temperatures and lighting work without it.

> History note: up to 0.4.5 the lighting went through the OpenRGB SDK server.
> Since 0.5.0 both controllers are driven directly — raw hidraw writes for
> the board, I2C for the card — and OpenRGB is gone entirely.

### Desktop integration

**Omarchy 4.** The checkout above already is the plugin, so the bar widget
arrives with it: a bulb icon that dims when the LEDs are frozen, and a panel
with effect, colour and speed for every zone, plus toggles for the
cooler display and the link. Left click opens the panel, right click toggles
the display, middle click toggles the link. There is no second window behind
it — the panel is the entire interface, effects included.

A keybinding is worth adding too, for opening the panel without touching the
mouse. Every first-party Omarchy panel answers to the same idiom — `SUPER +
CTRL + B` for Bluetooth, `SUPER + CTRL + D` for Display, and so on — so this
follows it:

```lua
o.bind("SUPER + CTRL + M", "Mimarchy", "omarchy-shell shell toggle io.github.villenull.mimarchy")
```

in `~/.config/hypr/hyprland.lua`. `SUPER + CTRL + <letter>` is claimed for
every letter except `G`, `J`, `M`, `U` and `Y` — checked against every binding
in `/usr/share/omarchy/default/hypr/bindings/*.lua` (`utilities.lua`,
`applications.lua`, `clipboard.lua`, `media.lua`, `tiling.lua`,
`voxtype.lua`). `M` is the one that reads as this plugin's name; pick another
free letter if your own config has already claimed it.

One optional extra:

| File | Merge into | Gives you |
|---|---|---|
| [`omarchy/mimarchy-menu.jsonc`](omarchy/mimarchy-menu.jsonc) | `~/.config/omarchy/extensions/omarchy-menu.jsonc` | Mimarchy in the Omarchy menu and its search |

## Keys

The panel takes a keyboard cursor, the same way Omarchy's other bar panels do:
open it, then move without ever touching the mouse.

| Key | Action |
|---|---|
| `h` `j` `k` `l`, arrow keys | move the cursor |
| `Enter` / `Space` | activate whatever the cursor is on |
| `1`–`6` | set the cursor's zone to static / rainbow / spectrum / chase / breathing / unhinged |
| `0` | set the cursor's zone to off |
| `+` `-` | speed up / down, every zone |
| `u` | link / unlink all zones |
| `d` | cooler display on / off |
| `Escape` | close the panel |

`1`–`6` and `0` are scoped to wherever the cursor is standing: with the cursor
on the GPU's effect row, pressing `3` sets the GPU to spectrum without
touching anything linked to it. `u`, `d`, `+` and `-` stay global regardless of
the cursor — `u` and `d` mirror the icon's own middle- and right-click, and
`+`/`-` are the coarse every-zone speed nudge (the icon's scroll wheel does
nothing; there is no zone for a scroll to pick). Keeping these four global
means the same keystroke never means two different things depending on a
cursor the user may not have summoned yet. The first press of a movement key
only reveals the cursor rather than moving it, so it never jumps in from
off-screen.

## mimarchy-ctl

The panel is not the only interface. `mimarchy-ctl` drives the same state from
a script, a Hyprland keybinding, an SSH session, or a machine with no Omarchy
desktop at all — and it is exactly what the panel itself calls, rather than
reaching into the state file directly.

```bash
/usr/bin/python3 bin/mimarchy-ctl status            # or --json, which is what the widget reads
/usr/bin/python3 bin/mimarchy-ctl effect rainbow
/usr/bin/python3 bin/mimarchy-ctl speed +           # or -
/usr/bin/python3 bin/mimarchy-ctl colour accent     # follow the theme; or green, red, ... or #ff0044
/usr/bin/python3 bin/mimarchy-ctl display toggle    # on / off / toggle
/usr/bin/python3 bin/mimarchy-ctl link toggle
```

(From the plugin checkout; `bin/` is not on PATH, so the system interpreter
is named outright.)

Writes go through an atomic write-then-rename, so a panel click and a scripted
write cannot interleave into a half-written file. Nothing here talks to
hardware; `mimarchy-lightd` still owns that.

`status` also reports what the daemon actually found: a configured zone whose
controller did not answer shows as `gpu: NOT DETECTED` (and as `missing_zones`
in the JSON), next to the state that would otherwise look healthy. The daemon
logs the same line when it starts, after waiting a bounded 20 s for a
controller that is merely slow to appear.

### Lighting that follows your theme

Give a colour a *role* — `accent`, `red`, `orange`, `yellow`, `green`, `cyan`,
`blue`, `magenta` — instead of a value, and it re-resolves whenever you change
Omarchy themes. In the panel, `theme` is the first chip in each zone's swatch
row, ahead of the seven fixed colours. The state file stores the role, so it
keeps following across reboots.

The lighting daemon keeps a hook in `~/.config/omarchy/hooks/theme-set.d/`
pointing at `omarchy/theme-set.d/mimarchy`, which calls
`bin/mimarchy-ctl reload-theme` after a theme switch; the daemon picks the new
colour up on its next frame, so the strips change with the wallpaper rather than
on the next reboot. Fixed hex colours are never touched by it.

Theme colours are used as authored, with one exception: a colour dimmer than
55% brightness is lifted to that floor, hue and saturation untouched. Measured
across the 22 stock themes, that lifts 17 of 173 colours and leaves 156 exactly
as the theme author set them — a floor rather than a scale, so a deliberately
muted theme still looks muted on the strip.

## Notes

Lighting only animates while the lighting daemon runs; stopping it freezes
the LEDs on their last frame. The cooler display has no off command in its
protocol — `d` stops the telemetry stream, and the panel blanks itself about 50
seconds later.

[docs/hardware-notes.md](docs/hardware-notes.md) covers the parts that were not
obvious: why zone sizing is mandatory, why effects are rendered rather than run
on the controllers, how the display protocol was worked out, and every
measurement behind those decisions.

## License

[MIT](LICENSE).
