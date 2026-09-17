"""Tests for `mimarchy.service`: pidfile claim, liveness, stop, and notes.

Pidfile ownership is genuinely racy logic — stale entries, recycled pids,
and two spawners racing — so it earns permanent tests. Everything here runs
against a temp runtime dir (via `XDG_RUNTIME_DIR`) and a fake `/proc`
(planted `cmdline` files), except the live-stop test, which kills a real
`sleep` whose argv carries the daemon marker so the real `/proc` agrees it
is the owner.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from mimarchy import service  # noqa: E402


@pytest.fixture
def proc(tmp_path, monkeypatch):
    """Temp runtime dir plus an empty fake `/proc`; returns the fake root."""
    run = tmp_path / "run"
    run.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(run))
    fake = tmp_path / "proc"
    fake.mkdir()
    return fake


def _plant(proc: Path, pid: int, *parts: str) -> None:
    """Give the fake `/proc` a `<pid>/cmdline` naming the given argv."""
    entry = proc / str(pid)
    entry.mkdir(exist_ok=True)
    (entry / "cmdline").write_bytes(b"\0".join(p.encode() for p in parts))


def test_claim_writes_own_pid(proc):
    assert service.claim_pidfile("lightd", _proc_root=proc) is True
    assert service.owner_pid("lightd") == os.getpid()


def test_no_pidfile_is_not_running(proc):
    assert service.daemon_alive("lightd", _proc_root=proc) is False
    assert service.owner_pid("lightd") is None


def test_stale_pidfile_is_overwritten(proc):
    service._pidfile("lightd").write_text("4242\n")
    assert service.daemon_alive("lightd", _proc_root=proc) is False
    assert service.claim_pidfile("lightd", _proc_root=proc) is True
    assert service.owner_pid("lightd") == os.getpid()


def test_recycled_pid_is_not_the_owner(proc):
    """The pidfile's pid now belongs to an unrelated process: not running."""
    service._pidfile("lightd").write_text("4242\n")
    _plant(proc, 4242, "/usr/bin/python3", "something-else-entirely")
    assert service.daemon_alive("lightd", _proc_root=proc) is False
    assert service.claim_pidfile("lightd", _proc_root=proc) is True


def test_garbage_pidfile_is_reclaimed(proc):
    service._pidfile("lightd").write_text("not-a-pid\n")
    assert service.daemon_alive("lightd", _proc_root=proc) is False
    assert service.claim_pidfile("lightd", _proc_root=proc) is True


def test_live_owner_refuses_the_claim_and_keeps_its_pid(proc):
    _plant(proc, 4242, "/usr/bin/python3", "-I", "-S", "bin/mimarchy-lightd")
    service._pidfile("lightd").write_text("4242\n")
    assert service.daemon_alive("lightd", _proc_root=proc) is True
    assert service.claim_pidfile("lightd", _proc_root=proc) is False
    assert service._pidfile("lightd").read_text() == "4242\n"


def test_claim_then_alive_with_own_cmdline(proc, monkeypatch):
    """End to end on the fake proc: claim, then the owner looks alive."""
    assert service.claim_pidfile("lightd", _proc_root=proc) is True
    _plant(proc, os.getpid(), "/usr/bin/python3", "bin/mimarchy-lightd")
    assert service.daemon_alive("lightd", _proc_root=proc) is True


def test_stop_on_a_stopped_daemon_is_success(proc):
    assert service.stop_daemon("lightd", _proc_root=proc) is None
    service._pidfile("lightd").write_text("4242\n")
    assert service.stop_daemon("lightd", _proc_root=proc) is None


def test_stop_terminates_a_live_owner(proc):
    """A real process whose argv carries the marker, stopped via real /proc."""
    child = subprocess.Popen(
        [sys.executable, "-c",
         "import time; marker = 'mimarchy-lightd'; time.sleep(60)"]
    )
    try:
        service._pidfile("lightd").write_text(f"{child.pid}\n")
        assert service.daemon_alive("lightd") is True
        assert service.stop_daemon("lightd") is None
        assert service.daemon_alive("lightd") is False
    finally:
        child.kill()
        child.wait()


def test_spawn_daemon_runs_the_script_detached(tmp_path, proc):
    out = tmp_path / "spawned"
    script = tmp_path / "fake-daemon"
    script.write_text(
        "#!/usr/bin/python3\nimport sys, pathlib\npathlib.Path(sys.argv[1]).touch()\n"
    )
    service.spawn_daemon(script, str(out))
    deadline = time.monotonic() + 5.0
    while not out.exists():
        assert time.monotonic() < deadline, "spawned script never ran"
        time.sleep(0.05)


def test_note_round_trip(proc):
    assert service.read_note("lightd") is None
    service.write_note("lightd", "do the sudo udev thing")
    assert service.read_note("lightd") == "do the sudo udev thing"
    service.clear_note("lightd")
    assert service.read_note("lightd") is None


def test_daemon_script_resolves_into_this_checkout():
    path = service.daemon_script("displayd")
    assert path.name == "mimarchy-displayd"
    assert path.parent.name == "bin"
    assert path.is_file()


def test_ensure_theme_hook_skipped_without_an_omarchy_config(tmp_path, monkeypatch):
    cfg = tmp_path / "config"
    cfg.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    assert service.ensure_theme_hook() is None
    assert list(cfg.iterdir()) == []


def test_ensure_theme_hook_links_into_omarchy(tmp_path, monkeypatch):
    cfg = tmp_path / "config"
    (cfg / "omarchy").mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    service.ensure_theme_hook()
    dest = cfg / "omarchy" / "hooks" / "theme-set.d" / "mimarchy"
    assert dest.is_symlink()
    assert dest.readlink().is_file()
    # Second run is a no-op, not a second link or an error.
    service.ensure_theme_hook()
    assert dest.is_symlink()
