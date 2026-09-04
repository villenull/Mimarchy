#!/usr/bin/env bash
# Install Mimarchy for the current user.
#
# Everything here is user-level: a virtualenv, three `systemctl --user` units,
# the launcher symlinks, and the desktop integration for whichever Omarchy is
# installed. The steps that need root are printed at the end rather than run, so
# this never asks for a password.
#
# The ordering matters in one place. OpenRGB's broad GPU/I2C detection is a
# documented total-system freeze with some cards, and its service is enabled at
# login — so the detector list is narrowed *before* the server is ever started,
# and the server is enabled only once that narrowing has been verified. Doing
# it the other way round means a freeze on the next boot. The one detection
# pass that cannot be narrowed — OpenRGB's very first, which is what creates
# the list — is never run from here; it is printed for the user to run on
# purpose.

set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
UNITS="$HOME/.config/systemd/user"

# The virtualenv lives outside the checkout, and that is load-bearing rather
# than tidy-minded. This repo is also an Omarchy shell plugin, so it may *be*
# `~/.config/omarchy/plugins/io.github.villenull.mimarchy` — and the plugin
# validator refuses any symlink anywhere inside a plugin folder except under
# .git. A venv has four of them (`bin/python`, `lib64`, ...; even `--copies`
# leaves `lib64`), so a venv in the checkout would fail `omarchy plugin
# validate`, and `omarchy plugin update` re-validates and rolls back — which
# would quietly make the plugin un-updatable.
#
# Keeping it out of the tree also means `omarchy plugin update`'s fast-forward
# pull never has to reason about it.
VENV="${MIMARCHY_VENV:-$HOME/.local/share/mimarchy/venv}"
BIN="$VENV/bin"

say() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

# Whether this machine is running Omarchy 4 at all — the only version this
# supports. Detected from where the active theme lives, which is a real signal
# rather than a guess: Omarchy 4 moved it into the XDG state directory, and a
# pre-4 install (or none at all) simply has nothing there.
STATE_HOME="${XDG_STATE_HOME:-$HOME/.local/state}"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"

# Tested with -L as well as -e, because that path is a *symlink* to the active
# theme and -e follows it: a link left dangling mid-theme-switch would otherwise
# report a real Omarchy 4 machine as having no Omarchy at all, and silently skip
# every integration step.
if [[ -e "$STATE_HOME/omarchy/current/theme" || -L "$STATE_HOME/omarchy/current/theme" ]]; then
  OMARCHY=4
else
  OMARCHY=none
fi

say "1/6  Python environment"
if [[ ! -x "$BIN/python" ]]; then
  mkdir -p "$(dirname "$VENV")"
  python3 -m venv "$VENV"
fi
# Everything pip may fetch is pinned by version and artifact hash in
# requirements.lock, so the venv's contents are decided by this checkout's
# commit rather than by whatever PyPI resolves to on install day — the
# property the marketplace's review of an exact commit depends on. Mimarchy
# itself then installs from the checkout with --no-deps (the lock already
# provided the whole closure) and --no-build-isolation (the build runs on the
# locked setuptools instead of fetching a fresh backend). The one unpinned
# input left is the checkout, and the checkout is the thing being installed.
"$BIN/python" -m pip install --quiet --require-hashes -r "$REPO/requirements.lock"
"$BIN/python" -m pip install --quiet --no-deps --no-build-isolation -e "$REPO"
echo "    installed into $VENV"

say "2/6  Narrowing OpenRGB's detector list (before first start)"
if ! command -v openrgb >/dev/null; then
  echo "    openrgb is not installed. Install it, then re-run this script:"
  echo "        sudo pacman -S openrgb"
  exit 1
fi
# OpenRGB's config — and the full detector list the allowlist is written into
# — only exists once OpenRGB has run once, and that first run is a detection
# pass with every detector enabled: the exact #4888 hazard this step exists to
# prevent. This script used to run that pass itself when the config was
# missing, on the theory that once, with the user watching, beats every boot.
# The marketplace review pointed out what the theory skipped: a script that
# runs a known-freeze pass without asking has not asked. So it no longer does.
# Without a config this step fails closed — nothing is probed, nothing is
# enabled below — and the pass is left to the user to run on purpose, with
# their work saved first.
DETECTORS_SAFE=no
if [[ ! -f "$HOME/.config/OpenRGB/OpenRGB.json" ]]; then
  cat <<'EOF'
    OpenRGB has never run on this machine, so it has no config yet and its
    detector list cannot be narrowed until it does. Creating that config
    takes one detection pass with every detector enabled — the documented
    total-system freeze hazard (OpenRGB issue #4888) — and this script will
    not run that for you. When you are ready, with your work saved, run it
    yourself:

        openrgb --list-devices

    then re-run this script. The services are installed below but left
    disabled until the detector list has been narrowed and verified.
EOF
else
  # The tool's own exit code is deliberately not the gate: it declines to
  # guess for hardware it does not know and says so, and an already-narrowed
  # list is safe whether or not it could add to it. The verification is the
  # --check run — exactly the safe set enabled, nothing more — and only that
  # decides whether the server may be enabled.
  "$BIN/python" "$REPO/tools/restrict-openrgb-detectors.py" || true
  if "$BIN/python" "$REPO/tools/restrict-openrgb-detectors.py" --check; then
    DETECTORS_SAFE=yes
  else
    cat <<EOF

    The detector list did not verify as exactly the safe set, so the server
    is left disabled: an enabled openrgb.service with an unverified list is
    the every-boot freeze this step exists to prevent. Fix the list, then
    re-run this script — or verify it and enable the services by hand:

        $REPO/tools/restrict-openrgb-detectors.py --check
        systemctl --user enable --now openrgb.service mimarchy-light.service
EOF
  fi
fi

say "3/6  User services"
mkdir -p "$UNITS"
install -m644 "$REPO/systemd/openrgb.service" "$UNITS/openrgb.service"
for unit in mimarchy-light mimarchy-display; do
  sed "s|@BIN@|$BIN|g" "$REPO/systemd/$unit.service" > "$UNITS/$unit.service"
done
systemctl --user daemon-reload
# The display stream is enabled but never started here: it lights the cooler's
# panel, which not everyone wants on, and the bar panel's `d` key (or a click on
# the row) starts it on demand. It talks to the cooler over HID, never to
# OpenRGB, so it sits outside the gate below.
systemctl --user enable mimarchy-display.service

# Confirms the listener the running server actually bound, not just the flag
# the unit passes — the unit binds it to loopback, and this is the check that
# it took. Detection runs before the port opens, so this waits for it, briefly.
confirm_loopback_listener() {
  local listen
  if ! command -v ss >/dev/null; then
    echo "    (ss is not installed, so the listener was not confirmed; the unit binds 127.0.0.1)"
    return
  fi
  for _ in $(seq 1 30); do
    listen="$(ss -ltnH 'sport = :6742' 2>/dev/null | awk '{print $4}' || true)"
    if [[ -n "$listen" ]]; then
      if [[ "$listen" == 127.0.0.1:6742 ]]; then
        echo "    OpenRGB's SDK server is listening on 127.0.0.1:6742 only"
      else
        echo "    WARNING: OpenRGB's SDK server is listening on $listen — expected 127.0.0.1:6742 only."
        echo "             If openrgb.service was already running before this install, it is still on"
        echo "             its old command line: restart it (or reboot), then re-check with"
        echo "             ss -ltn 'sport = :6742'. Otherwise check ExecStart in $UNITS/openrgb.service."
      fi
      return
    fi
    sleep 0.5
  done
  echo "    (could not confirm the SDK server's listener yet — later: ss -ltn 'sport = :6742')"
}

# The server, and the renderer that pulls it in through Wants=, are enabled
# only when step 2 verified the detector list. Otherwise both are disabled —
# including if an earlier run had enabled them, since an enabled server with
# an unverified list is precisely the freeze-on-every-boot this guards
# against — and step 2 has already printed the way back.
if [[ "$DETECTORS_SAFE" == yes ]]; then
  systemctl --user enable --now openrgb.service
  systemctl --user enable --now mimarchy-light.service
  echo "    openrgb + mimarchy-light started; mimarchy-display enabled but not started"
  confirm_loopback_listener
else
  systemctl --user disable openrgb.service mimarchy-light.service >/dev/null 2>&1 || true
  echo "    units installed; openrgb + mimarchy-light left disabled until the detector list verifies (step 2)"
fi

say "4/6  Point Mimarchy at your hardware"
cat <<EOF
    The shipped config names the developer's board and card. To use your own:

        mimarchy-setup

    then narrow OpenRGB to just what you picked, and restart it:

        $REPO/tools/restrict-openrgb-detectors.py
        systemctl --user restart openrgb.service mimarchy-light.service

    If mimarchy-setup lists no devices, that is the detector list rather than
    your cabling: it was narrowed before your hardware was ever detected, and
    nothing can be selected that was never found. Widen it for one pass:

        $REPO/tools/restrict-openrgb-detectors.py --discover
        systemctl --user restart openrgb.service
EOF

say "5/6  Launcher and desktop integration"
mkdir -p "$HOME/.local/bin"
# mimarchy-ctl is not optional here: the bar widget shells out to it by bare
# name for every poll and every click, so leaving it inside the venv means a
# widget stuck on "backend not installed" no matter how well the rest installed.
for entry in mimarchy-ctl mimarchy-setup; do
  ln -sf "$BIN/$entry" "$HOME/.local/bin/$entry"
done
echo "    symlinked into ~/.local/bin"

if ! command -v mimarchy-ctl >/dev/null 2>&1; then
  echo "    WARNING: ~/.local/bin is not on your PATH — the bar widget will not"
  echo "             find mimarchy-ctl until it is."
fi

# The theme-set hook is installed rather than printed, unlike the other v4
# integration steps. Those merge into files the user already owns and edits, so
# doing it for them risks clobbering their work; this drops one new file into a
# directory whose entire purpose is third-party hooks, under a name that is ours.
# Removing it is `rm`, and the hook no-ops when mimarchy-ctl is gone.
if [[ "$OMARCHY" == 4 ]]; then
  HOOKS="$CONFIG_HOME/omarchy/hooks/theme-set.d"
  mkdir -p "$HOOKS"
  install -m755 "$REPO/omarchy/theme-set.d/mimarchy" "$HOOKS/mimarchy"
  echo "    theme-set hook installed — LEDs set to a theme colour now follow theme switches"
fi
case "$OMARCHY" in
  4) echo "    detected Omarchy 4" ;;
  *) echo "    no Omarchy theme found — desktop integration steps skipped" ;;
esac

say "6/6  Manual steps left"

# Quoted heredoc: the printed commands carry backslashes and quoting that must
# reach the user's terminal verbatim.
cat <<'EOF'

  a) The cooler display's hidraw node is root-only without a udev rule. The
     command below spells the rule out instead of copying it from this
     checkout: root writes exactly the two lines you can read here, not
     whatever a file in a user-writable directory holds by the time sudo
     runs. (udev/99-mimarchy.rules is the same rule, kept for reference;
     tests assert the two never drift.)

       printf '%s\n' \
         '# Mimarchy: cooler display (5131:2007) hidraw access; remove with the plugin.' \
         'SUBSYSTEM=="hidraw", ATTRS{idVendor}=="5131", ATTRS{idProduct}=="2007", TAG+="uaccess", RUN{builtin}+="uaccess", GROUP="input", MODE="0660"' \
         | sudo tee /etc/udev/rules.d/99-mimarchy.rules >/dev/null
       sudo udevadm control --reload-rules
       sudo udevadm trigger --action=add --subsystem-match=hidraw

     Skip this if you do not have the cooler display; the lighting works without it.
EOF

if [[ "$OMARCHY" == 4 ]]; then
  # This repo is also the shell plugin — manifest.json sits at its root. When it
  # was cloned by `omarchy plugin add`, it already lives where the shell looks
  # and there is nothing further to do; otherwise the widget needs adding
  # separately, which clones a second copy. Both work; saying which one applies
  # beats printing instructions the user has already followed.
  if [[ "$REPO" == "$CONFIG_HOME/omarchy/plugins/"* ]]; then
    cat <<EOF

  b) Bar widget — already installed. This checkout *is* the plugin, so the
     backend it calls is now in place too. If the icon is not on the bar yet:

       omarchy plugin enable io.github.villenull.mimarchy --section right
EOF
  else
    cat <<EOF

  b) Bar widget — add the plugin to get the icon and panel:

       omarchy plugin add https://github.com/villenull/mimarchy --enable

     That clones a second copy into ~/.config/omarchy/plugins/. To keep one
     instead, let Omarchy do the cloning and install from where it lands:

       omarchy plugin add https://github.com/villenull/mimarchy --enable
       ~/.config/omarchy/plugins/io.github.villenull.mimarchy/install.sh

     (Safe either way: the virtualenv goes in ~/.local/share/mimarchy, never
     inside the plugin folder, which the validator would reject for symlinks.)
EOF
  fi

  cat <<EOF

  c) Omarchy menu (optional) — merge this entry into
     ~/.config/omarchy/extensions/omarchy-menu.jsonc:

       $REPO/omarchy/mimarchy-menu.jsonc

     Worth having even with the widget: it puts Mimarchy in the menu's search,
     which is how a lot of people open things.
EOF
  NEXT_STEP=d
else
  cat <<EOF

  b) No Omarchy install detected, so there is nothing to add a launcher to.
     mimarchy-ctl (installed above, in ~/.local/bin) is the only interface on
     a non-Omarchy machine — there is no TUI and no bar panel to fall back to.
     It is a real CLI in its own right: status/effect/colour/speed/display/link,
     scriptable and bindable to whatever keys or menu you use instead.
EOF
  NEXT_STEP=c
fi

# Continues the lettering from wherever the branch above stopped, so the list
# reads as one sequence instead of repeating or skipping a letter.
cat <<EOF

  ${NEXT_STEP}) Fan RPM readout (optional) needs the out-of-tree nct6687d driver; see
     the README. Temperatures and lighting work without it.

Done. Open it from the bar: click the Mimarchy icon.
EOF
