# Hardware notes

Why the code is shaped the way it is, and the handful of things about this
hardware that are not obvious. Everything here was measured on the device.

For what Mimarchy is and how to install it, see the [README](../README.md).

## ARGB

**Zone sizing is mandatory.** Addressable zones report `leds=0` — OpenRGB has no
way to know how long a strip is — and writing a colour to a zero-length zone
silently does nothing. No error, no effect. Every zone is resized to
`rgb.zone_size` on connect. This single detail is the difference between
"OpenRGB doesn't support this board" and working control.

Set it to the strip's *real* length. Too short leaves the tail dark; too long is
worse than it sounds, because spatial effects span the zone rather than the LEDs
— at 60 on a 15-LED strip, rainbow shows a quarter of the hue wheel and is
indistinguishable from spectrum.

**Colour only sticks in a direct-drive mode** (`Direct` on the board, `Static` on
the GPU). In an effect mode the controller keeps running its own animation and
ignores the SDK entirely.

**Two independent controllers.** The motherboard's Aura chip is USB HID
(`0b05:19af`); the GPU has its own reached over I2C *on the card* (`/dev/i2c-7`,
address `0x28`). Different hardware, different buses.

The GPU's ARGB connector is an **output** — a source for syncing other devices to
the card, not an input — so no motherboard header can drive the card's LEDs, and
I2C is the only route. Verified physically: rewiring the GPU onto the motherboard
chain changed nothing, and blacking out every motherboard zone left the GPU lit.

## Keep OpenRGB's detector list narrow

OpenRGB's broad GPU/I2C detection has been reported to hard-freeze systems with
this card ([#4888](https://gitlab.com/CalcProgrammer1/OpenRGB/-/issues/4888),
open). Enabling only the detector matching the exact card is safe here. All 1953
are disabled except four:

    ASUS Aura Addressable, ASUS Aura Core, ASUS Aura Motherboard,
    Sapphire Radeon RX 9070 XT Nitro+

`tools/restrict-openrgb-detectors.py` applies that set and `--check` verifies it.
`install.sh` runs it *before* the server ever starts, which is the order that
matters — the service starts at login, so an unrestricted config means a freeze
on every boot. **Re-run it after opening the OpenRGB GUI**, which rewrites the
config and can re-enable everything.

Those four are this machine's, not the tool's: the allowlist comes from the
devices in `config.toml`, which `mimarchy-setup` fills in from what OpenRGB
detects. Working out which detector produced which device is guesswork, because
OpenRGB never says — the SDK reports device names and the config file lists
detector names, with no shared id, and the only way to ask directly is to run
detection, which is the dangerous act. So the matching in `mimarchy/detectors.py`
is deliberately timid: a device name that matches two detectors without matching
either exactly is refused rather than guessed at, since a missed detector is a
dark zone somebody reports and a spurious one is a locked-up desktop nobody can.
`detectors = [...]` in `config.toml` overrides the lot.

There is one unavoidable chicken-and-egg in this. The list is narrowed before the
server first starts, so a machine whose hardware was never in that narrow set
sees no devices at all — and nothing can be selected that was never detected.
`--discover` re-enables everything for one detection pass, behind a typed
confirmation, which is the same state a stock OpenRGB install is in permanently.

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
