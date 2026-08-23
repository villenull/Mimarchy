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
# login — so the detector list is narrowed *before* the server is ever started.
# Doing it the other way round means a freeze on the next boot.

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

# Which Omarchy this machine is running, because the desktop integration is
# entirely different on either side of 4.0 and the wrong instructions are worse
# than none. Detected from where the active theme lives rather than from a
# version string: 4.0 moved it into the XDG state directory and left no
# compatibility symlink, so the path *is* the version test.
STATE_HOME="${XDG_STATE_HOME:-$HOME/.local/state}"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"

#
# Tested with -L as well as -e, because that path is a *symlink* to the active
# theme and -e follows it: a link left dangling mid-theme-switch would otherwise
# report a real Omarchy 4 machine as having no Omarchy at all, and silently skip
# every integration step.
if [[ -e "$STATE_HOME/omarchy/current/theme" || -L "$STATE_HOME/omarchy/current/theme" ]]; then
  OMARCHY=4
elif [[ -e "$CONFIG_HOME/omarchy/current/theme" || -L "$CONFIG_HOME/omarchy/current/theme" ]]; then
  OMARCHY=3
else
  OMARCHY=none
fi

say "1/6  Python environment"
if [[ ! -x "$BIN/python" ]]; then
  mkdir -p "$(dirname "$VENV")"
  python3 -m venv "$VENV"
fi
"$BIN/python" -m pip install --quiet --upgrade pip
"$BIN/python" -m pip install --quiet -e "$REPO"
echo "    installed into $VENV"

say "2/6  Narrowing OpenRGB's detector list (before first start)"
if ! command -v openrgb >/dev/null; then
  echo "    openrgb is not installed. Install it, then re-run this script:"
  echo "        sudo pacman -S openrgb"
  exit 1
fi
# The config only exists once OpenRGB has run at least once, and listing devices
# is the cheapest way to create it.
#
# Be honest about what this costs: it is a full detection pass with every
# detector enabled — the exact #4888 hazard the rest of this script exists to
# avoid — because the allowlist cannot be written into a file that is not there
# yet. A one-shot process is not what makes it safer; the difference is that it
# happens once, with the user present and watching, instead of on every boot.
# Machines where the freeze reproduces will hang here, and the honest answer is
# that this is the one pass that cannot be skipped.
#
# Skipped entirely when the config already exists, which is the common case on
# any machine that has run OpenRGB before.
if [[ ! -f "$HOME/.config/OpenRGB/OpenRGB.json" ]]; then
  echo "    creating OpenRGB's config"
  openrgb --list-devices >/dev/null 2>&1 || true
fi
# Not fatal when it declines to guess. The tool leaves the detector list exactly
# as it found it and explains why, and step 4 is where that gets answered — under
# `set -e` an exit 1 here would abort the install over a config question, leaving
# the services uninstalled.
if "$BIN/python" "$REPO/tools/restrict-openrgb-detectors.py"; then
  "$BIN/python" "$REPO/tools/restrict-openrgb-detectors.py" --check || true
else
  # The tool has already said which of its reasons applies — no OpenRGB config
  # yet, or a config whose devices it will not guess detectors for. Repeating a
  # guess here would contradict it.
  echo "    (continuing — the detector list is unchanged; see step 4)"
fi

say "3/6  User services"
mkdir -p "$UNITS"
install -m644 "$REPO/systemd/openrgb.service" "$UNITS/openrgb.service"
for unit in mimarchy-light mimarchy-display; do
  sed "s|@BIN@|$BIN|g" "$REPO/systemd/$unit.service" > "$UNITS/$unit.service"
done
systemctl --user daemon-reload
systemctl --user enable --now openrgb.service
systemctl --user enable --now mimarchy-light.service
# The display stream is left disabled: it lights the cooler's panel, which not
# everyone wants on, and the bar panel's `d` key (or a click on the row) starts
# it on demand.
systemctl --user enable mimarchy-display.service
echo "    openrgb + mimarchy-light started; mimarchy-display enabled but not started"

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
  3) echo "    detected Omarchy 3.x" ;;
  *) echo "    no Omarchy theme found — desktop integration steps skipped" ;;
esac

say "6/6  Manual steps left"

cat <<EOF

  a) The cooler display's hidraw node is root-only without a udev rule:

       sudo cp "$REPO/udev/99-mimarchy.rules" /etc/udev/rules.d/
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
elif [[ "$OMARCHY" == 3 ]]; then
  cat <<EOF

  b) Waybar — merge these into ~/.config/waybar/, then \`omarchy restart waybar\`:

       $REPO/legacy/waybar/mimarchy-module.jsonc  -> config.jsonc, and add
                                                     "custom/mimarchy" to modules-right
       $REPO/legacy/waybar/mimarchy-style.css     -> style.css
EOF
  NEXT_STEP=c
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
