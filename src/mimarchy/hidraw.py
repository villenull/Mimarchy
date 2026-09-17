"""Raw hidraw access, stdlib only.

Two devices here speak USB HID directly: the ASUS Aura controller (`0b05:19af`,
lighting) and the cooler display (`5131:2007`, already driven this way in
spirit — `display.py` just did it through the `hid` package). Both use
interrupt transfers with no numbered reports: a 65-byte write (report ID byte
plus 64 payload) and a 65-byte read.

hidapi's `hid_write`/`hid_read` do exactly this underneath — a plain `write()`
to the hidraw node and a blocking `read()` back — so there is nothing to lose
by doing it directly, and one third-party dependency to lose by doing so.
"""

from __future__ import annotations

import errno
import os
import select
from dataclasses import dataclass


REPORT_LEN = 65
READ_TIMEOUT = 1.0


class HidError(RuntimeError):
    """No matching device, or it would not talk."""


@dataclass
class HidNode:
    vendor_id: int
    product_id: int
    path: str


def _hid_id(path: str) -> tuple[int | None, int | None]:
    """The USB vendor/product pair from a HID device's uevent file.

    The ids live in `HID_ID=bus:vendor:product` — there are no per-id `vendor`
    / `product` files at this level, only inside the parent USB device.
    """
    try:
        with open(os.path.join(path, "uevent")) as handle:
            for line in handle:
                if line.startswith("HID_ID="):
                    parts = line.strip().split("=", 1)[1].split(":")
                    return int(parts[1], 16), int(parts[2], 16)
    except (OSError, ValueError):
        pass
    return None, None

def find(vendor_id: int, product_id: int,
         sysfs: str = "/sys/bus/hid/devices") -> list[HidNode]:
    """Every hidraw node whose USB ids match, in a stable order.

    Walks sysfs rather than trusting `/dev/hidrawN` numbering, which is
    allocation order and means nothing. `sysfs` is injectable so tests can
    point it at a fake tree.
    """
    found: list[HidNode] = []
    try:
        entries = sorted(os.listdir(sysfs))
    except OSError:
        return []
    for entry in entries:
        base = os.path.join(sysfs, entry)
        if _hid_id(base) != (vendor_id, product_id):
            continue
        try:
            children = sorted(os.listdir(os.path.join(base, "hidraw")))
        except OSError:
            continue
        for child in children:
            found.append(HidNode(vendor_id, product_id,
                                 os.path.join("/dev", child)))
    return found


class HidDevice:
    """An open hidraw node. `write` takes the 64 payload bytes; the leading
    report-ID byte on the wire is 0 for these devices' interrupt endpoint,
    except Aura which addresses its protocol by leading with 0xEC itself."""

    def __init__(self, path: str):
        try:
            self._fd = os.open(path, os.O_RDWR)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EPERM):
                raise HidError(
                    f"cannot open {path}: permission denied. The node is "
                    "root-only until the udev rule lands — install "
                    "/etc/udev/rules.d/99-mimarchy.rules (see the README), "
                    "reload udev, and replug; or run as root."
                ) from exc
            raise HidError(
                f"cannot open {path}: {exc.strerror or exc}. "
                "If this is a fresh install, the udev rule has not applied "
                "yet — see the README."
            ) from exc
        self._path = path

    def write(self, data: bytes) -> None:
        """One 65-byte report, first byte included, written verbatim.

        hidapi's `hid_write` does exactly this underneath — a plain write()
        of the whole report to the hidraw node — so this takes the same
        bytes: the report id first, then payload.
        """
        try:
            os.write(self._fd, data)
        except OSError as exc:
            raise HidError(f"write to {self._path} failed: {exc}") from exc


    def read(self, size: int = REPORT_LEN,
             timeout: float = READ_TIMEOUT) -> bytes:
        try:
            ready, _, _ = select.select([self._fd], [], [], timeout)
        except (OSError, ValueError) as exc:
            raise HidError(f"read from {self._path} failed: {exc}") from exc
        if not ready:
            raise HidError(f"timed out reading {self._path}")
        try:
            return os.read(self._fd, size)
        except OSError as exc:
            raise HidError(f"read from {self._path} failed: {exc}") from exc

    def close(self) -> None:
        try:
            os.close(self._fd)
        except OSError:
            pass

    def __enter__(self) -> "HidDevice":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
