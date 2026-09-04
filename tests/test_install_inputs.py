"""install.sh's inputs stay pinned, and its one privileged step stays honest.

Marketplace review is bound to an exact commit, which only means something if
that commit fully determines what an install does. Two properties make that
true here, and each is spread across more than one file — exactly the kind of
agreement that drifts unless a test holds it still:

* Every Python package install.sh fetches is pinned in requirements.lock by
  version and artifact hash, and install.sh actually enforces the lock:
  `--require-hashes` for the closure, then `--no-deps --no-build-isolation`
  for the checkout itself so nothing arrives through dependency resolution or
  a freshly fetched build backend.

* The udev rule a user installs with sudo is spelled out inline in the
  printed command instead of being read out of this user-writable checkout at
  elevation time — and that printed line must stay byte-identical to the
  reference copy in udev/99-mimarchy.rules.

* install.sh never runs OpenRGB's detection itself — the first-run pass is
  the documented freeze hazard, so it is printed for the user to run on
  purpose — and it enables the server only inside the branch where
  `restrict-openrgb-detectors.py --check` verified the detector list.

* The SDK server is unauthenticated, so the unit binds it to loopback, and the
  unit's bind address is the address the client connects to.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
INSTALL_SH = (REPO / "install.sh").read_text()
LOCK = (REPO / "requirements.lock").read_text()


def locked_requirements() -> dict[str, list[str]]:
    """Pinned name -> its --hash values, with continuation lines joined."""
    pins: dict[str, list[str]] = {}
    for line in re.sub(r"\\\n", " ", LOCK).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"([A-Za-z0-9._-]+)==\S+", line)
        assert match, f"unparseable requirements.lock line: {line!r}"
        pins[match.group(1)] = re.findall(r"--hash=sha256:[0-9a-f]{64}", line)
    return pins


def test_lock_covers_every_runtime_dependency():
    """A dependency pyproject.toml declares but the lock does not pin would be
    resolved from PyPI on install day — the exact thing the lock exists to
    prevent."""
    block = re.search(r"^dependencies = \[(.*?)^\]", (REPO / "pyproject.toml").read_text(),
                      re.M | re.S).group(1)
    names = re.findall(r'"([A-Za-z0-9._-]+)', block)

    assert names, "pyproject.toml dependencies block not found"
    pins = locked_requirements()
    for name in names:
        assert name in pins, f"{name} is a runtime dependency but is not pinned in requirements.lock"


def test_lock_pins_the_build_toolchain():
    """--no-build-isolation makes the venv's setuptools the build backend, and
    install.sh brings pip to a known version instead of "latest" — so both are
    install inputs and must be locked like everything else."""
    pins = locked_requirements()
    for name in ("pip", "setuptools"):
        assert name in pins, f"{name} must be pinned in requirements.lock"


def test_every_pin_carries_wheel_and_sdist_hashes():
    """Two hashes per pin: the wheel and the sdist, so the lock still holds
    anywhere pip falls back to building from source."""
    for name, hashes in locked_requirements().items():
        assert len(hashes) >= 2, f"{name} needs both a wheel and an sdist hash, has {len(hashes)}"


def test_install_sh_enforces_the_lock():
    """Pins nobody applies are documentation. The install commands themselves
    must refuse unhashed downloads and skip resolution for the checkout."""
    assert '--require-hashes -r "$REPO/requirements.lock"' in INSTALL_SH
    assert '--no-deps --no-build-isolation -e "$REPO"' in INSTALL_SH
    assert "--upgrade pip" not in INSTALL_SH, "pip must come from the lock, not from 'latest'"


def test_printed_udev_rule_matches_the_reference_file():
    """The rule is deliberately duplicated: inline in the printed sudo command
    (so root never reads the checkout) and as udev/99-mimarchy.rules (the
    readable reference). Byte-identical or the docs lie about the rule."""
    printed = re.search(r"'(SUBSYSTEM==\"hidraw\"[^']*)'", INSTALL_SH)
    assert printed, "install.sh no longer prints the udev rule inline"

    reference = [line for line in (REPO / "udev" / "99-mimarchy.rules").read_text().splitlines()
                 if line and not line.startswith("#")]
    assert reference == [printed.group(1)]


def test_udev_rule_applies_its_own_uaccess_acl():
    """The rule must grant access through logind's ACL, not through a group.

    Omarchy 4 removes users from `input` (its migration of 2026-09-03: the
    group is raw access to every keyboard), so GROUP="input" grants nothing
    there. The `uaccess` tag is what works — but only if this rule runs the
    builtin itself: 73-seat-late.rules acts on the tag before a 99- rule has
    set it, which is how the display stayed root-only for a whole session
    with the tag present and nobody the wiser.
    """
    reference = [line for line in (REPO / "udev" / "99-mimarchy.rules").read_text().splitlines()
                 if line and not line.startswith("#")]
    (rule,) = reference
    assert 'TAG+="uaccess"' in rule
    assert 'RUN{builtin}+="uaccess"' in rule, "the tag alone is set too late to be acted on"
    assert 'MODE="0666"' not in rule and 'MODE="0777"' not in rule


def test_no_privileged_step_reads_the_checkout():
    """The reviewed commit cannot vouch for a file root reads out of a
    user-writable directory at some later elevation time."""
    assert not re.search(r"sudo\s+(cp|install)\b[^\n]*\$REPO", INSTALL_SH), (
        "install.sh prints a sudo command that reads from the checkout"
    )


def executed_lines(script: str) -> str:
    """The script minus its printed heredocs — what it runs, not what it says.

    The printed text quotes commands for the user to run deliberately, the
    detection pass among them, so a check on the raw file would see the very
    command it exists to rule out.
    """
    return re.sub(r"cat <<'?EOF'?\n.*?\nEOF\n", "", script, flags=re.S)


def test_install_never_runs_openrgb_detection_itself():
    """OpenRGB's first run creates its config by probing with every detector
    enabled — the documented freeze hazard. A script that runs it without
    asking has not asked, so it is printed for the user and never executed."""
    code = executed_lines(INSTALL_SH)
    assert "openrgb --list-devices" not in code
    assert re.search(r"^\s*openrgb\b", code, re.M) is None
    assert "openrgb --list-devices" in INSTALL_SH   # still told how, just not done for them


def test_services_start_only_after_the_detector_list_verifies():
    """The enable lives inside the branch a passing --check guards, and nowhere
    else — an enabled server with an unverified list is the every-boot freeze."""
    code = executed_lines(INSTALL_SH)
    gate = re.search(r'if "\$BIN/python" "\$REPO/tools/restrict-openrgb-detectors\.py" --check; then'
                     r'\s*DETECTORS_SAFE=yes', code)
    assert gate, "DETECTORS_SAFE must be set only by a passing --check"
    assert code.count("DETECTORS_SAFE=yes") == 1

    branch = re.search(r'if \[\[ "\$DETECTORS_SAFE" == yes \]\]; then(.*?)\nelse', code, re.S)
    assert branch, "service enabling must be gated on DETECTORS_SAFE"
    for unit in ("openrgb.service", "mimarchy-light.service"):
        assert f"enable --now {unit}" in branch.group(1), unit
        assert code.count(f"enable --now {unit}") == 1, f"{unit} is enabled outside the gate"


def test_sdk_server_binds_loopback_and_matches_the_client():
    """The SDK protocol is unauthenticated. The unit binds it to 127.0.0.1
    explicitly rather than trusting OpenRGB's default, and that must be the
    address the client connects to — one setting that lives in two files."""
    unit = (REPO / "systemd" / "openrgb.service").read_text()
    exec_start = re.search(r"^ExecStart=(.+)$", unit, re.M).group(1)
    assert "--server-host 127.0.0.1" in exec_start
    assert "--server " in exec_start

    rgb = (REPO / "src" / "mimarchy" / "rgb.py").read_text()
    assert re.search(r'host: str = "127\.0\.0\.1"', rgb)
