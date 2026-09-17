"""The two daemon lifecycles: pidfiles, liveness, spawn, and stop.

Why a pidfile and not a socket: the same reasons `lightstate` cites for its
state file. The daemons have to survive the process that started them — the
panel spawns them and goes back to polling, and `mimarchy-ctl` is a one-shot
process that exits immediately — so there is nobody to hold the other end of
a socket. The payload is a single pid, which needs no framing, no port, and
no event loop. And a pidfile degrades honestly: a stale entry points at a
dead pid or a recycled one, and `/proc` tells those apart from a live owner,
so "is it running" never needs a second channel.

Layout is `$XDG_RUNTIME_DIR/mimarchy-lightd.pid` and
`$XDG_RUNTIME_DIR/mimarchy-displayd.pid`, with the same runtime-dir fallback
as `lightstate.STATE_PATH` (the runtime dir when set, otherwise the config
home). The runtime dir is cleared on logout, so a pidfile can never claim a
daemon survived a reboot.

A daemon that loses the pidfile race is not an error. The panel and a manual
`display on` can both try to start the same daemon, and the loser exiting 0
with the owner pid on stderr keeps the panel's restart backoff from
mistaking a healthy already-running daemon for a crash loop.

Fatal daemon exits (no device, no permission) additionally write a
`mimarchy-<name>.note` file: one line telling the user what to do, which is
what `ctl status` surfaces as `daemon_note`. The note is cleared on the next
successful start, so it can never describe a daemon that is currently fine.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

#: The one manual step left after install: the board's hidraw node is
#: root-only until this rule lands, so a daemon that cannot open any
#: controller tells the user exactly this instead of a bare Errno 13.
UDEV_NOTE = (
    "The LED hidraw node is root-only until the udev rule lands: "
    "sudo cp udev/99-mimarchy.rules /etc/udev/rules.d/ "
    "&& sudo udevadm control --reload-rules && sudo udevadm trigger, "
    "then re-plug the device (see README)."
)


def _runtime_dir() -> Path:
    # Same fallback as lightstate.STATE_PATH: a function rather than a
    # module constant so tests can point it at a temp dir through the
    # environment instead of reaching into module globals.
    return Path(
        os.environ.get("XDG_RUNTIME_DIR")
        or os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
    )


def _pidfile(name: str) -> Path:
    return _runtime_dir() / f"mimarchy-{name}.pid"


def _notefile(name: str) -> Path:
    return _runtime_dir() / f"mimarchy-{name}.note"


def daemon_script(name: str) -> Path:
    """The `bin/mimarchy-<name>` launcher, resolved from this file's real path.

    The panel and `mimarchy-ctl` both spawn daemons through this, so a
    checkout, a plugin clone, and an installed copy all start the backend
    they sit next to rather than whatever happens to be on `$PATH`.
    """
    return Path(__file__).resolve().parents[2] / "bin" / f"mimarchy-{name}"


def owner_pid(name: str) -> int | None:
    """The pid the pidfile names, or None when it names nothing usable."""
    try:
        return int(_pidfile(name).read_text().strip().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def daemon_alive(name: str, *, _proc_root: Path | None = None) -> bool:
    """Whether the pidfile names a live daemon, via pidfile plus `/proc`.

    The pidfile alone cannot answer this: the daemon may have died without
    removing it, and its pid may since have been recycled by an unrelated
    process. So a pid only counts when `/proc/<pid>/cmdline` still names the
    daemon's launcher (`mimarchy-lightd` / `mimarchy-displayd`). Anything
    else — missing file, dead pid, recycled pid — is "not running", never an
    error, because the CLI is routinely run where no daemon was ever started.

    `_proc_root` exists for the tests, which plant fake `/proc/<pid>/cmdline`
    files under a temp dir instead of forking real daemons.
    """
    if owner_pid(name) is None:
        return False
    pid = owner_pid(name)
    try:
        parts = ((_proc_root or Path("/proc")) / str(pid) / "cmdline").read_bytes()
    except OSError:
        return False
    marker = f"mimarchy-{name}".encode()
    return marker in parts


def claim_pidfile(name: str, *, _proc_root: Path | None = None) -> bool:
    """Take ownership of the daemon slot. False when a live owner exists.

    A stale pidfile — dead pid, recycled pid, garbage — is overwritten: the
    previous owner is gone, so the slot is free no matter what the file says.
    Only a live owner refuses the claim, and the file is left untouched then
    so the loser cannot orphan the winner's pid.
    """
    if daemon_alive(name, _proc_root=_proc_root):
        return False
    pidfile = _pidfile(name)
    pidfile.parent.mkdir(parents=True, exist_ok=True)
    pidfile.write_text(f"{os.getpid()}\n")
    return True


def stop_daemon(name: str, *, _proc_root: Path | None = None) -> str | None:
    """SIGTERM the daemon and wait for it to go. An error string, or None.

    Stopping something that is not running is success, not an error: `display
    off` from a script must not fail just because the stream already ended.
    """
    if not daemon_alive(name, _proc_root=_proc_root):
        return None
    pid = owner_pid(name)
    if pid is None:  # pragma: no cover — the pidfile changed mid-call
        return None
    marker = f"mimarchy-{name}"
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return None
    except OSError as exc:
        return f"could not stop {marker} (pid {pid}): {exc.strerror or exc}"
    deadline = time.monotonic() + 2.0
    while daemon_alive(name, _proc_root=_proc_root):
        if time.monotonic() >= deadline:
            return f"{marker} (pid {pid}) did not exit"
        time.sleep(0.05)
    return None


def spawn_daemon(script_path: str | Path, *args: str) -> None:
    """Start a daemon detached: new session, no stdio, no waiting.

    The caller polls `daemon_alive` to confirm the start — the daemon claims
    its pidfile only once it is actually up, so the pidfile appearing is the
    acknowledgement. A spawn failure (missing interpreter, missing script)
    raises here rather than returning a string, so the caller reports it
    next to the same plain message it prints when the daemon never appears.
    """
    subprocess.Popen(
        ["/usr/bin/python3", "-I", "-S", os.fspath(script_path), *args],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


def read_note(name: str) -> str | None:
    """The daemon's last fatal-exit note, or None when there is none."""
    try:
        text = _notefile(name).read_text().strip()
    except OSError:
        return None
    return text or None


def write_note(name: str, text: str) -> None:
    """Leave the one-line user action a fatal exit wants the panel to show."""
    try:
        path = _notefile(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text.strip() + "\n")
    except OSError:
        # Best-effort by design: this runs on the way out of a daemon that is
        # already dying, and a lost note must not mask the real exit.
        pass


def clear_note(name: str) -> None:
    """Forget the last fatal-exit note; called on every successful start."""
    try:
        _notefile(name).unlink(missing_ok=True)
    except OSError:
        pass


def ensure_theme_hook() -> None:
    """Link the theme-set hook into the omarchy hooks dir, if this is one.

    The light daemon owns this because it is the one long-lived backend
    process that is always there after login: the hook re-resolves
    theme-following colours, and without the link a theme switch would leave
    the LEDs on the old palette until someone re-ran setup. Skipped entirely
    when `~/.config/omarchy` is absent (not an omarchy machine) or when the
    repo hook is missing (an installed copy without the checkout layout).
    Never raises — a lighting daemon must not die over a convenience symlink.
    """
    try:
        config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        if not (config_home / "omarchy").exists():
            return
        target = (
            Path(__file__).resolve().parents[2]
            / "omarchy"
            / "theme-set.d"
            / "mimarchy"
        )
        if not target.is_file():
            return
        dest = config_home / "omarchy" / "hooks" / "theme-set.d" / "mimarchy"
        if dest.is_symlink() and dest.readlink() == target:
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_symlink() or dest.exists():
            dest.unlink()
        dest.symlink_to(target)
    except OSError:
        pass
