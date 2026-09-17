"""The plugin layout stays honest: no installer, no units, docs match the rules.

The whole install is `omarchy plugin add ... --enable` — the panel owns the
daemon lifecycle from the checkout it landed in. That only means something if
the checkout fully determines what an install does, so the agreements that
span more than one file live here, held still by tests:

* install.sh and systemd/ are gone: no script, no unit template, no
  bootstrapping narrative for them anywhere in the user-facing docs.
* The backend stays stdlib-only: no lockfile, no runtime dependencies.
* The udev rules a user installs with sudo are spelled out inline in the
  README's printed command instead of being read out of this user-writable
  checkout at elevation time — and those printed lines must stay
  byte-identical to the reference copy in udev/99-mimarchy.rules.
* bin/ holds the real launchers the panel spawns, and the theme hook finds
  them relative to its own real path rather than by a hardcoded home.
"""

from __future__ import annotations

import json
import py_compile
import re
import stat
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

LAUNCHERS = ("mimarchy-ctl", "mimarchy-lightd", "mimarchy-displayd", "mimarchy-setup")


def reference_rules() -> list[str]:
    return [line for line in (REPO / "udev" / "99-mimarchy.rules").read_text().splitlines()
            if line and not line.startswith("#")]


def test_installer_and_units_are_gone():
    """The plugin-add install has nothing to bootstrap from."""
    assert not (REPO / "install.sh").exists()
    assert not (REPO / "systemd").exists()
    assert not (REPO / "requirements.lock").exists()


def test_backend_installs_lock_free_with_no_deps():
    """Stdlib-only means no lockfile and no dependency resolution."""
    project = re.search(r"^\[project\](.*?)^\[", (REPO / "pyproject.toml").read_text(), re.M | re.S).group(1)
    block = re.search(r"^dependencies = \[(.*?)\]", project, re.M | re.S).group(1)
    assert re.findall(r'"([A-Za-z0-9._-]+)', block) == []


def test_printed_udev_rules_match_the_reference_file():
    """The rules are deliberately duplicated: inline in the README's printed
    sudo command (so root never reads the checkout) and as
    udev/99-mimarchy.rules (the readable reference). Byte-identical or the
    docs lie about the rules."""
    printed = re.findall(r"'(SUBSYSTEM==\"hidraw\"[^']*)'", (REPO / "README.md").read_text())
    assert printed, "README no longer prints the udev rules inline"
    assert reference_rules() == printed


def test_both_controllers_have_a_rule():
    """The cooler display and the Aura board each sit on a root-only hidraw
    node; the card needs none, riding I2C adapters that already carry uaccess."""
    reference = reference_rules()
    assert len(reference) == 2, f"expected the cooler + Aura rules, got {len(reference)}"
    by_product = {re.search(r'idProduct\}=="([0-9a-f]+)"', line).group(1): line
                  for line in reference}
    assert set(by_product) == {"2007", "19af"}
    for rule in by_product.values():
        assert 'TAG+="uaccess"' in rule
        assert 'RUN{builtin}+="uaccess"' in rule, "the tag alone is set too late to be acted on"
        assert 'MODE="0666"' not in rule and 'MODE="0777"' not in rule


def test_no_privileged_step_reads_the_checkout():
    """The reviewed commit cannot vouch for a file root reads out of a
    user-writable directory at some later elevation time."""
    readme = (REPO / "README.md").read_text()
    assert "sudo tee" in readme  # the inline printf pipe is the only sudo write
    assert not re.search(r"sudo\s+(cp|install)\b", readme)


def test_no_installer_narrative_remains_in_docs():
    """install.sh, venv installs, and systemd units survive only in history
    notes — never as something the user is told to run."""
    for relative in ("README.md", "CLAUDE.md", "docs/hardware-notes.md"):
        text = (REPO / relative).read_text()
        history_stripped = re.sub(r"^>.*$", "", text, flags=re.M)
        assert "install.sh" not in history_stripped, relative
        assert "systemctl" not in history_stripped, relative
        assert "mimarchy-light.service" not in history_stripped, relative
        assert "mimarchy-display.service" not in history_stripped, relative


def test_readme_install_is_one_command():
    """Plugin-add is the entire install; the only sudo block is the udev one."""
    readme = (REPO / "README.md").read_text()
    assert "omarchy plugin add https://github.com/villenull/mimarchy --enable" in readme
    assert readme.count("sudo") >= 3  # tee + reload + trigger in the udev block
    assert "virtualenv" not in readme.lower()


def test_bin_launchers_exist_executable_and_compile_clean():
    """The panel spawns these from the checkout, so they must be real files
    (the plugin validator forbids symlinks), executable, with the system
    shebang — and parse as valid Python."""
    for name in LAUNCHERS:
        path = REPO / "bin" / name
        assert path.is_file() and not path.is_symlink(), name
        assert path.stat().st_mode & stat.S_IXUSR, f"{name} is not executable"
        first = path.read_text().splitlines()[0]
        assert first == "#!/usr/bin/python3", f"{name}: {first!r}"
        py_compile.compile(str(path), doraise=True)


def test_hook_resolves_bin_relatively():
    """The theme hook runs from ~/.config as a symlink or copy, so it must
    locate the launcher from its own real path — never via $HOME or PATH."""
    hook = (REPO / "omarchy" / "theme-set.d" / "mimarchy").read_text()
    code = "\n".join(line for line in hook.splitlines() if not line.startswith("#"))
    assert "$HOME" not in code and "~/" not in code  # self-locating, not home-based
    assert "realpath" in hook
    assert "../../bin/mimarchy-ctl" in hook
    assert "/usr/bin/python3" in hook
    assert "timeout 5s" in hook  # still time-limited inside the theme switch
    assert "&" in hook  # still backgrounded so theme changes feel instant


def test_no_openrgb_reference_remains():
    """OpenRGB as a runtime is gone entirely: nothing the install acts on —
    launchers, hook, udev rule, README instructions, CLAUDE.md hazards —
    spawns, orders, or depends on it. Protocol ports in src/ credit OpenRGB
    as the reverse-engineering source, and docs/ keeps the history note; both
    are records, not references the install acts on, so they live outside
    this test's reach."""
    for relative in ("README.md", "CLAUDE.md"):
        body = re.sub(r"^>.*$", "", (REPO / relative).read_text(), flags=re.M)
        assert "penRGB" not in body and "penrgb" not in body and "6742" not in body, relative
    for path in list((REPO / "bin").iterdir()) + [
        REPO / "omarchy" / "theme-set.d" / "mimarchy",
        REPO / "udev" / "99-mimarchy.rules",
    ]:
        if path.is_file():
            text = path.read_text(errors="replace")
            assert "penRGB" not in text and "penrgb" not in text, str(path)
    assert not (REPO / "src" / "mimarchy" / "detectors.py").exists()
    assert not (REPO / "systemd").exists()


def test_versions_move_together():
    """manifest.json and pyproject.toml carry one version for the plugin and
    the backend it shells out to (mirrors test_manifest's equality check)."""
    manifest = json.loads((REPO / "manifest.json").read_text())
    pyproject = (REPO / "pyproject.toml").read_text()
    version = re.search(r'^version = "([^"]+)"', pyproject, re.M).group(1)
    assert manifest["version"] == version == "0.6.0"
