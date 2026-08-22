"""The two systemd user units, and the handful of calls that drive them.

Pulled out of the TUI when `mimarchy-ctl` arrived, because both now need to ask
the same two questions — is the lighting daemon running, is the display stream
running — and answer them the same way. Two copies of a `systemctl --user`
invocation is the kind of duplication that stays correct right up until one of
them learns about an edge case the other does not.

Nothing here raises. A machine where the units are not installed, or where
there is no user bus at all, is a normal thing for the CLI to be run on — the
answer there is "not active", not a traceback.
"""

from __future__ import annotations

import subprocess

#: The telemetry stream. Starting and stopping this unit is what turns the
#: cooler panel on and off — there is no off command in its protocol.
DISPLAY_UNIT = "mimarchy-display.service"

#: The effect renderer. Lighting only animates while this runs; stopping it
#: freezes the LEDs on their last frame rather than clearing them.
LIGHT_UNIT = "mimarchy-light.service"


def _systemctl(*args: str) -> subprocess.CompletedProcess[str]:
    """`systemctl --user`, never raising.

    A missing systemctl (a container, a non-systemd machine) comes back as a
    synthetic failure so callers can treat it exactly like a unit that is not
    running, which is what it amounts to.
    """
    try:
        return subprocess.run(["systemctl", "--user", *args],
                              capture_output=True, text=True)
    except (OSError, ValueError):
        return subprocess.CompletedProcess(args=args, returncode=1,
                                           stdout="", stderr="systemctl unavailable")


def unit_active(unit: str) -> bool:
    return _systemctl("is-active", unit).stdout.strip() == "active"


def set_unit(unit: str, running: bool) -> str | None:
    """Start or stop `unit`. Returns an error message, or None on success.

    The message is the last line of stderr rather than the whole of it: systemd
    is happy to explain a failure over five lines, and the caller here is a
    one-line status field in a TUI or a bar panel. The synthesised fallback
    names the unit rather than saying "the display", which was right when this
    only ever drove the display and is wrong now that it also drives the
    lighting daemon.
    """
    action = "start" if running else "stop"
    result = _systemctl(action, unit)
    if not result.returncode:
        return None
    return (result.stderr.strip().splitlines() or
            [f"could not {action} {unit}"])[-1]
