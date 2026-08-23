"""Desired lighting state, shared between `mimarchy-ctl` and the lighting daemon.

A small JSON file `mimarchy-ctl` writes and the daemon polls. Deliberately not
a socket: the daemon has to survive the writer exiting (that's the whole point
of it existing — `mimarchy-ctl` is a one-shot process, called anew by the bar
panel for every click and keypress), the state is a handful of fields, and a
file means state also persists across reboots with no extra work.

Writes are atomic — the daemon polls a few times a second and would otherwise
occasionally read a half-written file.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

_RUNTIME_DIR = Path(
    os.environ.get("XDG_RUNTIME_DIR")
    or os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
)
_CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))

STATE_PATH = _RUNTIME_DIR / "mimarchy-lighting.json"

#: Where the state is kept across reboots. XDG_RUNTIME_DIR is cleared on
#: logout, so the daemon seeds from here when the runtime copy is missing.
PERSIST_PATH = _CONFIG_HOME / "mimarchy" / "lighting.json"


@dataclass
class TargetState:
    effect: str = "static"
    colour: tuple[int, int, int] = (255, 255, 255)
    speed: float = 1.0

    #: A theme palette role (`accent`, `green`, ...) when the colour is meant to
    #: follow the desktop, or None when the user picked a fixed colour.
    #:
    #: The resolved RGB is still written to `colour` alongside it, and that
    #: redundancy is deliberate. It keeps `lightd` unchanged — it reads `colour`
    #: and knows nothing about themes, so the rendering path takes no new failure
    #: mode — and it means a theme that has gone missing shows the last colour
    #: that worked rather than going dark. `colour_role` is the record of *why*
    #: that value is what it is, which is what lets a theme switch re-resolve it.
    colour_role: str | None = None


@dataclass
class LightingState:
    linked: bool = True
    targets: dict[str, TargetState] = field(default_factory=dict)

    def for_target(self, key: str) -> TargetState:
        return self.targets.setdefault(key, TargetState())

    def to_json(self) -> str:
        return json.dumps(
            {"linked": self.linked,
             "targets": {k: asdict(v) for k, v in self.targets.items()}},
            indent=2,
        )

    @classmethod
    def from_json(cls, text: str) -> "LightingState":
        raw = json.loads(text)
        return cls(
            linked=bool(raw.get("linked", True)),
            targets={
                k: TargetState(
                    effect=v.get("effect", "static"),
                    colour=tuple(v.get("colour", (255, 255, 255))),  # type: ignore[arg-type]
                    speed=float(v.get("speed", 1.0)),
                    # Absent in every file written before theme-following
                    # existed, which reads correctly as "this is a fixed colour".
                    colour_role=v.get("colour_role") or None,
                )
                for k, v in raw.get("targets", {}).items()
            },
        )


def load() -> LightingState:
    for path in (STATE_PATH, PERSIST_PATH):
        try:
            return LightingState.from_json(path.read_text())
        except (OSError, ValueError):
            continue
    return LightingState()


def save(state: LightingState) -> None:
    text = state.to_json()
    for path in (STATE_PATH, PERSIST_PATH):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Write-then-rename: the daemon polls several times a second and
            # would otherwise read a partially written file.
            tmp = path.with_suffix(".tmp")
            tmp.write_text(text)
            tmp.replace(path)
        except OSError:
            pass


def mtime() -> float:
    try:
        return STATE_PATH.stat().st_mtime
    except OSError:
        return 0.0
