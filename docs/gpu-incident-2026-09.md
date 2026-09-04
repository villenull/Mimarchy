# GPU detection incident — findings log (local session, 2026-09-04)

Written before each hardware step so a freeze does not lose them. Newest at
the bottom. Read `HANDOFF-2026-09.md` §4 first; this corrects it.

## What the OpenRGB logs in the uninstall backup show

`~/mimarchy-uninstall-backup-20260904-111320.tar.gz` holds every OpenRGB log
since the first install. The line that matters is
`[Sapphire Radeon RX 9070 XT Nitro+] Registering RGB controller`:

| OpenRGB start | Boot | Kernel | Sapphire registered? |
|---|---|---|---|
| Aug 22 20:26, 20:27, 20:39 (first passes + service) | Aug 22 | 7.1.8 | **yes** |
| Aug 23 13:14 (service, ran a week) | Aug 23 | 7.1.8 | **yes** |
| Aug 30 19:41 (service) | Aug 30 | **7.1.9** | **no** |
| Aug 31 23:33 (restart with the memory drop-in) | Aug 30 | 7.1.9 | no |
| Sep 1 07:39 (diagnostics restart) | Aug 30 | 7.1.9 | no |
| Sep 3 00:46 (service, after warm reboot) | Sep 3 | 7.1.9 | no |

So the card was **already gone at the Aug 30 boot**, a day before the
`openrgb.service` restart the handoff blamed. Whatever the LEDs were doing
between Aug 30 and Aug 31 was the card's own firmware, not Mimarchy — the
`mimarchy-light` journal for that boot only shows the usual "can't reach the
server yet" retry at startup, and `lighting.json` has every zone at `off`.

The one thing that changed between the last working boot and the first
broken one, from `/var/log/pacman.log`:

    2026-08-30 15:00  mesa   1:26.1.8-1 -> 1:26.2.1-1
    2026-08-30 15:00  linux  7.1.8.arch1-3 -> 7.1.9.arch1-2

OpenRGB itself was 1.0rc3-3 throughout (installed Aug 22, removed Sep 4).
`linux-firmware` last moved on Aug 22, *before* the working runs.

The I2C adapter the card's LED controller hangs off is still there on 7.1.9:
`/dev/i2c-7` = `AMDGPU DM i2c OEM bus`, PCI `1002:7550`, subsystem
`1DA2:E489`, and every log — broken runs included — registers it. So the
adapter is exposed; what is not established is whether address `0x28` on it
answers. That is the next probe.

The `Corsair Lighting Node Pro` in the old `config.toml` (`strip` zone) never
registered in any log. It was never on this machine.

## Hypotheses, re-ranked on that evidence

1. **Kernel 7.1.8 → 7.1.9 (amdgpu) changed the card's OEM I2C bus** — now the
   leading hypothesis, since detection failed on the very first boot after
   the upgrade and has never succeeded on 7.1.9. Distinguisher: does `0x28`
   answer a single targeted read on bus 7? A cold boot that does *not* bring
   the card back would also point here; the previous kernel is the test.
2. **The card's LED MCU is wedged** and needs a cold boot (PSU off ~30 s). Still
   possible — the reboot on Aug 30 was warm — but the timing coincidence with
   the kernel upgrade is hard to explain away.

## Session facts

- Uninstall verified complete on arrival; the only leftover was a broken
  `~/.local/bin/mimarchy-tui` symlink from an older version (removed).
- `/dev/i2c-*` are `root:i2c 0660` with an ACL granting `huyke` rw
  (udev `uaccess`), so targeted probes need no root.
- The webcam at `/dev/video0` (UGREEN 2K) was pointed at Diego's chair, not
  the card, when this session started. Visual confirmation needs it moved.

## Probes (2026-09-04, this session)

OpenRGB's detector for this card (`SapphireGPUControllerDetect.cpp`) is one
`i2c_smbus_read_byte(0x28)` on every bus whose PCI ids match
`1002:7550` / `1DA2:E489`. Repeating exactly that by hand, with the card
otherwise idle and no OpenRGB installed:

| Probe | Result |
|---|---|
| `i2cdetect -y -r 7 0x28 0x28` (OEM bus) | `--` (no answer) |
| `i2cget -y 7 0x28` | `Error: Read failed` |
| `0x28` on buses 3, 4, 5, 6 (DM i2c hw 0–3) | `--` on all four |
| `i2cdetect -y -r 7` (whole OEM bus, 0x08–0x77) | nothing at any address |

No kernel messages from any of it. The kernel 7.1.9 changelog contains no
amdgpu I2C/OEM change (only a VCN aperture-mapping revert), and the amdgpu
init messages — DMUB, SMU firmware, VBIOS, Display Core — are byte-identical
across the working boot (Aug 23), the first broken boot (Aug 30) and now.

**Conclusion:** the card's LED microcontroller has dropped off its I2C bus
altogether. That is the wedged-MCU story (§4.4 hypothesis 1), and the
kernel-upgrade coincidence looks like a coincidence: the Aug 30 reboot was the
first *reboot* since the working week, and the MCU did not survive it, but
nothing the kernel does differently can be found. A warm reboot has already
been shown not to reset it. **Next step: a cold boot — shut down, PSU switch
off for ~30 s, power on.** The slot's standby rail keeps the MCU powered
through anything softer. If the card is still absent after that, the
remaining test is the previous kernel (7.1.8, not in the pacman cache; it
would come from the Arch Linux Archive).

## Reinstall (2026-09-04, this session) — done

- `openrgb 1.0rc3-3` reinstalled (`pacman -S openrgb`).
- `~/.config/OpenRGB/OpenRGB.json` restored from the backup rather than
  regenerated, so the broad first-run detection pass (the #4888 freeze hazard)
  was never run: the backup already held the full 1953-entry list, narrowed.
  `install.sh` then narrowed it to what `config.toml` needs — the three ASUS
  Aura detectors and the one Sapphire detector — and `--check` confirmed
  exactly that set before it enabled anything.
- `~/.config/mimarchy/config.toml` restored from the backup **minus the
  `strip` zone**: the `Corsair Lighting Node Pro` it named never registered in
  any OpenRGB log, so it was a wizard leftover, not hardware. `lighting.json`
  likewise lost its `strip` target.
- `install.sh` run from `~/mimarchy` (the venv is an editable install of that
  checkout); `openrgb.service` and `mimarchy-light.service` enabled and
  running; SDK listener confirmed on `127.0.0.1:6742` only.
- The udev rule for the cooler display installed by the printed command;
  `/dev/hidraw3` (`5131:2007`) is now `root:input 0660`.
- Bar plugin added and enabled (`omarchy plugin add ... --enable`).

What the fresh install sees, on this boot (card still absent, as expected
before a cold boot):

    mimarchy-setup --list   ->  1 device: ASUS PRIME X870-P WIFI
    mimarchy-ctl status     ->  gpu: NOT DETECTED — no OpenRGB device matching
                                'Sapphire Radeon RX 9070 XT Nitro+'
    journal (mimarchy-light) -> zone 'gpu' not detected: ... a cold boot
                                (power off at the PSU) is the known fix.
                                rendering the zones that were found: cpu_fans

Also observed on this start, and worth knowing: the daemon's *first* connect
got "OpenRGB is running but detected no devices" and exited, and the
5-second `Restart=` retry then found the board. OpenRGB's SDK listener opens
before its detection pass finishes; that is the race the new grace window in
`lightd` exists for when only *some* zones are late.

## After the cold boot — what to check

Nothing needs re-running: the services start at login with the narrowed
list. In order:

1. `mimarchy-ctl status` — the `gpu: NOT DETECTED` line should be gone.
2. `mimarchy-setup --list` — two devices, the Sapphire card among them.
3. `i2cdetect -y -r 7 0x28 0x28` — `28` in the grid.
4. Set an effect from the bar panel and watch the card follow it.

If the card is still absent after a genuine PSU-off cold boot, the kernel
becomes the suspect again despite the clean changelog: boot `linux 7.1.8`
from the Arch Linux Archive and repeat step 3.

## Outcome (2026-09-04, afternoon)

Diego cold-booted (PSU off). On the next boot `0x28` answers on bus 7,
OpenRGB registers `Sapphire Radeon RX 9070 XT Nitro+` again, `mimarchy-setup
--list` shows both devices, and `mimarchy-ctl status` carries no
`NOT DETECTED` line. Hypothesis 1 — a wedged LED microcontroller that only a
power cut resets — is confirmed; the kernel was never involved.

For next time: the symptom is "card dark or frozen on one colour, `i2cdetect
-y -r 7 0x28 0x28` shows `--`", and the fix is the PSU switch, not a reboot
and not a reinstall.
