#!/usr/bin/env bash
# Install Mimarchy for the current user.
#
# Everything here is user-level: a virtualenv, three `systemctl --user` units,
# and the Waybar/Hyprland snippets. The two steps that need root are printed at
# the end rather than run, so this never asks for a password.
#
# The ordering matters in one place. OpenRGB's broad GPU/I2C detection is a
# documented total-system freeze with some cards, and its service is enabled at
# login — so the detector list is narrowed *before* the server is ever started.
# Doing it the other way round means a freeze on the next boot.

set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
BIN="$REPO/.venv/bin"
UNITS="$HOME/.config/systemd/user"

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

say "1/5  Python environment"
if [[ ! -x "$BIN/python" ]]; then
  python3 -m venv "$REPO/.venv"
fi
"$BIN/python" -m pip install --quiet --upgrade pip
"$BIN/python" -m pip install --quiet -e "$REPO"
echo "    installed into $REPO/.venv"

say "2/5  Narrowing OpenRGB's detector list (before first start)"
if ! command -v openrgb >/dev/null; then
  echo "    openrgb is not installed. Install it, then re-run this script:"
  echo "        sudo pacman -S openrgb"
  exit 1
fi
# The config only exists once OpenRGB has run at least once. Listing devices is
# the cheapest way to create it, and is safe because it is a one-shot process
# rather than the always-on server.
if [[ ! -f "$HOME/.config/OpenRGB/OpenRGB.json" ]]; then
  echo "    creating OpenRGB's config"
  openrgb --list-devices >/dev/null 2>&1 || true
fi
"$BIN/python" "$REPO/tools/restrict-openrgb-detectors.py"
"$BIN/python" "$REPO/tools/restrict-openrgb-detectors.py" --check

say "3/5  User services"
mkdir -p "$UNITS"
install -m644 "$REPO/systemd/openrgb.service" "$UNITS/openrgb.service"
for unit in mimarchy-light mimarchy-display; do
  sed "s|@BIN@|$BIN|g" "$REPO/systemd/$unit.service" > "$UNITS/$unit.service"
done
systemctl --user daemon-reload
systemctl --user enable --now openrgb.service
systemctl --user enable --now mimarchy-light.service
# The display stream is left disabled: it lights the cooler's panel, which not
# everyone wants on, and the TUI's `d` key starts it on demand.
systemctl --user enable mimarchy-display.service
echo "    openrgb + mimarchy-light started; mimarchy-display enabled but not started"

say "4/5  Launcher and desktop integration"
mkdir -p "$HOME/.local/bin"
ln -sf "$REPO/bin/omarchy-launch-mimarchy" "$HOME/.local/bin/omarchy-launch-mimarchy"
ln -sf "$BIN/mimarchy-tui" "$HOME/.local/bin/mimarchy-tui"
echo "    symlinked into ~/.local/bin"
case "$OMARCHY" in
  4) echo "    detected Omarchy 4" ;;
  3) echo "    detected Omarchy 3.x" ;;
  *) echo "    no Omarchy theme found — desktop integration steps skipped" ;;
esac

say "5/5  Manual steps left"

cat <<EOF

  a) The cooler display's hidraw node is root-only without a udev rule:

       sudo cp "$REPO/udev/99-mimarchy.rules" /etc/udev/rules.d/
       sudo udevadm control --reload-rules
       sudo udevadm trigger --action=add --subsystem-match=hidraw

     Skip this if you do not have the cooler display; the lighting works without it.
EOF

if [[ "$OMARCHY" == 4 ]]; then
  cat <<EOF

  b) Omarchy menu — merge this entry into
     ~/.config/omarchy/extensions/omarchy-menu.jsonc:

       $REPO/omarchy/mimarchy-menu.jsonc

     Omarchy 4 draws bar widgets only from shell plugins, so there is no
     standalone bar icon yet — the menu is the supported way in until the
     Mimarchy shell plugin ships.

  c) Hyprland — to float the TUI, append this line to ~/.config/hypr/hyprland.lua:

       o.window("org.omarchy.mimarchy-tui", { tag = "+floating-window" })

     (also in $REPO/omarchy/mimarchy.lua, with the reasoning)

     Then reload: \`hyprctl reload\`
EOF
elif [[ "$OMARCHY" == 3 ]]; then
  cat <<EOF

  b) Waybar — merge these into ~/.config/waybar/, then \`omarchy restart waybar\`:

       $REPO/legacy/waybar/mimarchy-module.jsonc  -> config.jsonc, and add
                                                     "custom/mimarchy" to modules-right
       $REPO/legacy/waybar/mimarchy-style.css     -> style.css

  c) Hyprland — to float the TUI like bluetui, add to ~/.config/hypr/hyprland.conf:

       windowrule = tag +floating-window, match:class org.omarchy.mimarchy-tui
EOF
else
  cat <<EOF

  b) No Omarchy install detected, so there is nothing to add a launcher to.
     The TUI runs anywhere and falls back to your terminal's own colours.
EOF
fi

cat <<EOF

  d) Fan RPM readout (optional) needs the out-of-tree nct6687d driver; see the
     README. Temperatures and lighting work without it.

Done. Launch with: mimarchy-tui
EOF
