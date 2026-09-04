# Handoff — September 2026

> **Superseded in part on 2026-09-04.** The local session found that §4's
> timeline is wrong — the card was already undetected at the Aug 30 boot,
> a day before the restart blamed here — and that its LED controller is
> silent on every I2C bus. See `docs/gpu-incident-2026-09.md` for the
> evidence, what was reinstalled, and what remains. The rest of this file
> (marketplace state, decisions, the uninstall record) still stands.

**Read this whole file before doing anything else.** It is written for a Claude
Code instance running *locally* on Diego Huyke's Omarchy PC (hostname
`fbi-pc`), with a terminal and a webcam pointed at the graphics card. A
previous cloud-hosted instance — which could not touch the machine — did the
work summarised here and was shut down on 2026-09-03. This file is its entire
memory. Where it says "verify", verify: some of what it believed turned out to
be wrong, and it says so.

## 0. The one-paragraph version

Mimarchy is Diego's Omarchy bar plugin for the ARGB lighting on his CPU cooler
fans, his GPU, and the cooler's built-in display. It is being submitted to the
Omarchy Plugin Marketplace (`HANCORE-linux/omarchy-plugin-marketplace`, issue
#2935). Two rounds of maintainer security review have been addressed in code
and are on `main`, but the issue has **not** yet been nudged to re-validate.
Separately: while looking into why the `openrgb` process looked heavy in a
system monitor, a restart of `openrgb.service` re-probed the I2C bus and the
GPU's LED controller stopped being detected. It was still not lit after a warm
reboot. Diego then chose a full uninstall of Mimarchy *and* OpenRGB to start
clean. Your job: reinstall, get the GPU lit again, settle the resource
question, finish the marketplace submission — and report back in plain
English.

## 1. Who you are working with, and the decisions already made

- Diego is the author and sole maintainer. He is comfortable running a
  command but is not a git/shell power user; the previous session lost a lot
  of time using him as a copy-paste relay between a cloud container and his
  terminal. **You have the terminal — use it.** Show him results, not
  commands to run.
- Explain in plain English. Lead with what changed and what he needs to do.
- Decisions Diego made in the handoff interview (2026-09-03):
  - **Uninstall scope: everything, including OpenRGB** (package and
    `~/.config/OpenRGB`). §6 lists exactly what the uninstall removes and
    where the backup is. Consequence: OpenRGB's config, and with it the
    narrowed detector list, is gone; the first OpenRGB run on this machine
    will be the broad detection pass (§4.2).
  - **Marketplace: you may act as Diego on issue #2935** — post replies and
    edit the issue to re-trigger validation — through his authenticated `gh`.
    Guardrails: only report fixes and ask questions; never argue with the
    maintainer; never say something is fixed before it is on `main`; list
    every comment you posted in your final summary.
  - **Hardware risk: full autonomy, just warn.** You may run the broad
    OpenRGB detection pass and targeted I2C probes yourself, after telling
    Diego to save his work. A hard freeze loses your context, so **write your
    findings to `docs/` before each risky step.** A physical power cycle
    still needs him.
- Definition of done, in his words: *"all issues resolved and a short, plain
  English explanation of what it did and what it now needs me to do if I want
  to publish this thing."*

## 2. Repository state

Verify with `git log --oneline -6` and `git tag`.

| Commit | What | Status |
|---|---|---|
| `e1b15fb` | Last commit before the previous session (Aug 23). | Was the submitted commit. |
| `73fb365` | Round-1 marketplace fixes: `requirements.lock` with sha256 pins, `--require-hashes` / `--no-deps --no-build-isolation` installs, udev rule printed inline instead of `sudo cp` from the checkout, `tests/test_install_inputs.py`. Version 0.4.1. | Accepted by the maintainer ("earlier supply-chain findings are resolved"). |
| `2c4faad` | `WriteFailureWatch` in `lightd.py`: exit after 5 s of *total* write failure so `Restart=on-failure` reconnects; `tests/test_lightd_recovery.py`. | On main. |
| `b126f33` | Round-2 marketplace fixes: `install.sh` fails closed (never runs the detection pass; enables `openrgb`/`mimarchy-light` only inside the branch a passing `restrict-openrgb-detectors.py --check` guards, disables them otherwise); `openrgb.service` binds `--server-host 127.0.0.1`; `install.sh` confirms the effective listener with `ss`; README documents both; three more tests. Version 0.4.2. | On main. **Not yet validated by the marketplace** (§3). |
| this file's commit | `docs/HANDOFF-2026-09.md` + `CLAUDE.md`. **No `v0.4.2` tag exists yet:** the cloud session's git proxy refused tag pushes (HTTP 403, branches only). Create it as your first git action: `git tag -a v0.4.2 -m "Mimarchy 0.4.2" origin/main && git push origin v0.4.2`. | Head of main. |

- Branch `claude/edit-resubmit-submission-0fyh4d` is the previous session's
  working branch and is identical to `main`; delete it when convenient.
- Test suite: 274 tests at the time of writing (285 as of 0.4.3). Run them exactly as `CLAUDE.md` describes (a venv
  built from `requirements.lock`, then `pytest`). They must stay green.
- A memory-hardening change (`MALLOC_ARENA_MAX=2`, `MemoryHigh=300M` in
  `openrgb.service`) was written and then **deliberately dropped** before
  reaching main, because the evidence in §5 showed the premise was wrong.
- Nothing on `main` touches `src/mimarchy/rgb.py`, detection, or the units'
  detection behaviour in a way that could explain the GPU incident; the
  running system was also never re-installed from these commits during the
  incident. Re-verify that claim yourself rather than inheriting it:
  `git diff e1b15fb main --stat`.

## 3. The marketplace submission

- **Where:** https://github.com/HANCORE-linux/omarchy-plugin-marketplace/issues/2935
  (a resubmission; #2026 was the original, closed by accident while editing;
  the author cannot reopen an issue someone else closed, and a reopen
  request there went unanswered).
- **Rules:** the marketplace repo's `SUBMISSION.md` and `SECURITY.md`. Title
  must start with `[Plugin]:`. Editing the issue re-runs validation against
  the **current head of `main`**. Validation passing removes `needs-fixes`;
  the automated security baseline then sets `security-review-required`
  (meaning: capabilities need a human sign-off, no blocking findings). A
  maintainer applies `approved-and-verified`; a bot publishes the listing and
  closes the issue as completed. **Approval is bound to the exact validated
  commit** — once the maintainer has started re-reviewing a commit, do not
  move `main` unless they asked for changes.
- **Labels at the last check (2026-09-03 01:07 UTC):** `manual-setup`,
  `needs-fixes`, `security-review-required`, `submission`, `validated`.
  `needs-fixes` is the maintainer's round-2 label and is stale now that the
  fixes are on `main` — it clears when validation re-runs. `manual-setup` is
  a listing attribute ("standard install cannot produce a functioning plugin"
  — true, and by design), not a blocker.
- **Round 1 (Aug 29, maintainer HANCORE-linux):** unpinned Python packaging
  inputs; privileged udev copy sourced from the user-writable checkout. Fixed
  in `73fb365`; maintainer confirmed resolved.
- **Round 2 (Sep 1):** (1) `install.sh` ran the documented freeze-hazard
  detection pass automatically when OpenRGB had no config, ignored the
  restrict tool's failure, and enabled/started the server unconditionally —
  must fail closed; (2) `openrgb.service` bound the unauthenticated SDK
  server to all interfaces while Mimarchy only connects to 127.0.0.1 — bind
  loopback and document/test it. Fixed in `b126f33`.
- **What is left, in order:**
  1. Confirm `git rev-parse origin/main` and that the marketplace's own
     scanner is clean on it (§3.1). It was clean on `b126f33`; this file's
     commit only adds documentation, and `docs/` is excluded from the scan.
  2. Post the reply below on #2935 (as Diego, per §1), with the real SHA
     filled in. Then make a small edit to the issue body (e.g. append
     "Round-2 fixes are on main at <sha>." to *Maintainer notes*) — the edit
     is what re-runs validation. Within a couple of minutes the bot updates
     its two comments and the labels should become
     `manual-setup + submission + validated + security-review-required`.
  3. Watch the issue (`gh issue view 2935 --repo HANCORE-linux/omarchy-plugin-marketplace --comments`).
     Address any new maintainer ask the same way: fix on `main`, verify with
     tests + scanner, reply, edit. If a week passes with no response, post
     one polite ping.
  4. Done when the issue is closed as completed by `github-actions` (or
     carries `approved-and-verified`). Publishing is then the marketplace's
     action, not Diego's — tell him so plainly.

Reply to post (fill in the SHA):

> Both boundaries are addressed on main, now at `<HEAD SHA>` (the fixes are
> in `b126f33`; the commit on top only adds documentation).
>
> 1. **install.sh fails closed at the detector boundary.** It no longer runs
> OpenRGB's first detection pass under any circumstances: with no OpenRGB
> config present it prints the command for the user to run deliberately and
> stops there; with a config present it runs `restrict-openrgb-detectors.py`
> and then gates on `--check` — `openrgb.service` and `mimarchy-light.service`
> are enabled and started only when the check confirms exactly the safe set,
> and are otherwise explicitly disabled (including if an earlier run had
> enabled them), with the remedy printed. The tool's own exit code is
> deliberately not the gate, since it declines to guess for unknown hardware
> by design; the `--check` verification is.
> 2. **SDK server bound to loopback.** `openrgb.service` now passes
> `--server-host 127.0.0.1`; after starting it, install.sh confirms the
> effective listener with `ss` and warns if it is anything other than
> `127.0.0.1:6742`; the README documents the binding and why.
>
> `tests/test_install_inputs.py` adds three checks evaluated on the script
> with its printed heredocs stripped, so quoted commands cannot satisfy them:
> install.sh never executes `openrgb` itself, the services are enabled only
> inside the branch a passing `--check` guards, and the unit's bind address
> equals the client's. The commit also carries a small robustness fix in
> `lightd` (it exits after five seconds of total write failure so systemd
> restarts it into a fresh connection). I've edited the issue to trigger
> revalidation at the new SHA.

### 3.1 Running the marketplace's scanner yourself

The previous session reproduced the bot's reports exactly this way; it is the
fastest way to know what the maintainer's automation will say before pushing.

```bash
git clone --depth 1 https://github.com/HANCORE-linux/omarchy-plugin-marketplace /tmp/mp
cat > /tmp/mp/run-baseline.mjs <<'EOF'
import { runSecurityBaseline } from "./scripts/security-baseline-scanner.mjs";
import { buildSecurityBaselineReport } from "./scripts/security-baseline-report.mjs";
const sha = process.argv[2];
const result = await runSecurityBaseline("https://github.com/villenull/mimarchy", sha, {
  requiredPaths: ["quickshell/Panel.qml"],
  listedPlugins: [{ pluginId: "io.github.villenull.mimarchy", manifestPathHint: "manifest.json" }],
});
process.stdout.write(buildSecurityBaselineReport(result, { context: "submission" }));
EOF
cd /tmp/mp && GITHUB_TOKEN=$(gh auth token) node run-baseline.mjs "$(git -C ~/mimarchy rev-parse origin/main)"
```

Needs Node 22+. The report's first line embeds base64 JSON with `outcome`,
`findings` and `capabilities`. Every scan so far: `outcome: review-required`,
`findings: []`, capabilities `remote-build, service-management, privilege,
installer, package-manager` — all inherent to a hardware plugin with an
installer, all already explained in the issue's Maintainer notes.

## 4. The GPU incident — what happened, what is known, what is not

### 4.1 Timeline (from `journalctl --user -u openrgb -u mimarchy-light`)

| When (local) | Event |
|---|---|
| Aug 23 13:14 | Boot. `openrgb.service` ran continuously until Aug 30: **9 h 26 min CPU over 1 w 6 h wall, 44 M memory peak**. GPU lighting worked. |
| Aug 30 19:41 | Reboot. `openrgb` ran until Aug 31: **3 min 57 s CPU over 1 d 3 h 52 min, 39.9 M memory peak**. GPU lighting worked. |
| Aug 31 23:33 | On the previous instance's advice Diego added a systemd drop-in to `openrgb.service` (`MALLOC_ARENA_MAX=2`, `MemoryHigh=300M`) and ran `systemctl --user restart openrgb.service mimarchy-light.service`. **After this restart the GPU LEDs stopped responding.** |
| Aug 31 23:35 | `mimarchy-light` restarted alone (no change). |
| Sep 1 | Diagnostics (§4.3). Re-ran the detector tool, restarted `openrgb`, waited 10 s, restarted `mimarchy-light`: still only the motherboard detected. |
| Sep 2/3 | Warm reboot. Diego reports the GPU LED is still unresponsive. **Whether the card is detected after that reboot was never confirmed** — the first thing to establish. |
| Sep 3 | Full uninstall (§6) so you start clean. |

### 4.2 The hardware facts (from `docs/hardware-notes.md`, `src/mimarchy/detectors.py`)

- GPU: Sapphire Radeon RX 9070 XT Nitro+. Its LED controller is a separate
  microcontroller **on the card**, reached over I2C at `/dev/i2c-7`, address
  `0x28`. OpenRGB detector name: `Sapphire Radeon RX 9070 XT Nitro+`.
- Motherboard: ASUS PRIME X870-P WIFI, Aura USB controller (`0b05:19af`),
  zones `Aura Addressable 1..3`. Cooler: Balam Rush Heliux Pro HEX75, LEDs
  on the Aura headers, display over HID (`5131:2007`).
- **Freeze hazard:** OpenRGB's broad GPU/I2C detection — every detector
  enabled, which is how OpenRGB ships and what its very first run does — is
  reported to hard-freeze whole systems with this card (OpenRGB issue 4888).
  The repo's notes say it was safe on kernel 7.1.4 and that the freeze
  reports were on 6.15. `tools/restrict-openrgb-detectors.py` narrows the
  list; `--check` verifies; `--discover` re-enables everything once, behind a
  typed confirmation. The previous install of this machine ran the broad
  pass once (the old `install.sh` did it) without freezing.
- The narrowed set that was verified on Sep 1 (5 of 1953): `ASUS Aura
  Addressable`, `ASUS Aura Core`, `ASUS Aura Motherboard`, `Corsair Lighting
  Node Pro`, `Sapphire Radeon RX 9070 XT Nitro+`. The Corsair entry was
  derived from `config.toml`; `mimarchy-ctl status` listed three zones
  (`cpu_fans`, `gpu`, `strip`). Whether a Corsair device actually exists on
  this machine is an **open question** — `mimarchy-setup --list` showed only
  the ASUS motherboard, so either it was never present (stale wizard output)
  or it, too, stopped being detected. The backup in §6 contains that
  `config.toml`.

### 4.3 What has been ruled out, with the evidence

| Ruled out | Evidence |
|---|---|
| The detector list | `restrict-openrgb-detectors.py --check` → "OK — exactly the safe set is enabled", Sapphire detector present and enabled. |
| `i2c_dev` not loaded | `lsmod` showed it loaded with 36 users. The OpenRGB journal warning "One or more I2C/SMBus interfaces failed to initialize" appears on **every** start, including the working ones — it is noise. |
| The memory override | `memory.events` for the unit showed `high 0`: the 300 M ceiling never engaged. (The drop-in is removed by the uninstall.) |
| Changes on `main` | None touch detection or `rgb.py`; the running system was not reinstalled from them. |
| `mimarchy-ctl status` as evidence of detection | It reads the **state file** and asks systemd if the unit is active (`ctl.py:cmd_status`); it never asks OpenRGB what it sees. "gpu: unhinged" proves nothing about the card. Use `mimarchy-setup --list` or `openrgb --list-devices`. |

Two of the previous instance's confident calls were wrong: it blamed
`i2c-dev` first, then predicted a warm reboot would fix it. Treat its
remaining hypotheses as hypotheses.

### 4.4 Hypotheses still open, most likely first

1. **The card's RGB MCU is wedged and a warm reboot does not reset it.** The
   slot stays powered through a reboot; the classic fix for a hung GPU RGB
   controller is a cold boot — shutdown, PSU switched off ~30 s, power on.
   The previous session's own comment in `rgb.py` ("the card had already
   dropped off the I2C bus") records this happening before.
2. **A kernel / amdgpu / firmware update between Aug 30 and the reboot
   changed how the card's I2C adapter is exposed.** Check `uname -r`,
   `grep -E "upgraded (linux|linux-firmware|openrgb|mesa|amd-ucode)" /var/log/pacman.log | tail`,
   `for d in /sys/bus/i2c/devices/i2c-*; do echo "$(basename $d): $(cat $d/name)"; done`
   (expect an `AMDGPU` adapter around `i2c-7`), and `journalctl -k -b | grep -i amdgpu`.
3. **The card is detected after the reboot and this is a mode/render
   problem, not detection.** Cheap to establish first; the fix is software.
4. Device-node permissions on `/dev/i2c-*` (OpenRGB's package ships udev
   rules; check `ls -l /dev/i2c-*` and Diego's groups), or an OpenRGB
   version change. Lower odds.

### 4.5 Suggested procedure (you have the webcam — use it as ground truth)

0. Photograph the card before touching anything (`ffmpeg -f v4l2 -i /dev/video0 -frames:v 1 /tmp/gpu-0.jpg`,
   or `v4l2-ctl`). Lit in a firmware effect, frozen on one colour, or dark
   are three different stories about whether the MCU is alive.
1. Reinstall OpenRGB (`sudo pacman -S --needed openrgb`). Warn Diego, then
   run the broad first pass deliberately: `openrgb --list-devices`. This is
   the single most informative test: with **every** detector enabled, does
   the Sapphire card appear? Record the answer in `docs/` immediately.
2. If it does not appear: work hypothesis 2, then a targeted probe
   `sudo i2cdetect -y -r 7` (bus 7 only; look for `0x28` answering). If the
   adapter exists and `0x28` is silent, that is hypothesis 1 → ask Diego for
   a cold boot, then repeat step 1.
3. If it appears: proceed with the Mimarchy install (§7), narrow the
   detectors, run the wizard, and verify with the webcam that a rendered
   effect reaches the card and that mode transitions (e.g. rainbow →
   chase) take. `docs/hardware-notes.md` and the comments in `rgb.py`
   describe the card's quirks (direct-mode bounce, single-send effect modes).
4. Afterwards, close the two gaps this incident exposed, with tests:
   `lightd` trusts the device list it sees at startup forever and never
   notices a configured zone that is missing (add a startup comparison of
   configured vs detected zones that logs plainly and exits non-zero so
   `Restart=` retries, with a bounded backoff); and `mimarchy-ctl status`
   should show detection state, not just the state file.

## 5. The CPU/RAM question

- What Diego saw: an Omarchy activity panel listing `openrgb` at **0.7 % CPU
  and 8.1 % RAM** after ~2 h 11 m uptime. He asked why and whether it could
  be reduced.
- What the previous instance got wrong: it theorised glibc heap
  fragmentation from the 30 fps SDK stream and shipped (then withdrew) the
  `MALLOC_ARENA_MAX` / `MemoryHigh` change. The journal's cgroup accounting
  says the process peaked at **44 M** over six days and **39.9 M** over the
  next day — small and flat. The 8.1 % is almost certainly virtual address
  space (an amdgpu-using process maps large VRAM apertures), not resident
  memory. Measure with `ps -o rss,vsz -p $(pgrep -x openrgb)` and
  `systemctl --user show openrgb.service -p MemoryCurrent -p MemoryPeak`
  before concluding anything.
- What *is* real and unexplained: CPU time. The Aug 23–30 run consumed
  **9 h 26 min of CPU over 174 h wall (~5.4 % of a core, continuously)**;
  the Aug 30–31 run only **3 min 57 s over 27.9 h (~0.24 %)**. Twenty times
  the per-hour cost in the first run. `mimarchy-light` itself is cheap in
  both (15 min / 1 min 50 s). Nothing changed in Mimarchy between the two.
  Find out what differs (detection retries? the display service? the
  effect in use? a stuck client?) before optimising anything.
- Levers if a reduction is warranted: `mimarchy-lightd --fps 20` (the flag
  exists; `FPS = 30` in `lightd.py`) cuts the SDK stream by a third with
  little visible cost; `write_frame` already uses `fast=True`. Do not
  reintroduce the memory hardening without a measurement that justifies it.

## 6. What the uninstall removed (2026-09-03)

Diego ran the command below from a terminal. It first writes a backup to
`~/mimarchy-uninstall-backup-<timestamp>.tar.gz` (contents of
`~/.config/mimarchy` — including `config.toml` with the zone selection and
`lighting.json` — `~/.config/OpenRGB` including the narrowed detector list
and its `.bak-*` files, and the three user units plus the drop-in) and copies
the udev rule next to it. Then it removes: the three user units and the
override drop-in; the bar plugin (`omarchy plugin remove
io.github.villenull.mimarchy` and its checkout); the venv
(`~/.local/share/mimarchy`); the launchers in `~/.local/bin`; the theme-set
hook; `~/.config/mimarchy`; the runtime state file;
`/etc/udev/rules.d/99-mimarchy.rules`; the `openrgb` package (`pacman -Rns`)
and `~/.config/OpenRGB`.

**What actually happened on 2026-09-04:** the run paused for several minutes
at the plugin step (`omarchy plugin remove` with its stderr hidden), then
completed on its own: `pacman -Rns` removed **openrgb 1.0rc3-3** and its three
orphaned dependencies (`qt5-base`, `qt5-translations`, `mbedtls3` — only
OpenRGB needed them; `pacman -S openrgb` brings them back). Backup:
`~/mimarchy-uninstall-backup-20260904-111320.tar.gz`. A second run of the
corrected command below confirmed nothing was left ("plugin ... is not
installed", "no package named openrgb"). Note the OpenRGB version: if
`/var/log/pacman.log` shows an openrgb upgrade between Aug 30 and Aug 31,
that is hypothesis 2 in §4.4. **Verify on arrival:** `omarchy plugin list` should not show
the plugin, `systemctl --user list-unit-files | grep -E 'openrgb|mimarchy'`
should be empty, `pacman -Qs openrgb` should be empty, and the backup tarball
should exist. Redo any step that did not take.

Left alone on purpose: any Mimarchy entry Diego merged into
`~/.config/omarchy/extensions/omarchy-menu.jsonc`, and the optional
`nct6687d` fan-RPM kernel module if he installed it (`pacman -Qs nct6687`).

```bash
bash -c '
set -u
ts=$(date +%Y%m%d-%H%M%S); backup=$HOME/mimarchy-uninstall-backup-$ts.tar.gz
echo "==> backing up configs to $backup"
tar czf "$backup" --ignore-failed-read -C "$HOME" .config/mimarchy .config/OpenRGB .config/systemd/user/openrgb.service .config/systemd/user/openrgb.service.d .config/systemd/user/mimarchy-light.service .config/systemd/user/mimarchy-display.service 2>/dev/null
[ -f /etc/udev/rules.d/99-mimarchy.rules ] && cp /etc/udev/rules.d/99-mimarchy.rules "$HOME/mimarchy-uninstall-backup-$ts-99-mimarchy.rules"
echo "==> stopping and disabling the user services"
systemctl --user disable --now mimarchy-light.service mimarchy-display.service openrgb.service 2>/dev/null
rm -rf "$HOME/.config/systemd/user/openrgb.service.d"
rm -f "$HOME"/.config/systemd/user/{openrgb,mimarchy-light,mimarchy-display}.service
systemctl --user daemon-reload; systemctl --user reset-failed 2>/dev/null
echo "==> removing the bar plugin"
command -v omarchy >/dev/null && timeout --foreground 60 omarchy plugin remove io.github.villenull.mimarchy || echo "    (plugin command did not finish — removing its folder directly)"
rm -rf "$HOME/.config/omarchy/plugins/io.github.villenull.mimarchy"
echo "==> removing venv, launchers, theme hook, config and state"
rm -rf "$HOME/.local/share/mimarchy" "$HOME/.config/mimarchy"
rm -f "$HOME/.local/bin/mimarchy-ctl" "$HOME/.local/bin/mimarchy-setup" "$HOME/.config/omarchy/hooks/theme-set.d/mimarchy"
rm -f "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/mimarchy-lighting.json"
echo "==> root steps: udev rule and the openrgb package (sudo will ask for your password)"
sudo rm -f /etc/udev/rules.d/99-mimarchy.rules && sudo udevadm control --reload-rules
if pacman -Qq openrgb >/dev/null 2>&1; then sudo pacman -Rns --noconfirm openrgb; else echo "    (no package named openrgb — check: pacman -Qs openrgb)"; fi
rm -rf "$HOME/.config/OpenRGB"
echo "==> done. Backup: $backup"
'
```

## 7. Reinstalling on the clean machine

`install.sh` now fails closed (§2), so on a machine with no OpenRGB config it
will *tell you* to run the first pass and stop. The order that works:

```bash
git clone https://github.com/villenull/mimarchy ~/mimarchy && cd ~/mimarchy   # your dev clone; run everything from here
sudo pacman -S --needed openrgb
openrgb --list-devices            # THE broad first pass (§4.2). Warn Diego first. Creates ~/.config/OpenRGB/OpenRGB.json.
./install.sh                      # narrows to the reference set (no config.toml yet → REFERENCE_KEEP, which includes the Sapphire detector), verifies, installs, enables
mimarchy-setup                    # pick zones — the GPU must be detected for it to be selectable
tools/restrict-openrgb-detectors.py && systemctl --user restart openrgb.service mimarchy-light.service
omarchy plugin add https://github.com/villenull/mimarchy --enable   # the bar widget (a second clone; the backend it calls is the venv install.sh built)
```

Then the printed root step for the cooler display's hidraw node (the `sudo
tee` udev command `install.sh` prints), and optionally the `omarchy-menu.jsonc`
entry. The venv is an *editable* install pointing at whichever checkout ran
`install.sh` — run it from `~/mimarchy` so your code changes are what the
daemons execute after a `systemctl --user restart mimarchy-light.service`.
Restarting `mimarchy-light` alone is always safe; restarting `openrgb`
re-probes the I2C bus and is the action that lost the card in §4 — prefer a
reboot for that until §4 is understood.

## 8. Plan for you

Three tracks. A is hardware-serial; B is independent and can run in a
subagent from the start; C needs a running install, so it follows A.

- **A — GPU (blocking, physical):** §4.5. Success = the webcam shows the
  card following a Mimarchy effect and `mimarchy-setup --list` shows it.
  Then the code hardening in §4.5 step 4, with tests.
- **B — Marketplace (independent):** §3. Success = `needs-fixes` cleared by
  revalidation, every maintainer ask answered on `main`, and either
  `approved-and-verified` / issue closed as completed, or an honest "waiting
  on the maintainer since <date>" in your report.
- **C — CPU/RAM (after A):** §5. Success = a measured statement (RSS, CPU
  share over ≥1 h) and either a justified change on `main` or a justified
  "nothing to fix".

Rules for all three: tests green before every push; run the marketplace
scanner before anything reaches `main` while the review is open; keep
`manifest.json` and `pyproject.toml` versions in step (tested); write comments
that say *why*, in the voice the existing files use.

**Your final report to Diego** — short, plain English, no SHAs unless he
needs to click one: what you did on each track, what you posted on his
behalf, whether the GPU is lit, and the one thing (if anything) he must do to
publish. If the honest answer is "wait for the maintainer", say that.

## 9. Session hygiene

- The previous session's scheduled check-ins on #2935 were deleted; nothing
  will wake it.
- Its cloud clone and scratch files are gone with it. Everything it produced
  that matters is on `main`.
- It could not push tags (proxy policy), so `v0.4.2` is a task for you, not a
  fact — see the table in §2. `manifest.json` and `pyproject.toml` already say
  0.4.2.
