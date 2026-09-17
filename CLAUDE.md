# Mimarchy — notes for Claude Code sessions

- **Start with `docs/HANDOFF-2026-09.md`.** It records the marketplace
  submission (HANCORE-linux/omarchy-plugin-marketplace#2935), an unresolved
  GPU-detection incident, a CPU/RAM question, and the owner's standing
  decisions about how much you may do on your own. Trust it as a starting
  point, verify it as a source.
- **Tests** (count as of version 0.6.0), from a scratch venv outside the
  checkout (a plugin folder may contain no symlinks, so a venv inside it
  would fail `omarchy plugin validate`):
  ```bash
  /usr/bin/python3 -m venv /tmp/mv && /tmp/mv/bin/pip install -e ".[dev]" \
    && /tmp/mv/bin/python -m pytest -q
  ```
- **Hazards.** Both controllers are driven directly (hidraw for the board,
  I2C for the card), so there is no detector list, no SDK server, and no
  broad-detection freeze hazard. The remaining privileged surface is one
  udev rule for the two hidraw nodes (printed inline by the README, never
  copied out of the checkout by root) plus the out-of-tree `nct6687d` fan
  driver. Daemons are panel-supervised with pidfile singletons, not
  systemd units. The marketplace review is bound to exact commits on
  `main`; do not move `main` mid-review without a reason.
- **Install is plugin-add only.** `omarchy plugin add
  https://github.com/villenull/mimarchy --enable` is the whole install;
  there is no installer script, no venv, no systemd unit. `bin/` holds real
  stdlib-only launchers (`mimarchy-ctl`, `mimarchy-lightd`,
  `mimarchy-displayd`, `mimarchy-setup`) that the panel spawns via
  `/usr/bin/python3`.
- **Conventions.** Comments explain *why*, in prose; every agreement that
  spans files gets a test (`tests/test_install_inputs.py`,
  `tests/test_manifest.py`); `manifest.json` and `pyproject.toml` versions
  move together.
