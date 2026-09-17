"""Daemon that keeps the cooler's display alive with live CPU telemetry.

The panel has no on/off command: it lights up while frames arrive and blanks
when they stop. So "turning the display on" means running this.
"""

from __future__ import annotations

import argparse
import signal
import sys
from typing import NoReturn

from mimarchy import hidraw
from mimarchy.config import load_config
from mimarchy.display import (CPUDisplay, DEFAULT_INTERVAL, DisplayFrame,
                              ProtocolUnknownError, stream)
from mimarchy.hwmon import (
    read_cpu_fan_rpm,
    read_cpu_load,
    read_cpu_temp,
    read_gpu_temp,
)
from mimarchy.service import (claim_pidfile, clear_note, owner_pid, write_note)


def build_frame() -> DisplayFrame:
    # The RPM comes from read_cpu_fan_rpm(): nct6687's "CPU Fan" (the
    # out-of-tree nct6687d driver, force=1 plus acpi_enforce_resources=lax;
    # see docs/hardware-notes.md). The 64-byte frame has no "unknown"
    # encoding, so a missing reading (None, e.g. driver not loaded) still
    # sends 0 on the wire and the panel truncates to the nearest 100, which
    # renders as 0. `mimarchy-ctl status` forwards the None, not this 0,
    # which is where "—" lives. The GPU's own fan1 is never the CPU fan
    # (see hwmon.read_cpu_fan_rpm).
    return DisplayFrame(
        cpu_temp=read_cpu_temp() or 0,
        cpu_load=read_cpu_load(),
        gpu_temp=read_gpu_temp() or 0,
        rpm=read_cpu_fan_rpm() or 0,
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--interval", type=float, default=DEFAULT_INTERVAL,
                    help=f"seconds between frames (default {DEFAULT_INTERVAL})")
    ap.add_argument("--once", action="store_true",
                    help="send a single frame and exit (for testing)")
    args = ap.parse_args()

    if not args.once and not claim_pidfile("displayd"):
        # Same race as lightd: a manual `display on` next to the panel's
        # supervision. Exit 0 with the owner named so this reads as a clean
        # no-op. --once never claims — it is a probe that must leave a
        # running daemon alone.
        print(f"mimarchy-displayd already running (pid {owner_pid('displayd')})",
              file=sys.stderr)
        return
    if not args.once:
        clear_note("displayd")

    config = load_config()

    def _fail(message: str) -> "NoReturn":
        # Fatal exits leave the one-line user action behind for `status`,
        # which is what surfaces it in the panel without anyone reading logs.
        # --once is a probe, not a daemon start: it reports the error but
        # leaves the running daemon's note alone.
        if not args.once:
            write_note("displayd", message)
        sys.exit(message)

    try:
        display = CPUDisplay(config.display)
    except ProtocolUnknownError as exc:
        _fail(str(exc))
    except hidraw.HidError as exc:
        _fail(str(exc))
    except PermissionError:
        _fail(
            "Permission denied opening the display's hidraw node. Install "
            "udev/99-mimarchy.rules, reload udev, and re-plug the device "
            "(see README)."
        )

    try:
        if args.once:
            frame = build_frame()
            display.send(frame)
            print(f"sent: cpu={frame.cpu_temp}C load={frame.cpu_load}% "
                  f"gpu={frame.gpu_temp}C rpm={frame.rpm}")
            display.close()
            return

        stopping = False

        def _stop(*_: object) -> None:
            nonlocal stopping
            stopping = True

        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)

        stream(display, build_frame, interval=args.interval,
               stop=lambda: stopping)
    except ProtocolUnknownError as exc:
        _fail(str(exc))
    except hidraw.HidError as exc:
        _fail(str(exc))
    except PermissionError:
        _fail(
            "Permission denied opening the display's hidraw node. Install "
            "udev/99-mimarchy.rules, reload udev, and re-plug the device "
            "(see README)."
        )


if __name__ == "__main__":
    main()
