#!/usr/bin/env python3
"""Restrict OpenRGB to the detectors this machine can safely run.

OpenRGB ships ~1953 detectors and enables all of them. Its broad GPU/I2C
detection is a documented total-system freeze
(https://gitlab.com/CalcProgrammer1/OpenRGB/-/issues/4888, open) — reproduced
here with a Sapphire RX 9070 XT. Since openrgb.service is enabled at login, an
unrestricted config makes that a freeze on every boot, so this must be applied
before the service is first started, not after.

The set to keep is whatever the devices in `~/.config/mimarchy/config.toml`
need, so it follows the hardware the user actually selected rather than the
hardware this was written on. `mimarchy.detectors` explains how a device name
is turned into detector names and why that has to be done conservatively; the
short version is that OpenRGB never says which detector produced which device,
so this errs towards leaving a zone dark rather than towards probing an I2C bus
nobody asked about.

With no config to read it falls back to the four detectors this was developed
against, which is exactly what it did before it could read one.

Running the OpenRGB *GUI* can rewrite this file and re-enable everything, so
re-run this if lighting ever starts behaving oddly or the machine locks up
during detection.

Usage:  restrict-openrgb-detectors.py [--config PATH] [--check]
        restrict-openrgb-detectors.py --keep "Some Detector" [--keep ...]
        restrict-openrgb-detectors.py --discover      # enable everything, once
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

# The checkout always contains both halves, so this works when the tool is run
# straight out of it with a bare `python3` — which is how the README tells
# people to re-run it after opening the OpenRGB GUI, long after the virtualenv
# has stopped being on their mind.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mimarchy import detectors as det  # noqa: E402
from mimarchy.config import CONFIG_PATH, load_config  # noqa: E402

FREEZE_WARNING = """\
OpenRGB's broad GPU/I2C detection has been reported to hard-freeze whole
systems (issue #4888, still open), and its server starts at login — so a wide
detector list is a freeze on every boot, not a one-off risk.
"""


def wanted_detectors(config_path: Path, known: list[str],
                     extra: list[str]) -> tuple[set[str], list[str]]:
    """The allowlist, and the lines explaining how it was arrived at.

    Four sources, most explicit first, because each one exists to answer a case
    the next cannot:

    1. `--keep` on the command line — a one-off, and the answer when the
       derivation below is wrong about a specific machine.
    2. `detectors = [...]` in config.toml — what `mimarchy-setup` writes, from
       the device names OpenRGB actually reported. The best information anyone
       has, since the wizard sees full names rather than the substrings the
       zone matcher is happy with.
    3. Derived from the zones' `device` values, for a config edited by hand.
    4. The reference rig's four, when there is no config at all.
    """
    notes: list[str] = []
    keep = set(extra)
    if keep:
        notes.append(f"--keep: {sorted(keep)}")

    config = None
    if config_path.exists():
        try:
            config = load_config(config_path)
        except (OSError, ValueError) as exc:
            notes.append(f"{config_path} could not be read ({exc})")
    else:
        notes.append(f"no config at {config_path}")

    selected = config is not None and (config.detectors or config.zones)
    if config is not None and config.detectors:
        keep |= set(config.detectors)
        notes.append(f"config.toml `detectors`: {sorted(config.detectors)}")
    elif config is not None and config.zones:
        for match in det.resolve([z.device for z in config.zones.values()], known):
            keep |= match.detectors
            if match.note:
                notes.append(f"{match.device!r}: {match.note}")
            if match.detectors:
                notes.append(f"{match.device!r}: {sorted(match.detectors)}")

    # The reference set is for a machine that has made no selection at all —
    # which is the state this tool was written in, and the behaviour it has
    # always had. It is deliberately *not* the answer when a config exists but
    # nothing could be derived from it: quietly enabling one particular I2C card
    # detector because somebody else's rig needed it is the opposite of what the
    # rest of this file is for.
    if not keep and not selected:
        keep = set(det.REFERENCE_KEEP)
        notes.append("no selection to work from — falling back to the "
                     f"reference set: {sorted(keep)}")

    unknown = keep - set(known)
    if unknown:
        # Not fatal: OpenRGB renames detectors between releases, and a name this
        # build does not have simply never gets enabled. Worth saying out loud,
        # because the symptom of a stale name is a device that stops being
        # detected for no visible reason.
        notes.append(f"not offered by this OpenRGB build, ignored: "
                     f"{sorted(unknown)}")
    return keep & set(known), notes


def _write(path: Path, data: dict) -> Path:
    backup = path.with_suffix(f".json.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(path, backup)
    path.write_text(json.dumps(data, indent=4))
    return backup


def cmd_check(detectors: dict[str, bool], keep: set[str],
              notes: list[str]) -> int:
    enabled = {name for name, on in detectors.items() if on}
    unexpected = enabled - keep
    missing = keep - enabled

    for note in notes:
        print(f"    {note}")
    print(f"{len(enabled)} of {len(detectors)} detectors enabled")
    if unexpected:
        print(f"UNSAFE: {len(unexpected)} unexpected detector(s) enabled, "
              f"e.g. {sorted(unexpected)[:5]}")
        print(FREEZE_WARNING)
    if missing:
        print(f"missing expected: {sorted(missing)}")
    if not unexpected and not missing:
        print("OK — exactly the safe set is enabled")
    return 1 if (unexpected or missing) else 0


def cmd_discover(args: argparse.Namespace, data: dict,
                 detectors: dict[str, bool]) -> int:
    """Turn everything back on, deliberately, to find out what is attached.

    The chicken-and-egg this exists for: the detector list is narrowed before
    the server first runs, so a machine whose hardware was never in that narrow
    set sees no devices — and `mimarchy-setup` cannot select what OpenRGB never
    detected. Widening for one detection pass is the only way out, and it is
    the same state a stock OpenRGB install is in permanently.

    Guarded by a typed confirmation rather than a flag: this is the one command
    here that can hang the machine it is run on, and the warning is worth
    making somebody read.
    """
    print(FREEZE_WARNING)
    print("--discover enables all "
          f"{len(detectors)} detectors so OpenRGB can find your hardware.")
    print("Narrow it again with a plain run of this tool as soon as "
          "`mimarchy-setup` has your zones.\n")
    if not args.yes:
        try:
            if input("Type 'discover' to go ahead: ").strip() != "discover":
                print("nothing changed")
                return 1
        except (EOFError, KeyboardInterrupt):
            print("\nnothing changed")
            return 1

    for name in detectors:
        detectors[name] = True
    backup = _write(args.config, data)
    print(f"backup: {backup}")
    print(f"enabled all {len(detectors)}")
    print("restart the server:  systemctl --user restart openrgb.service")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=det.DEFAULT_OPENRGB_CONFIG,
                    help="OpenRGB's config file")
    ap.add_argument("--mimarchy-config", type=Path, default=CONFIG_PATH,
                    help="where to read the selected zones from")
    ap.add_argument("--check", action="store_true",
                    help="report state without modifying anything")
    ap.add_argument("--keep", action="append", default=[], metavar="NAME",
                    help="also enable this detector (repeatable)")
    ap.add_argument("--discover", action="store_true",
                    help="enable every detector so hardware can be found — "
                         "read what it prints first")
    ap.add_argument("--yes", action="store_true",
                    help="skip --discover's confirmation prompt")
    args = ap.parse_args(argv)

    try:
        data, detectors = det.read_config(args.config)
    except det.DetectorConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.discover:
        return cmd_discover(args, data, detectors)

    keep, notes = wanted_detectors(args.mimarchy_config, sorted(detectors),
                                   args.keep)

    if args.check:
        return cmd_check(detectors, keep, notes)

    for note in notes:
        print(f"    {note}")
    if not keep:
        # Writing this out would leave OpenRGB detecting nothing, which is safe
        # and useless. Refusing keeps whatever is currently enabled — including,
        # possibly, everything — so the message has to say that plainly rather
        # than let "it errored, so nothing happened" read as "I am protected".
        print("Nothing to enable: no detector could be matched to your "
              "configured devices.", file=sys.stderr)
        print("The detector list has been left exactly as it is. Fix it with "
              "one of:", file=sys.stderr)
        print("    mimarchy-setup                     # pick zones, and write "
              "the detector list", file=sys.stderr)
        print("    --keep 'Detector Name'             # name them here",
              file=sys.stderr)
        print("    detectors = [...] in config.toml   # or there",
              file=sys.stderr)
        return 1

    for name in detectors:
        detectors[name] = name in keep
    backup = _write(args.config, data)

    print(f"backup: {backup}")
    print(f"enabled {len(keep)} of {len(detectors)}: {sorted(keep)}")
    print("restart the server:  systemctl --user restart openrgb.service")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
