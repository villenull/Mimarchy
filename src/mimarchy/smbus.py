"""I2C/SMBus byte access over `/dev/i2c-*`, stdlib only.

Only what the Sapphire Nitro Glow V3 needs: set the slave address, then the
same SMBus transactions OpenRGB and `i2cdetect`/`i2cget` use — single-byte
read, register read, register write — issued as one `I2C_SMBUS` ioctl each, so
register reads get the repeated start the controller expects rather than a
stop-start pair from separate `read()`/`write()` calls.

No probing here: enumerating buses and deciding which address to ask about is
the caller's job (`nitro.find`), because a broad scan is the freeze this
project used to guard against with a detector allowlist. This module touches
exactly the bus and address it is handed.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import errno
import os

I2C_SLAVE = 0x0703
I2C_SMBUS = 0x0720

_READ = 1
_WRITE = 0

_SMBUS_BYTE = 1
_SMBUS_BYTE_DATA = 2


class I2cError(RuntimeError):
    """The bus would not open, or the controller did not answer."""


class _SmbusData(ctypes.Union):
    _fields_ = [
        ("byte", ctypes.c_uint8),
        ("word", ctypes.c_uint16),
        ("block", ctypes.c_uint8 * 34),
    ]


class _SmbusIoctlData(ctypes.Structure):
    _fields_ = [
        ("read_write", ctypes.c_uint8),
        ("command", ctypes.c_uint8),
        ("size", ctypes.c_int),
        ("data", ctypes.POINTER(_SmbusData)),
    ]


_libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)


def _ioctl(fd: int, request: int, arg) -> None:
    if _libc.ioctl(fd, request, arg) < 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


class I2cBus:
    """One open `/dev/i2c-N`, speaking SMBus to one address at a time."""

    def __init__(self, bus: int):
        self._path = f"/dev/i2c-{bus}"
        try:
            self._fd = os.open(self._path, os.O_RDWR)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EPERM):
                raise I2cError(
                    f"cannot open {self._path}: permission denied. Add your "
                    "user to the i2c group (`sudo usermod -aG i2c $USER`) "
                    "and log back in, or run as root."
                ) from exc
            raise I2cError(
                f"cannot open {self._path}: {exc.strerror or exc}"
            ) from exc

    def _select(self, addr: int) -> None:
        try:
            _ioctl(self._fd, I2C_SLAVE, ctypes.c_int(addr))
        except OSError as exc:
            raise I2cError(
                f"cannot address 0x{addr:02x} on {self._path}: {exc}"
            ) from exc

    def _transfer(self, addr: int, read_write: int, command: int,
                  size: int) -> _SmbusData:
        self._select(addr)
        data = _SmbusData()
        args = _SmbusIoctlData(read_write, command, size,
                               ctypes.pointer(data))
        try:
            _ioctl(self._fd, I2C_SMBUS, ctypes.byref(args))
        except OSError as exc:
            raise I2cError(
                f"0x{addr:02x} on {self._path} did not answer: {exc}"
            ) from exc
        return data

    def read_byte(self, addr: int) -> int:
        """One byte, no register — the presence probe."""
        return self._transfer(addr, _READ, 0, _SMBUS_BYTE).byte

    def read_byte_data(self, addr: int, register: int) -> int:
        return self._transfer(addr, _READ, register, _SMBUS_BYTE_DATA).byte

    def write_byte_data(self, addr: int, register: int, value: int) -> None:
        self._select(addr)
        data = _SmbusData()
        data.byte = value & 0xFF
        args = _SmbusIoctlData(_WRITE, register, _SMBUS_BYTE_DATA,
                               ctypes.pointer(data))
        try:
            _ioctl(self._fd, I2C_SMBUS, ctypes.byref(args))
        except OSError as exc:
            raise I2cError(
                f"write to 0x{addr:02x}/0x{register:02x} failed: {exc}"
            ) from exc

    def close(self) -> None:
        try:
            os.close(self._fd)
        except OSError:
            pass

    def __enter__(self) -> "I2cBus":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
