"""Checks on `manifest.json`, standing in for `omarchy plugin validate`.

The real validator runs inside Omarchy and is not available here, so these
replicate the checks it documents: schema version, required fields, an id
outside the reserved namespace, entry points that are safe relative paths and
actually exist, an entry point for every declared kind, and no symlinks
anywhere in the plugin directory.

Worth having as tests rather than as a one-time manual check, because the
manifest is the thing the marketplace validates on submission and re-validates
on every update — and installs track this repo's default branch, so a manifest
broken on `main` is broken for everyone who runs `omarchy plugin update`. A
renamed QML file with a stale entry point would otherwise be invisible until a
stranger's shell failed to load it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO / "manifest.json"

#: Kinds the shell understands, and the entryPoints key each one requires.
KIND_ENTRY_POINTS = {
    "bar-widget": "barWidget",
    "panel": "panel",
    "overlay": "overlay",
    "menu": "menu",
    "service": "service",
    "bar": "bar",
}

#: Categories the shipped first-party manifests use. Not enforced by the
#: validator, but drifting outside the set puts the widget in a group of one in
#: the bar's own picker.
KNOWN_CATEGORIES = {
    "Compositor", "System", "Network", "Status", "Audio",
    "Time", "Media", "Layout", "Info", "Files", "AI",
}


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text())


def test_manifest_is_valid_json_at_the_repo_root():
    """`omarchy plugin add` looks here and nowhere else."""
    assert MANIFEST_PATH.is_file()
    json.loads(MANIFEST_PATH.read_text())


def test_schema_version_is_the_one_the_shell_speaks(manifest):
    assert manifest["schemaVersion"] == 1


@pytest.mark.parametrize("field", ["id", "name", "version", "kinds", "entryPoints"])
def test_required_fields_are_present_and_non_empty(manifest, field):
    assert manifest.get(field)


def test_id_is_outside_the_reserved_namespace(manifest):
    """`omarchy.*` is reserved; a third-party plugin claiming it is refused."""
    assert not manifest["id"].startswith("omarchy.")


def test_id_follows_the_recommended_namespacing(manifest):
    """Ids are permanent and global — a collision cannot be fixed later."""
    assert manifest["id"] == "io.github.villenull.mimarchy"


def test_every_declared_kind_is_known(manifest):
    for kind in manifest["kinds"]:
        assert kind in KIND_ENTRY_POINTS, kind


def test_every_declared_kind_has_an_entry_point(manifest):
    for kind in manifest["kinds"]:
        assert KIND_ENTRY_POINTS[kind] in manifest["entryPoints"], kind


def test_entry_points_are_safe_relative_paths_that_exist(manifest):
    for key, relative in manifest["entryPoints"].items():
        path = Path(relative)
        assert not path.is_absolute(), key
        assert ".." not in path.parts, key
        assert (REPO / path).is_file(), f"{key} -> {relative}"


def test_no_symlinks_anywhere_in_the_plugin():
    """The validator rejects symlinks outright, and prunes only `.git`.

    Mirrors `omarchy-plugin-validate` exactly:

        find "$PLUGIN_DIR" -name .git -prune -o -type l -print -quit

    Nothing else is excluded here, deliberately. A virtualenv contains four
    symlinks (`bin/python`, `lib64`, ...; even `--copies` leaves `lib64`), so a
    `.venv` inside this checkout would fail the real validator — and because
    `omarchy plugin update` re-validates and rolls back, it would quietly make
    the plugin un-updatable for anyone who installed it this way. That is why
    `install.sh` puts the venv in ~/.local/share/mimarchy instead. Excluding
    `.venv` here would hide exactly the mistake this test exists to catch.
    """
    for path in REPO.rglob("*"):
        if ".git" in path.parts:
            continue
        assert not path.is_symlink(), (
            f"{path.relative_to(REPO)} is a symlink; "
            "omarchy plugin validate refuses these anywhere but .git"
        )


def test_bar_widget_block_is_present_and_coherent(manifest):
    block = manifest["barWidget"]

    assert block["displayName"]
    assert block["category"] in KNOWN_CATEGORIES
    assert block["defaultSection"] in {"left", "center", "right"}
    assert block["allowMultiple"] is False   # one lighting widget is enough


def test_every_settings_key_has_a_matching_default(manifest):
    """A schema entry with no default is a control with nothing behind it."""
    block = manifest["barWidget"]
    defaults = block["defaults"]

    for entry in block["schema"]:
        assert entry["key"] in defaults, entry["key"]
        assert entry["defaultValue"] == defaults[entry["key"]], entry["key"]


def test_settings_the_qml_reads_are_all_declared(manifest):
    """The QML's `setting(...)` calls and the manifest must not drift apart."""
    qml = (REPO / manifest["entryPoints"]["barWidget"]).read_text()
    read = set(re.findall(r'setting\(\s*"([^"]+)"', qml))

    assert read <= set(manifest["barWidget"]["defaults"]), read


def test_version_matches_the_python_package(manifest):
    """One version for the plugin and the backend it shells out to."""
    pyproject = (REPO / "pyproject.toml").read_text()
    version = re.search(r'^version = "([^"]+)"', pyproject, re.M).group(1)

    assert manifest["version"] == version
