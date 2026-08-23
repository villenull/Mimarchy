"""`mimarchy-ctl` — the non-interactive half of the TUI.

Exists because the Omarchy 4 bar widget needs to read and change lighting state,
and QML is the wrong place to do either. Quickshell plugins run unsandboxed
inside the user's long-lived shell process, so anything with logic in it is
better off in a subprocess that can be tested and that cannot take the desktop
down with it. The widget therefore shells out to this for everything: one
`status --json` to paint itself, one command per interaction.

It writes through `lightstate`, which is the same atomic write-then-rename the
TUI uses, so a bar click and a keypress cannot interleave into a half-written
file. Nothing here talks to hardware — `mimarchy-lightd` still owns that, and
still renders both controllers from one clock. This only ever moves the desired
state that the daemon reads.

Useful on its own terms as well, which is why it is a real command rather than a
private helper: Hyprland keybindings, shell scripts, and the Omarchy menu can
all drive the lighting without opening a window.

    mimarchy-ctl status --json
    mimarchy-ctl speed +
    mimarchy-ctl effect rainbow
    mimarchy-ctl display toggle
"""

from __future__ import annotations

import argparse
import json
import sys

from mimarchy import lightstate
from mimarchy.config import load_config
from mimarchy.effects import COLOUR_EFFECTS, EFFECTS, SPEED_LEVELS, nearest_speed
from mimarchy.hwmon import (read_cpu_fan_rpm, read_cpu_temp, read_gpu_temp,
                            snapshot)
from mimarchy.service import DISPLAY_UNIT, LIGHT_UNIT, set_unit, unit_active
from mimarchy.theme import LED_ROLES, led_colour

#: Effects that ignore the speed ladder entirely. Asking for a speed change on
#: one of these is not an error — it is a no-op with an explanation, because the
#: bar widget's scroll wheel does not know what effect is running when it turns.
STATIC_EFFECTS = frozenset({"static", "off"})


def _targets(state: lightstate.LightingState) -> list[str]:
    """Which target keys a command should act on.

    Linked is not "both targets happen to match" — it means the pair is driven
    as one, so a command edits every target rather than a chosen one. Unlinked,
    the same command still edits every target, because the bar has no notion of
    a selected row; picking one is the TUI's job.
    """
    keys = list(state.targets)
    if keys:
        return keys
    # A state file that has never been written has no targets yet. Seed from the
    # configured zones so the first command from the bar does something, rather
    # than silently editing an empty dict.
    try:
        return list(load_config().zones) or ["cpu_fans", "gpu"]
    except (OSError, ValueError, KeyError):
        # `load_config` now skips a malformed zone table rather than raising, so
        # KeyError should no longer reach here — kept because this runs on every
        # bar-widget poll, and the cost of an extra exception class in the tuple
        # is nothing against a traceback in the user's shell process.
        return ["cpu_fans", "gpu"]


def _selected_targets(state: lightstate.LightingState,
                      zone: str | None) -> list[str] | None:
    """The target keys a `--zone`-aware command should act on.

    No `--zone` means every target, which is the meaning the bar icon's wheel
    and middle-click already rely on, and it must stay that way: this is just
    `_targets(state)` unchanged. A given `--zone` narrows to that one key, but
    only after checking it is a real target. `LightingState.for_target` is a
    bare `setdefault` with no validation, so handing a typo straight to it
    would silently create and persist a phantom zone that nothing renders;
    returning None here lets the caller report the error instead.
    """
    targets = _targets(state)
    if zone is None:
        return targets
    if zone not in targets:
        print(f"unknown zone: {zone}", file=sys.stderr)
        print(f"try one of: {', '.join(targets)}", file=sys.stderr)
        return None
    return [zone]


def _speed_label(speed: float) -> int:
    """The speed as a 1-based stop number, which is what a person reads.

    The stored value is a float on a five-stop ladder; "3/5" is legible in a bar
    tooltip in a way that "0.6" is not.
    """
    return SPEED_LEVELS.index(nearest_speed(speed)) + 1


def cmd_status(args: argparse.Namespace) -> int:
    state = lightstate.load()
    targets = {}
    for key in _targets(state):
        target = state.for_target(key)
        targets[key] = {
            "effect": target.effect,
            "colour": list(target.colour),
            "speed": target.speed,
            "speed_stop": _speed_label(target.speed),
            "colour_role": target.colour_role,
            "follows_theme": bool(target.colour_role),
            "takes_colour": target.effect in COLOUR_EFFECTS,
            "takes_speed": target.effect not in STATIC_EFFECTS,
        }

    # One `sensors -j` for all three readings. Called bare they would spawn it
    # three times, and the bar widget runs this every two seconds while its
    # panel is open — from inside the user's long-lived shell process.
    sensors = snapshot()

    payload = {
        "linked": state.linked,
        "targets": targets,
        "lighting_active": unit_active(LIGHT_UNIT),
        "display_active": unit_active(DISPLAY_UNIT),
        "speed_stops": len(SPEED_LEVELS),
        "sensors": {
            "cpu_temp": read_cpu_temp(sensors),
            "gpu_temp": read_gpu_temp(sensors),
            "cpu_fan_rpm": read_cpu_fan_rpm(sensors),
        },
    }

    if args.json:
        json.dump(payload, sys.stdout)
        sys.stdout.write("\n")
        return 0

    print(_human_status(payload))
    return 0


def _human_status(payload: dict) -> str:
    lines = []
    for key, target in payload["targets"].items():
        speed = (f"  speed {target['speed_stop']}/{payload['speed_stops']}"
                 if target["takes_speed"] else "")
        lines.append(f"{key}: {target['effect']}{speed}")
    lines.append("linked" if payload["linked"] else "unlinked")
    lines.append(f"lighting: {'running' if payload['lighting_active'] else 'stopped'}")
    lines.append(f"display:  {'on' if payload['display_active'] else 'off'}")

    sensors = payload["sensors"]
    readings = [
        ("cpu", sensors["cpu_temp"], "°C"),
        ("gpu", sensors["gpu_temp"], "°C"),
        ("fan", sensors["cpu_fan_rpm"], "rpm"),
    ]
    lines.append("  ".join(f"{name} {value:.0f}{unit}"
                           for name, value, unit in readings if value is not None))
    return "\n".join(line for line in lines if line)


def cmd_speed(args: argparse.Namespace) -> int:
    state = lightstate.load()
    targets = _selected_targets(state, args.zone)
    if targets is None:
        return 2

    if args.direction == "set":
        if args.value is None or not 1 <= args.value <= len(SPEED_LEVELS):
            print(f"speed stop must be between 1 and {len(SPEED_LEVELS)}",
                  file=sys.stderr)
            return 2
    else:
        step = 1 if args.direction in ("+", "up") else -1

    moved = False
    for key in targets:
        target = state.for_target(key)
        if target.effect in STATIC_EFFECTS:
            continue
        if args.direction == "set":
            new_speed = SPEED_LEVELS[args.value - 1]
        else:
            index = SPEED_LEVELS.index(nearest_speed(target.speed))
            stop = max(0, min(len(SPEED_LEVELS) - 1, index + step))
            new_speed = SPEED_LEVELS[stop]
        if new_speed != target.speed:
            moved = True
        target.speed = new_speed

    if not moved:
        # Not an error. The wheel turned at the end of the ladder, or on an
        # effect with no speed at all; either way the caller wanted a speed
        # change and there is simply none to make.
        print("no speed change", file=sys.stderr)
        return 0

    lightstate.save(state)
    return 0


def cmd_effect(args: argparse.Namespace) -> int:
    if args.name not in EFFECTS:
        print(f"unknown effect: {args.name}", file=sys.stderr)
        print(f"try one of: {', '.join(EFFECTS)}", file=sys.stderr)
        return 2

    state = lightstate.load()
    targets = _selected_targets(state, args.zone)
    if targets is None:
        return 2

    for key in targets:
        state.for_target(key).effect = args.name
    lightstate.save(state)
    return 0


def cmd_display(args: argparse.Namespace) -> int:
    active = unit_active(DISPLAY_UNIT)
    wanted = {"on": True, "off": False, "toggle": not active}[args.action]

    if wanted == active:
        return 0

    error = set_unit(DISPLAY_UNIT, wanted)
    if error:
        print(error, file=sys.stderr)
        return 1
    return 0


def cmd_colour(args: argparse.Namespace) -> int:
    """Set a fixed colour, or hand a target's colour over to the theme."""
    role: str | None = None
    rgb: tuple[int, int, int] | None = None

    if args.value in LED_ROLES:
        role = args.value
        rgb = led_colour(role)
        if rgb is None:
            # Every theme defines `accent` and the six ANSI-backed hues; only
            # `orange` is genuinely absent from some (three of the stock v4
            # themes). Saying which is missing beats a generic failure.
            print(f"the active theme does not define '{role}'", file=sys.stderr)
            return 1
    else:
        rgb = _parse_hex(args.value)
        if rgb is None:
            print(f"not a colour: {args.value}", file=sys.stderr)
            print(f"give a hex value like '#ff0044', or one of: "
                  f"{', '.join(LED_ROLES)}", file=sys.stderr)
            return 2

    state = lightstate.load()
    targets = _selected_targets(state, args.zone)
    if targets is None:
        return 2

    for key in targets:
        target = state.for_target(key)
        target.colour = rgb
        target.colour_role = role
    lightstate.save(state)
    return 0


def cmd_reload_theme(args: argparse.Namespace) -> int:
    """Re-resolve every theme-following colour against the current theme.

    This is what `~/.config/omarchy/hooks/theme-set.d/` calls, and it is the
    whole of the live re-theme: the resolved colours are written back into the
    state file, and `mimarchy-lightd` picks them up on its next frame through
    the same mtime poll it already uses for a keypress. Nothing in the daemon
    knows a theme switch happened, which is precisely why this could not break
    the rendering path.
    """
    state = lightstate.load()

    changed = False
    for key in _targets(state):
        target = state.for_target(key)
        if not target.colour_role:
            continue
        rgb = led_colour(target.colour_role)
        # A role the new theme does not define keeps its previous colour rather
        # than going dark or reverting to white: the user asked for "the theme's
        # orange", and the honest answer on a theme without one is to leave the
        # last orange in place until a theme with one comes back.
        if rgb is not None and rgb != tuple(target.colour):
            target.colour = rgb
            changed = True

    if changed:
        lightstate.save(state)
    return 0


def _parse_hex(value: str) -> tuple[int, int, int] | None:
    text = value.strip().lstrip("#")
    if len(text) != 6:
        return None
    try:
        return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return None


def cmd_link(args: argparse.Namespace) -> int:
    state = lightstate.load()
    state.linked = {"on": True, "off": False, "toggle": not state.linked}[args.action]
    lightstate.save(state)

    # The link flag is also persisted in config.toml, which is what the TUI
    # reads at startup. Writing only the state file would leave the two
    # disagreeing until the next time the TUI wrote one.
    try:
        load_config().save_link_state(state.linked)
    except (OSError, ValueError, KeyError):
        # Best-effort by design: the state file is the source of truth the
        # daemon reads, and it is already written. A malformed config.toml
        # should not turn a successful toggle into a traceback.
        pass
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mimarchy-ctl",
        description="Drive Mimarchy's lighting and cooler display without the TUI.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="print current state")
    status.add_argument("--json", action="store_true",
                        help="machine-readable output (what the bar widget reads)")
    status.set_defaults(func=cmd_status)

    speed = sub.add_parser("speed", help="step the speed ladder, or set it directly")
    speed.add_argument("direction", choices=["+", "-", "up", "down", "set"],
                       help="relative step, or 'set' for an absolute stop")
    speed.add_argument("value", nargs="?", type=int, metavar="N",
                       help=f"1-based speed stop 1..{len(SPEED_LEVELS)}, "
                            f"required with 'set'")
    speed.add_argument("--zone", metavar="KEY",
                       help="act on this target only (default: every target)")
    speed.set_defaults(func=cmd_speed)

    effect = sub.add_parser("effect", help="set the effect on every target")
    effect.add_argument("name", help=f"one of: {', '.join(EFFECTS)}")
    effect.add_argument("--zone", metavar="KEY",
                        help="act on this target only (default: every target)")
    effect.set_defaults(func=cmd_effect)

    display = sub.add_parser("display", help="cooler display on/off")
    display.add_argument("action", choices=["on", "off", "toggle"])
    display.set_defaults(func=cmd_display)

    link = sub.add_parser("link", help="link or unlink CPU and GPU")
    link.add_argument("action", choices=["on", "off", "toggle"])
    link.set_defaults(func=cmd_link)

    colour = sub.add_parser("colour", aliases=["color"],
                            help="set a fixed colour, or follow the theme")
    colour.add_argument("value", metavar="COLOUR",
                        help=f"a hex value like '#ff0044', or one of: "
                             f"{', '.join(LED_ROLES)}")
    colour.add_argument("--zone", metavar="KEY",
                        help="act on this target only (default: every target)")
    colour.set_defaults(func=cmd_colour)

    reload_theme = sub.add_parser(
        "reload-theme",
        help="re-resolve theme-following colours (called by the theme-set hook)")
    reload_theme.set_defaults(func=cmd_reload_theme)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
