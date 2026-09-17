"""`mimarchy-setup` — point Mimarchy at whatever LEDs this machine actually has.

`config.toml` accepts arbitrary hardware: each zone names a controller channel,
so a different board and a different card are only ever a text edit away. The
problem is finding out what to type. This probes the two controllers directly,
prints what is there, asks which zones to drive and how long each strip is,
and writes the file.

Two things it deliberately does *not* do:

* **Touch LEDs.** It reads the Aura config table and probes the Nitro address
  for presence. No mode is set, no colour is sent — running it while the
  lighting daemon is animating is safe and changes nothing until the daemon
  next reloads.
* **Ask about the cooler display.** That driver is specific to one USB panel
  (`5131:2007`) and there is nothing to discover: either the device is present
  or it is not.

    mimarchy-setup --list      # what the controllers report, changes nothing
    mimarchy-setup             # the wizard
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from mimarchy import aura as aura_mod
from mimarchy import hidraw
from mimarchy import nitro as nitro_mod
from mimarchy import smbus
from mimarchy.config import CONFIG_PATH, Config, load_config
from mimarchy.rgb import RGBError

#: Names that make the CPU/GPU link work without the user knowing it exists.
#: Linking joins every configured zone (see `lightd._source_target`), so it works
#: regardless of naming — but the shared state is keyed off the first zone in
#: config order, and `cpu_fans` is the name that puts the motherboard header
#: there. Suggesting these two names for the devices they describe is the
#: difference between `u` in the panel doing something recognisable and doing
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


def describe_aura(board) -> DetectedDevice | None:
    """The board's channels as one device, or None when no board answered."""
    if board is None:
        return None
    return DetectedDevice(
        index=0,
        name="ASUS Aura USB",
        kind="motherboard",
        zones=[
            DetectedZone(index=ch.index, name=ch.name,
                         leds=0 if ch.addressable else 1,
                         addressable=ch.addressable)
            for ch in board.channels
        ],
    )


def describe_nitro(present: bool) -> DetectedDevice | None:
    """The card's bar as one fixed zone, or None when nothing answered."""
    if not present:
        return None
    return DetectedDevice(
        index=0,
        name="Sapphire Nitro Glow",
        kind="gpu",
        zones=[DetectedZone(index=0, name="GPU Bar", leds=1,
                            addressable=False)],
    )


def probe() -> list[DetectedDevice]:
    """Every controller that answered, without touching any LEDs.

    The Aura config-table read and the single-byte Nitro presence probe are
    reads only; neither changes a mode nor sends a colour.
    """
    try:
        board = aura_mod.AuraBoard.open()
    except hidraw.HidError:
        board = None
    try:
        aura_dev = describe_aura(board) if board is not None else None
    finally:
        if board is not None:
            try:
                board.close()
            except Exception:  # noqa: BLE001 - close is best-effort
                pass
    present = nitro_mod.find() is not None
    devices = [d for d in (aura_dev, describe_nitro(present)) if d is not None]
    for i, device in enumerate(devices):
        device.index = i
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
        lines.append("An addressable zone showing 0 LEDs is normal: a strip shows "
                     "whatever frames")
        lines.append("arrive, so its length comes from your config. That is what "
                     "the wizard asks you for.")
    return "\n".join(lines).rstrip() + "\n"


#: Printed when no controller answered. The two causes are permissions and
#: cabling/power, not software: the Aura hidraw node is root-only until the
#: udev rule lands, and a card whose microcontroller has dropped off its I2C
#: bus needs a cold boot rather than any command.
_NO_DEVICES = """\
No lighting controllers answered.

Two things to check, in this order:

* Permissions — the Aura board speaks over a hidraw node that is root-only
  until the udev rule is installed:

    sudo cp udev/99-mimarchy.rules /etc/udev/rules.d/
    sudo udevadm control --reload-rules && sudo udevadm trigger

  Then unplug and re-plug the board's USB, or reboot.

* Power — if the card's bar went dark suddenly, its microcontroller has
  likely dropped off the I2C bus. A cold boot (power off at the PSU,
  wait ~30 s) resets it.
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
            # one-LED card zone invites an answer that cannot be honoured.
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


def render_config(selections: list[Selection],
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
        "# below. A strip shows whatever frames arrive, so its length is the",
        "# config's job, and a zero-length zone is skipped.",
        "#",
        "# Too short leaves the tail of the strip dark; too long is worse than it",
        "# sounds, because spatial effects span the *zone* — at 60 on a 15-LED "
        "strip,",
        "# rainbow shows a quarter of the hue wheel and looks like spectrum.",
        f"zone_size = {DEFAULT_LEDS}",
        "",
    ]

    lines += [
        "# The zones to drive. `device` names the controller for a person "
        "reading this",
        "# file; `zone` is the channel index within it — `mimarchy-setup --list`",
        "# prints both. `leds` overrides `zone_size` for one zone.",
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
        "# `cpu_fans` and `gpu` move together by default; `u` in the panel splits",
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

    Same timestamped-backup convention as ever, for the same reason: this file
    is documented as hand-editable, so overwriting it without a copy would
    throw away comments and values someone deliberately put there.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if path.exists():
        backup = path.with_suffix(f".toml.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(path, backup)
    path.write_text(text)
    return backup


# ---- commands -------------------------------------------------------------


def cmd_list(args: argparse.Namespace) -> int:
    print(format_listing(probe()), end="")
    return 0


def cmd_setup(args: argparse.Namespace, ask) -> int:
    devices = probe()
    print(format_listing(devices), end="")
    if not devices:
        return 1

    print()
    selections = prompt_zones(devices, ask)
    if not selections:
        print("No zones chosen — config.toml left alone.")
        return 1

    text = render_config(selections, existing=_existing(args.config))
    backup = write_config(text, args.config)

    print(f"\nwrote {args.config}")
    if backup:
        print(f"previous version kept at {backup}")
    for selection in selections:
        print(f"  {selection.key}: {selection.device!r} zone {selection.zone}"
              + (f", {selection.leds} LEDs" if selection.leds else ""))

    print("\nWrote the config. The panel starts the lighting daemon itself, so")
    print("it picks the new zones up on its next status poll; a headless")
    print("mimarchy-lightd needs a restart to re-read the file.")
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
    return parser


def main(argv: list[str] | None = None, ask=input) -> int:
    args = build_parser().parse_args(argv)

    if not args.list and ask is input and not sys.stdin.isatty():
        print("mimarchy-setup needs a terminal to ask questions. "
              "Use --list for a listing.", file=sys.stderr)
        return 2

    try:
        return cmd_list(args) if args.list else cmd_setup(args, ask)
    except (RGBError, hidraw.HidError, smbus.I2cError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Abort as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
