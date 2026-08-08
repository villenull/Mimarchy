#!/usr/bin/env python3
"""Restrict OpenRGB to the detectors this machine can safely run.

OpenRGB ships ~1953 detectors and enables all of them. Its broad GPU/I2C
detection is a documented total-system-freeze with the Sapphire RX 9070 XT
(https://gitlab.com/CalcProgrammer1/OpenRGB/-/issues/4888, open). Since
openrgb.service is enabled at login, an unrestricted config makes that a freeze
on every boot — so this must be applied before the service is first started,
not after.

Keeps exactly four: three USB-only ASUS Aura detectors for the motherboard
headers, and the single detector matching this exact graphics card. Enabling
only the one matching card, rather than all 101 GPU detectors, is what makes
GPU lighting work here without tripping the bug.

Running the OpenRGB *GUI* can rewrite this file and re-enable everything, so
re-run this if lighting ever starts behaving oddly or the machine locks up
during detection.

Usage:  restrict-openrgb-detectors.py [--config PATH] [--check]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

DEFAULT_CONFIG = Path.home() / ".config/OpenRGB/OpenRGB.json"

KEEP = {
    # Motherboard ARGB headers, over USB HID — safe, no I2C involved.
    "ASUS Aura Addressable",
    "ASUS Aura Core",
    "ASUS Aura Motherboard",
    # This exact card, over I2C. Safe on kernel 7.1.4; the freeze reports were
    # on 6.15, before the AMD I2C patches landed.
    "Sapphire Radeon RX 9070 XT Nitro+",
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--check", action="store_true",
                    help="report state without modifying anything")
    args = ap.parse_args()

    if not args.config.exists():
        sys.exit(f"no OpenRGB config at {args.config} — start OpenRGB once first")

    data = json.loads(args.config.read_text())
    detectors = data.get("Detectors", {}).get("detectors")
    if detectors is None:
        sys.exit("config has no Detectors section — unexpected OpenRGB version?")

    enabled = {k for k, v in detectors.items() if v}

    if args.check:
        unexpected = enabled - KEEP
        missing = KEEP - enabled
        print(f"{len(enabled)} of {len(detectors)} detectors enabled")
        if unexpected:
            print(f"UNSAFE: {len(unexpected)} unexpected detector(s) enabled, "
                  f"e.g. {sorted(unexpected)[:5]}")
        if missing:
            print(f"missing expected: {sorted(missing)}")
        if not unexpected and not missing:
            print("OK — exactly the safe set is enabled")
        sys.exit(1 if (unexpected or missing) else 0)

    backup = args.config.with_suffix(f".json.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(args.config, backup)

    for name in detectors:
        detectors[name] = name in KEEP
    args.config.write_text(json.dumps(data, indent=4))

    print(f"backup: {backup}")
    print(f"enabled {len(KEEP)} of {len(detectors)}: {sorted(KEEP)}")
    print("restart the server:  systemctl --user restart openrgb.service")


if __name__ == "__main__":
    main()
