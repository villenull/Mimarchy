"""`mimarchy-setup` — point Mimarchy at whatever LEDs this machine actually has.

`config.toml` has always accepted arbitrary hardware: `device` is a substring of
the OpenRGB device name and `zone` is an index into it, so a different board and
a different card were only ever a text edit away. The problem was finding out
what to type. `openrgb --list-devices` prints the names, the comments in the
config explain the fields, and between those two the user is expected to work out
that an addressable zone reporting `leds=0` is normal, that the number they want
is the physical length of their strip, and which of five zones on a motherboard
is the header their cooler is plugged into. That is a lot of reading before
anything lights up, and it is the step that turns "works on my rig" into "works
on yours".

So this connects to the same SDK server the daemon uses, prints what is there,
asks which zones to drive and how long each strip is, and writes the file.

Three things it deliberately does *not* do:

* **Touch hardware.** It reads the device list and writes a text file. Nothing
  is resized, no mode is set, no colour is sent — running it while the lighting
  daemon is animating is safe and changes nothing until the daemon next reloads.
* **Restrict detectors.** It works out which OpenRGB detectors the selected
  devices need and writes them into the config, but narrowing the list is
  `tools/restrict-openrgb-detectors.py`'s job and stays a separate, explicit
  step — see `mimarchy.detectors` for why that separation matters.
* **Ask about the cooler display.** That driver is specific to one USB panel
  (`5131:2007`) and there is nothing to discover: either the device is present
  or it is not.

    mimarchy-setup --list      # what OpenRGB sees, changes nothing
    mimarchy-setup             # the wizard
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from openrgb.utils import ZoneType

from mimarchy import detectors as detectors_mod
from mimarchy.config import CONFIG_PATH, Config, load_config
from mimarchy.rgb import RGBError, connect

#: Names that make the CPU/GPU link work without the user knowing it exists.
#: Linking joins every configured zone (see `lightd._source_target`), so it works
#: regardless of naming — but the shared state is keyed off the first zone in
#: config order, and `cpu_fans` is the name that puts the motherboard header
#: there. Suggesting these two names for the devices they describe is the
#: difference between `u` in the TUI doing something recognisable and doing
#: something arbitrary.
SUGGESTED_KEYS = {
    "motherboard": "cpu_fans",
    "gpu": "gpu",
    "cooler": "cooler",
    "dram": "ram",
    "ledstrip": "strip",
    "case": "case",
}

#: What a strip length defaults to when the user just presses return. Same as
#: `[rgb] zone_size`, and wrong for most people — which is why it is asked
#: rather than assumed, and why the prompt says what the number is for.
DEFAULT_LEDS = 15


@dataclass
class DetectedZone:
    index: int
    name: str
    leds: int
    addressable: bool


@dataclass
class DetectedDevice:
    index: int
    name: str
    kind: str
    zones: list[DetectedZone] = field(default_factory=list)


@dataclass
class Selection:
    """One line of the answer: a config key pointing at one device zone."""

    key: str
    device: str
    zone: int
    leds: int | None = None


class Abort(RuntimeError):
    """The user pressed ctrl-d or ctrl-c, or answered something impossible."""


# ---- reading what is there ------------------------------------------------


def _addressable(device, index: int, zone) -> bool:
    """Whether this zone's length is ours to set.

    `leds_min != leds_max` is the authority — it is what OpenRGB itself checks
    before honouring a resize — but the SDK wrapper only carries it on the raw
    `ControllerData`, not on the `Zone` object, and older servers do not send it
    at all. The zone type is the fallback: SINGLE is a fixed lamp, LINEAR and
    MATRIX are strips and panels.
    """
    zone_data = getattr(getattr(device, "data", None), "zones", None)
    if zone_data is not None and index < len(zone_data):
        low = getattr(zone_data[index], "leds_min", None)
        high = getattr(zone_data[index], "leds_max", None)
        if low is not None and high is not None:
            return low != high
    return getattr(zone, "type", ZoneType.SINGLE) != ZoneType.SINGLE


def describe(client) -> list[DetectedDevice]:
    """The device tree, flattened into something printable and testable."""
    devices = []
    for index, device in enumerate(client.devices):
        kind = getattr(getattr(device, "type", None), "name", "") or "unknown"
        devices.append(DetectedDevice(
            index=index,
            name=device.name,
            kind=kind.lower(),
            zones=[
                DetectedZone(index=z_index, name=zone.name,
                             leds=len(zone.leds),
                             addressable=_addressable(device, z_index, zone))
                for z_index, zone in enumerate(device.zones)
            ],
        ))
    return devices


def format_listing(devices: list[DetectedDevice]) -> str:
    """The `--list` output, and the menu the wizard picks from.

    One block per device with `device.zone` coordinates spelled out, because
    those coordinates are what the wizard asks for — a listing that has to be
    mentally re-indexed to answer the next question is a listing that gets
    misread.
    """
    if not devices:
        return _NO_DEVICES

    lines = [f"{len(devices)} device(s):", ""]
    for device in devices:
        lines.append(f"  [{device.index}] {device.name}  ({device.kind})")
        for zone in device.zones:
            shape = "addressable" if zone.addressable else "fixed"
            plural = "" if zone.leds == 1 else "s"
            lines.append(f"        {device.index}.{zone.index}  "
                         f"{zone.name!r}  {shape}, {zone.leds} LED{plural}")
        lines.append("")

    if any(z.addressable and z.leds == 0 for d in devices for z in d.zones):
        lines.append("An addressable zone showing 0 LEDs is normal: OpenRGB has "
                     "no way to know how")
        lines.append("long a strip is until it is told. That is what the wizard "
                     "asks you for.")
    return "\n".join(lines).rstrip() + "\n"


#: Printed when the server answers but reports nothing. Almost always the
#: detector list rather than the cabling: `install.sh` narrows it *before* the
#: server first starts, because an unrestricted probe is a boot-time freeze on
#: some cards (OpenRGB #4888) — which leaves a machine whose hardware was never
#: in that narrow set seeing no devices at all. So the fix is spelled out here
#: rather than left as "check your connections".
_NO_DEVICES = """\
OpenRGB is running but reports no devices.

If Mimarchy has just been installed, this is expected rather than broken: the
detector list was narrowed before the OpenRGB server was first started, and
your hardware is not in the set it was narrowed to. Widening it once is how you
find out what you have:

    tools/restrict-openrgb-detectors.py --discover
    systemctl --user restart openrgb.service
    mimarchy-setup
    tools/restrict-openrgb-detectors.py
    systemctl --user restart openrgb.service

Read what `--discover` prints before running it — a full probe is the thing
that can hang a machine, and that is the reason for this dance.
"""


# ---- the wizard -----------------------------------------------------------


def suggest_key(device: DetectedDevice, taken: set[str]) -> str:
    """A config key for this device that is likely to be the one wanted."""
    base = SUGGESTED_KEYS.get(device.kind, device.kind or "zone")
    if base not in taken:
        return base
    for suffix in range(2, 100):
        if f"{base}{suffix}" not in taken:
            return f"{base}{suffix}"
    return base


def _lookup(devices: list[DetectedDevice], answer: str) -> tuple[DetectedDevice,
                                                                DetectedZone]:
    device_part, _, zone_part = answer.partition(".")
    try:
        device_index, zone_index = int(device_part), int(zone_part)
        # Negative indices are valid Python and nonsense here: without this,
        # typing "-1" quietly selects the last zone of the last device.
        if device_index < 0 or zone_index < 0:
            raise IndexError(answer)
        device = devices[device_index]
        zone = device.zones[zone_index]
    except (IndexError, ValueError) as exc:
        raise ValueError(f"no such zone: {answer!r}") from exc
    return device, zone


def prompt_zones(devices: list[DetectedDevice], ask, out=print) -> list[Selection]:
    """Ask which zones to drive, until the user says that is all of them."""
    selections: list[Selection] = []
    out("Which zones should Mimarchy drive? Enter them as device.zone — 0.1 is "
        "zone 1 of device 0.")
    out("Press return on an empty line when you are done.\n")

    while True:
        answer = _ask(ask, "  zone (blank to finish): ")
        if not answer:
            return selections
        try:
            device, zone = _lookup(devices, answer)
        except ValueError as exc:
            out(f"    {exc}")
            continue
        if any(s.device == device.name and s.zone == zone.index
               for s in selections):
            out("    already added")
            continue

        suggested = suggest_key(device, {s.key for s in selections})
        key = _ask(ask, f"  name for it [{suggested}]: ") or suggested
        if key in {s.key for s in selections}:
            out(f"    {key!r} is already used — skipping")
            continue

        leds = None
        if zone.addressable:
            # Only for zones whose length is ours to set. Asking about a
            # one-LED GPU zone invites an answer that cannot be honoured, and a
            # rejected resize is silent.
            leds = _ask_int(ask, out,
                            f"  how many LEDs on this strip [{DEFAULT_LEDS}]: ",
                            DEFAULT_LEDS)

        selections.append(Selection(key=key, device=device.name,
                                    zone=zone.index, leds=leds))
        out(f"    {key} -> {device.name!r} zone {zone.index}\n")


def _ask(ask, prompt: str) -> str:
    try:
        return ask(prompt).strip()
    except (EOFError, KeyboardInterrupt) as exc:
        raise Abort("cancelled — nothing was written") from exc


def _ask_int(ask, out, prompt: str, default: int) -> int:
    while True:
        answer = _ask(ask, prompt)
        if not answer:
            return default
        try:
            value = int(answer)
        except ValueError:
            out("    that needs to be a number")
            continue
        if value < 1:
            out("    a zone needs at least one LED")
            continue
        return value


# ---- writing the file -----------------------------------------------------


def _toml_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_config(selections: list[Selection], detector_names: list[str],
                  existing: Config | None = None) -> str:
    """The whole `config.toml`, comments and all.

    The comments are written out again rather than transplanted from whatever
    was there before. Round-tripping a hand-edited TOML file through a parser
    and a writer loses every comment in it, and no stdlib writer preserves them
    — so the explanations that ship with the file are restated here, and
    anything a user added by hand is preserved by keeping the old file as a
    timestamped backup rather than by trying to parse it back out.

    Values the wizard has no opinion about — the display's USB ids, the link
    toggle — are carried across from `existing` so a re-run does not quietly
    reset choices made elsewhere.
    """
    display = existing.display if existing else None
    vendor = display.vendor_id if display and display.known else 0x5131
    product = display.product_id if display and display.known else 0x2007
    linked = existing.link_cpu_gpu if existing else True

    lines = [
        "# Mimarchy config, written by `mimarchy-setup`.",
        "#",
        "# Hand-editable from here on: re-running the wizard rewrites the file "
        "(keeping a",
        "# timestamped .bak), but nothing else does.",
        "",
        "[rgb]",
        "# Default length for addressable zones that do not set their own `leds`",
        "# below. Addressable zones report leds=0 until told, and a zero-length "
        "zone",
        "# silently swallows colour writes.",
        "#",
        "# Too short leaves the tail of the strip dark; too long is worse than it",
        "# sounds, because spatial effects span the *zone* — at 60 on a 15-LED "
        "strip,",
        "# rainbow shows a quarter of the hue wheel and looks like spectrum.",
        f"zone_size = {DEFAULT_LEDS}",
        "",
    ]

    lines += [
        "# The OpenRGB detectors these devices need, and the only ones",
        "# `tools/restrict-openrgb-detectors.py` will enable. Everything else "
        "stays off:",
        "# OpenRGB's broad GPU/I2C probing is a documented total-system freeze",
        "# (OpenRGB #4888) and its server starts at login, so a wide list is a "
        "freeze",
        "# on every boot rather than a one-off.",
    ]
    if detector_names:
        lines.append("detectors = [")
        lines += [f"    {_toml_string(name)}," for name in detector_names]
        lines.append("]")
    else:
        # An empty list is not the same as an absent key, and the difference
        # matters: absent means "work it out from the device names", empty means
        # "we tried and could not", which is a question for a person.
        lines += [
            "#",
            "# Nothing here: the device names below did not resemble any "
            "detector OpenRGB",
            "# knows. Run `tools/restrict-openrgb-detectors.py --check` to see "
            "the names,",
            "# then list the ones matching your hardware here.",
            "detectors = []",
        ]
    lines.append("")

    lines += [
        "# The zones to drive. `device` is matched as a case-insensitive "
        "substring of",
        "# the OpenRGB device name, so it survives minor naming changes; `zone` "
        "is the",
        "# index within that device. `leds` overrides `zone_size` for one zone.",
        "#",
        "# Add as many as you have — the daemon renders every zone listed here.",
    ]
    for selection in selections:
        lines.append(f"[rgb.zones.{selection.key}]")
        lines.append(f"device = {_toml_string(selection.device)}")
        lines.append(f"zone = {selection.zone}")
        if selection.leds is not None:
            lines.append(f"leds = {selection.leds}")
        lines.append("")

    lines += [
        "[ui]",
        "# `cpu_fans` and `gpu` move together by default; `u` in the TUI splits",
        "# them so each can run its own mode. Any other zone is always "
        "independent.",
        f"link_cpu_gpu = {str(linked).lower()}",
        "",
        "[display]",
        "# CPU cooler display controller. usb.ids mislabels this as an "
        '"MSR-101U magnetic',
        "# card reader\" — the ID is cloned and also used by USB relay boards. "
        "Needs",
        "# udev/99-mimarchy.rules installed, or its hidraw node is root-only.",
        "#",
        "# Harmless to leave here if you have no such panel: the display "
        "service simply",
        "# finds nothing. The lighting does not depend on it.",
        f"vendor_id = 0x{vendor:04x}",
        f"product_id = 0x{product:04x}",
        "",
    ]
    return "\n".join(lines)


def write_config(text: str, path: Path) -> Path | None:
    """Write the config, keeping any previous version. Returns the backup path.

    Same timestamped-backup convention as the detector tool, for the same
    reason: this file is documented as hand-editable, so overwriting it without
    a copy would throw away comments and values someone deliberately put there.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if path.exists():
        backup = path.with_suffix(f".toml.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(path, backup)
    path.write_text(text)
    return backup


# ---- commands -------------------------------------------------------------


def _detectors_for(selections: list[Selection], openrgb_config: Path,
                   out) -> list[str]:
    """Which detectors the selected devices need, or nothing if unknowable.

    Best effort by design. A missing or unreadable OpenRGB config is not a
    reason to refuse to write a lighting config — the zones are the part the
    user came for, and the detector list has its own tool with its own
    `--check`.
    """
    try:
        known = detectors_mod.read_detector_names(openrgb_config)
    except detectors_mod.DetectorConfigError as exc:
        out(f"note: {exc}")
        out("      the detector allowlist was left out; run "
            "tools/restrict-openrgb-detectors.py later.")
        return []

    names: set[str] = set()
    for match in detectors_mod.resolve([s.device for s in selections], known):
        if match.note:
            out(f"note: {match.device!r} — {match.note}")
        names |= match.detectors
    return sorted(names)


def cmd_list(args: argparse.Namespace) -> int:
    client = connect(args.host, args.port, name="mimarchy-setup")
    print(format_listing(describe(client)), end="")
    return 0


def cmd_setup(args: argparse.Namespace, ask) -> int:
    client = connect(args.host, args.port, name="mimarchy-setup")
    devices = describe(client)
    print(format_listing(devices), end="")
    if not devices:
        return 1

    print()
    selections = prompt_zones(devices, ask)
    if not selections:
        print("No zones chosen — config.toml left alone.")
        return 1

    detector_names = _detectors_for(selections, args.openrgb_config, print)
    text = render_config(selections, detector_names,
                         existing=_existing(args.config))
    backup = write_config(text, args.config)

    print(f"\nwrote {args.config}")
    if backup:
        print(f"previous version kept at {backup}")
    for selection in selections:
        print(f"  {selection.key}: {selection.device!r} zone {selection.zone}"
              + (f", {selection.leds} LEDs" if selection.leds else ""))

    print("\nNext, narrow OpenRGB's detector list to what you just picked and "
          "restart it:")
    print("    tools/restrict-openrgb-detectors.py")
    print("    systemctl --user restart openrgb.service "
          "mimarchy-light.service")
    return 0


def _existing(path: Path) -> Config | None:
    """The current config, or None. Never creates one.

    `load_config` writes the default file when it finds none, which is right for
    a daemon starting up and wrong here — it would mean the wizard's first act
    was to install the reference rig's settings.
    """
    if not path.exists():
        return None
    try:
        return load_config(path)
    except (OSError, ValueError) as exc:
        print(f"note: {path} could not be read ({exc}); "
              "its display and link settings will not be carried over")
        return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mimarchy-setup",
        description="Find your LED zones and write Mimarchy's config for them.",
    )
    parser.add_argument("--list", action="store_true",
                        help="print the detected devices and zones, and exit")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH,
                        help=f"config file to write (default: {CONFIG_PATH})")
    parser.add_argument("--openrgb-config", type=Path,
                        default=detectors_mod.DEFAULT_OPENRGB_CONFIG,
                        help="OpenRGB's config, read for its detector names")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6742)
    return parser


def main(argv: list[str] | None = None, ask=input) -> int:
    args = build_parser().parse_args(argv)

    if not args.list and ask is input and not sys.stdin.isatty():
        print("mimarchy-setup needs a terminal to ask questions. "
              "Use --list for a listing.", file=sys.stderr)
        return 2

    try:
        return cmd_list(args) if args.list else cmd_setup(args, ask)
    except RGBError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Abort as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
