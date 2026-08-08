"""Daemon that keeps the cooler's display alive with live CPU telemetry.

The panel has no on/off command: it lights up while frames arrive and blanks
when they stop. So "turning the display on" means running this.
"""

from __future__ import annotations

import argparse
import signal
import sys

from mimarchy.config import load_config
from mimarchy.display import ProtocolUnknownError, DEFAULT_INTERVAL, CPUDisplay, DisplayFrame, stream
from mimarchy.hwmon import (
    read_cpu_fan_rpm,
    read_cpu_load,
    read_cpu_temp,
    read_gpu_temp,
)


def build_frame() -> DisplayFrame:
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

    config = load_config()
    display = CPUDisplay(config.display)

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
        sys.exit(str(exc))
    except PermissionError:
        sys.exit(
            "Permission denied opening the display. Install "
            "udev/99-mimarchy.rules (see README)."
        )


if __name__ == "__main__":
    main()
