# Mimarchy

**Omarchy-native LED control for CPU cooler fans and GPU.**

A single-screen TUI for the ARGB lighting on a CPU cooler's fans and a graphics
card, plus the cooler's built-in temperature display. It is built for
[Omarchy](https://omarchy.org/) specifically: it takes every colour from your
active Omarchy theme at startup, and it opens as a floating terminal window in
the style Omarchy uses for its own TUIs. Lighting reaches the motherboard
headers and the card over the OpenRGB SDK; the cooler's display is driven
directly over HID with a protocol reverse-engineered for it.

Runs on **Omarchy 4** and on Omarchy 3.x. Both theme formats are read, so an
install keeps working across the upgrade.

![Mimarchy demo](docs/demo.gif)

## What it does

- **Effects** — static, rainbow, spectrum, chase, breathing, and *unhinged*,
  all rendered in software from one clock so both devices stay in phase.
- **Linked or independent** — CPU and GPU move together by default; unlink to
  drive them separately.
- **Theme-driven** — no colour is hardcoded. Switch Omarchy themes and the TUI
  follows on next open.
- **Sensors** — CPU and GPU temperature, fan RPM.
- **Cooler display** — streams live temperature and fan speed to the panel.

## Hardware

Developed against an ASUS PRIME X870-P WIFI, a Sapphire RX 9070 XT Nitro+, and
a Balam Rush Heliux Pro HEX75 cooler. Anything OpenRGB can drive should work for
the lighting; `[rgb.zones]` in the config is how you point it at your own
devices. The cooler display is specific to that USB device (`5131:2007`).

## Install

```bash
git clone https://github.com/villenull/mimarchy
cd mimarchy
./install.sh
```

That creates a virtualenv, narrows OpenRGB's detector list, installs and starts
the user services, and prints the two or three steps that need root or a config
merge. It detects which Omarchy you are on and prints the matching desktop
integration. Then:

```bash
mimarchy-tui
```

Requires Python 3.11+, `openrgb`, and a running Wayland session. Fan RPM
additionally needs the out-of-tree `nct6687d` driver — temperatures and lighting
work without it.

### Desktop integration

**Omarchy 4.** This repo is also an Omarchy shell plugin, so the tidiest install
is to let Omarchy do the cloning and then run the installer from where it landed
— one checkout that `omarchy plugin update` keeps current:

```bash
omarchy plugin add https://github.com/villenull/mimarchy --enable
~/.config/omarchy/plugins/io.github.villenull.mimarchy/install.sh
```

That gives you the bar widget: a bulb icon that dims when the LEDs are frozen,
and a panel with effect, speed, temperatures, fan RPM, and toggles for the
cooler display and the CPU/GPU link. Left click opens it, right click toggles
the display, scroll changes speed. The TUI is one click away and is still the
place to actually choose effects.

The virtualenv is created in `~/.local/share/mimarchy/` rather than inside the
checkout, which matters here: `omarchy plugin validate` refuses a symlink
anywhere in a plugin folder outside `.git`, a virtualenv contains several, and
`omarchy plugin update` re-validates and rolls back — so a venv in the checkout
would quietly make the plugin un-updatable.

Two optional extras, both printed by the installer:

| File | Merge into | Gives you |
|---|---|---|
| [`omarchy/mimarchy-menu.jsonc`](omarchy/mimarchy-menu.jsonc) | `~/.config/omarchy/extensions/omarchy-menu.jsonc` | Mimarchy in the Omarchy menu and its search |
| [`omarchy/mimarchy.lua`](omarchy/mimarchy.lua) | `~/.config/hypr/hyprland.lua` | the TUI floats instead of tiling |

The plugin deliberately installs no backend of its own — `omarchy plugin add`
never runs install hooks or asks for sudo, which is exactly the property that
makes it safe to run. Until `install.sh` has been run, the widget says so
instead of drawing an empty panel.

**Omarchy 3.x (legacy).** The Waybar module is kept in
[`legacy/waybar/`](legacy/waybar/): merge `mimarchy-module.jsonc` and
`mimarchy-style.css` into `~/.config/waybar/`, add `"custom/mimarchy"` to
`modules-right`, and add the `windowrule` line the installer prints to
`~/.config/hypr/hyprland.conf`.

> **One ordering constraint the script handles for you:** OpenRGB's broad
> GPU/I2C detection is a documented total-system freeze on some cards
> ([#4888](https://gitlab.com/CalcProgrammer1/OpenRGB/-/issues/4888)), and its
> service starts at login. `install.sh` narrows the detector list *before*
> starting the server for the first time. If you set this up by hand, do it in
> that order — and re-run `tools/restrict-openrgb-detectors.py` after ever
> opening the OpenRGB GUI, which rewrites the config.

## Keys

| Key | Action |
|---|---|
| `1`–`6` | static / rainbow / spectrum / chase / breathing / unhinged |
| `0` | off |
| same number again | next colour, for effects that take one |
| `←` `→` | speed down / up (`-` and `+` also work) |
| `↑` `↓` | move the selection |
| `u` | link / unlink CPU and GPU |
| `d` | cooler display on / off |

There is deliberately no quit key — this is an overlay, closed by closing its
window the way Omarchy's own floating TUIs are. `Ctrl+C` still works.

## Without the TUI

`mimarchy-ctl` drives the same state from a script, a Hyprland keybinding, or
the bar widget — which is exactly what the widget does, rather than reaching
into the state file itself.

```bash
mimarchy-ctl status            # or --json, which is what the widget reads
mimarchy-ctl effect rainbow
mimarchy-ctl speed +           # or -
mimarchy-ctl display toggle    # on / off / toggle
mimarchy-ctl link toggle
```

Writes go through the same atomic write-then-rename the TUI uses, so a bar click
and a keypress cannot interleave into a half-written file. Nothing here talks to
hardware; `mimarchy-lightd` still owns that.

## Notes

Lighting only animates while `mimarchy-light.service` runs; stopping it freezes
the LEDs on their last frame. The cooler display has no off command in its
protocol — `d` stops the telemetry stream, and the panel blanks itself about 50
seconds later.

[docs/hardware-notes.md](docs/hardware-notes.md) covers the parts that were not
obvious: why zone sizing is mandatory, why effects are rendered rather than run
on the controllers, how the display protocol was worked out, and every
measurement behind those decisions.

## License

[MIT](LICENSE).
