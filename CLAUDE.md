# Mimarchy — notes for Claude Code sessions

- **Start with `docs/HANDOFF-2026-09.md`.** It records the marketplace
  submission (HANCORE-linux/omarchy-plugin-marketplace#2935), an unresolved
  GPU-detection incident, a CPU/RAM question, and the owner's standing
  decisions about how much you may do on your own. Trust it as a starting
  point, verify it as a source.
- **Tests** (293 as of version 0.4.4), from a venv built the way `install.sh` builds one:
  ```bash
  python3 -m venv /tmp/mv && /tmp/mv/bin/pip install --require-hashes -r requirements.lock \
    && /tmp/mv/bin/pip install --no-deps --no-build-isolation -e . \
    && /tmp/mv/bin/pip install "pytest>=9.1.1,<10" "pytest-asyncio>=1.4.0,<2" \
    && /tmp/mv/bin/python -m pytest -q
  ```
- **Hazards.** OpenRGB's broad detection pass (every detector enabled — its
  first run, `--discover`, or a rewritten config after opening the OpenRGB
  GUI) can hard-freeze this hardware (OpenRGB issue 4888); `install.sh`
  must never run it and the tests enforce that. The marketplace review is
  bound to exact commits on `main`; do not move `main` mid-review without a
  reason. `openrgb.service` binds the SDK server to `127.0.0.1` on purpose.
- **Conventions.** Comments explain *why*, in prose; every agreement that
  spans files gets a test (`tests/test_install_inputs.py`,
  `tests/test_manifest.py`); `manifest.json` and `pyproject.toml` versions
  move together; the venv lives outside the checkout because a plugin
  folder may contain no symlinks.
