# Hardware notes

Why the code is shaped the way it is, and the handful of things about this
hardware that are not obvious. Everything here was measured on the device.

For what Mimarchy is and how to install it, see the [README](../README.md).

## ARGB

**Zone sizing is configuration, not negotiation.** A strip shows whatever
frames arrive, so the backend has to know how many LEDs to render for. Every
zone renders `leds_for` LEDs — the zone's own `leds` when set, else
`[rgb] zone_size` (15).

Set it to the strip's *real* length. Too short leaves the tail dark; too long is
worse than it sounds, because spatial effects span the zone rather than the LEDs
— at 60 on a 15-LED strip, rainbow shows a quarter of the hue wheel and is
indistinguishable from spectrum.

**Colour is written in a direct-drive mode.** `mimarchy/aura.py` puts the
board's channels in Direct and `mimarchy/nitro.py` puts the card in
External Control before writing colours; a controller left in an effect mode
keeps running its own animation and ignores the frame.

**Two independent controllers.** The motherboard's Aura chip is USB HID
(`0b05:19af`, `src/mimarchy/aura.py`); the GPU has its own reached over I2C
*on the card* (`/dev/i2c-7`, address `0x28`, `src/mimarchy/nitro.py`).
Different hardware, different buses.

**If the card vanishes, check the bus before anything else.**
In September 2026 the card's LED controller stopped answering at `0x28` — on
the OEM bus and on every other bus the card exposes — across a warm reboot,
while the amdgpu adapter and the kernel's I2C messages were unchanged.
Presence is one `read_byte(0x28)`, so the same question can be asked by hand,
in a few milliseconds:

    i2cdetect -y -r 7 0x28 0x28     # `28` = the controller answers; `--` = silent

A silent controller is the microcontroller itself, not the driver stack. The
slot's standby rail keeps it powered through reboots and shutdowns, so the
reset is a cold boot: power off, PSU switch off for ~30 s, power on.
`mimarchy-ctl status` now says `gpu: NOT DETECTED` in that state, and the
daemon logs it at startup, so it is no longer something to discover a week
later (`docs/gpu-incident-2026-09.md`).

The GPU's ARGB connector is an **output** — a source for syncing other devices to
the card, not an input — so no motherboard header can drive the card's LEDs, and
I2C is the only route. Verified physically: rewiring the GPU onto the motherboard
chain changed nothing, and blacking out every motherboard zone left the GPU lit.

## Why there is no detector list anymore

Up to 0.4.5 the lighting went through the OpenRGB SDK server, and its broad
GPU/I2C detection could hard-freeze this machine
([#4888](https://gitlab.com/CalcProgrammer1/OpenRGB/-/issues/4888), open) —
so the install narrowed its detector list before the server ever started,
and the config carried a `detectors = [...]` allowlist. Since 0.5.0 both
controllers are driven directly (`src/mimarchy/aura.py` over hidraw,
`src/mimarchy/nitro.py` over I2C), which only ever touches the board's own
USB device and the card's own bus: there is nothing to narrow and no server
to order around. `load_config` still tolerates old files that contain a
`detectors` key, and ignores it.

## Effects are rendered in software, not by the controllers

`mimarchy-lightd` renders every effect itself and writes per-LED colours to both
devices from one clock. The controllers' own effect modes could not be made to
work together:

- **The board has no speed control.** `HAS_SPEED` is unset on every one of its
  modes, so its rainbow ran at whatever rate the firmware chose.
- **Each controller free-runs.** In spectrum or breathing they showed different
  colours at the same instant and drifted further apart over time.
- **Mode switching is slow and drops packets**, so cycling effects quickly left
  the GPU a mode behind.

One clock fixes all three. Measured: at speed 1.0 the strip and the GPU run
67.2 and 71.2 deg/s under rainbow, 75.0 and 73.9 under spectrum, against 72.0
expected — and spectrum's instantaneous hues agree within 8 degrees.

The cost is that lighting only animates while the daemon runs; stopping it
freezes the LEDs on their last frame.

**What the stream costs, measured.** The render is not the cost; the I2C
write to the card's controller is. Each write is a transaction the amdgpu
driver bit-bangs with busy-waits — about 1.5 ms of CPU per write — while the
board's USB writes are nearly free. Measured on the same effect (`unhinged`,
every frame different) at 30 fps:

| Configuration | CPU |
|---|---|
| board + card, every frame sent (≤ 0.4.3) | 5.1 % of one core |
| board + card, unchanged frames skipped (0.4.4) | 3.5 % |
| board only, card held static | 0.5 % |
| board + card, card capped at 10 writes/s (0.4.5) | **1.2 %** |
| board + card, `static` | 0.4 % |

So the daemon sends a frame only when it differs from the last one that
went out (re-sent once a second regardless, so a dropped packet cannot
strand a zone), and writes a one-LED zone at most ten times a second: one
flat colour stepping at 10 Hz is indistinguishable from 30, since there is
no spatial motion to smooth, and the strip keeps the full rate. Memory is
25–45 MB resident; the "8 % RAM" a system monitor shows is virtual address
space from mapping the GPU. `mimarchy-lightd --fps 20` remains available but
no longer buys much.

### Except on the GPU, which is one LED

The card exposes a single controllable LED for a bar with many physical
segments. Rendering can only send one colour per controllable LED, so a hue wave
and a travelling head both arrive as one flat colour — rainbow becomes spectrum,
chase becomes static. The card's own `Rainbow Wave` and `Runway` animate across
the bar properly, so `lightd.plan` routes those to firmware:

    rainbow            -> the card's Rainbow Wave, linked or not
    chase, unlinked    -> the card's Runway
    everything else    -> rendered, one clock, in phase

Chase stays rendered while linked because it carries a chosen colour and every
firmware mode here reports `color_mode=0` and ignores one — handing it over put
red on the strip and yellow on the bar. Rainbow has no chosen colour and the
card's wheel is the same wheel, so there is nothing to mismatch.

Firmware cannot share the clock, so its rate is matched by *period* rather than
by ladder position: `firmware_speed_for_period` inverts a measured power law
(Rainbow Wave takes 0.61 s per pass at speed field 10, 10.63 s at 250) to ask the
card for the same period the renderer is using. Mapping rung-to-rung instead
lines up only the ends and leaves the card at roughly twice our rate everywhere
between.

## Fan sensors need an out-of-tree driver

`nct6687d` (AUR: `nct6687d-dkms-git`), loaded with `force=1` — the driver gates
on a vendor allowlist this board is not on and refuses with `ENODEV` otherwise.
It also needs `acpi_enforce_resources=lax` on the kernel command line, since ACPI
otherwise holds the Super I/O ports exclusively. `hwmon.py` drops readings above
10,000 RPM: unpopulated headers report sentinel garbage rather than zero.

Temperatures and lighting work without any of this.

## The cooler display

USB `5131:2007`, HID, reverse-engineered from a capture of the vendor app. A
64-byte frame to the interrupt OUT endpoint, about once a second:

    [0]    0x40           constant header, validated by the firmware
    [1]    CPU temp °C    rendered
    [5..6] uint16 BE RPM  rendered, to the nearest 100
    [2]    CPU load %     sent by the vendor app, not displayed
    [9]    GPU temp °C    sent by the vendor app, not displayed

`usb.ids` mislabels the device as an "MSR-101U magnetic card reader"; the ID is
cloned and also used by USB relay boards.

Its hidraw node is opened through a logind `uaccess` ACL, not a group: Omarchy
4 removes users from `input` (raw access to every keyboard), which is how the
display silently went root-only after the first reboot following that
migration. The rule runs the `uaccess` builtin itself because 73-seat-late.rules
acts on the tag before a 99- rule has set it — see `udev/99-mimarchy.rules`.

**There is no off command.** The panel lights because frames arrive and blanks on
a firmware timeout once they stop, which is why the module exposes a run loop
rather than a toggle. That is not an assumption: every byte position was swept at
all 256 values on both of the device's working write channels — the interrupt
endpoint and `SET_REPORT(Output)` on the control pipe — while filming the panel.
32,256 probes, no candidate. `authorized=0`, runtime suspend, unconfigure and a
USB reset all leave it lit, and the root hub reports no power switching, so its
VBUS cannot be cut in software either.

The blank timeout is **50.35 s**, filmed and reproduced across two five-trial
runs at sigma 0.02 and 0.04. Sending frames faster does not shorten it.

## Testing without the hardware

There is no stub backend. On a machine without the hardware,
`mimarchy-setup --list` reports what the drivers find (nothing, without the
devices — the board's side lives in `src/mimarchy/aura.py`, the card's in
`src/mimarchy/nitro.py`), and `mimarchy-lightd --once` logs the frames it
would write. Daemon liveness is a pidfile singleton under `$XDG_RUNTIME_DIR`
(`mimarchy-lightd.pid`, `mimarchy-displayd.pid`); a daemon that exits because
no device answered leaves a one-line user action in `mimarchy-<name>.note`
beside it, which `mimarchy-ctl status` surfaces as `daemon_note`.
