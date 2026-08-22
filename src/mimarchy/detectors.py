"""Which OpenRGB detectors this machine needs enabled, and only those.

OpenRGB ships ~1953 device detectors and enables every one of them. Its broad
GPU/I2C probing is a documented total-system freeze
([#4888](https://gitlab.com/CalcProgrammer1/OpenRGB/-/issues/4888), open) and
`openrgb.service` starts at login, so an unrestricted config is a freeze on
every boot rather than a one-off. Narrowing the list is part of installing this,
not a tuning step.

The hard part is that nothing in OpenRGB connects a *device* to the *detector*
that produced it. The SDK reports device names; the config file lists detector
names; there is no shared id, and the only way to ask OpenRGB directly is to run
detection — which is the dangerous act. So the allowlist is inferred from names,
and inferred conservatively: enabling too few detectors costs a dark zone the
user can see and report, enabling too many costs a locked-up machine.

Three rules, in the order they were needed:

1. **The device's own name first, then containment.** A GPU detector is
   registered under the card's full retail name and OpenRGB hands that same
   string back as the device name, so an exact match is the common case and
   wins outright. Failing that, either string containing the other counts,
   because a device name is sometimes the detector name plus a suffix and
   sometimes the other way round.
2. **One match, or none.** Zone `device` values are matched as substrings, so
   they are deliberately loose — this repo's own default config shipped
   `device = "Sapphire"`, which is a fine way to find one card and a terrible
   way to pick detectors: it names every Sapphire card OpenRGB knows, i.e.
   dozens of I2C probes, which is exactly the hazard. Anything that matches two
   or more detectors without matching one of them exactly is describing a
   product line rather than a product, and is refused rather than guessed at.
   The rule started as "more than the largest real family", i.e. more than the
   three ASUS Aura detectors — which turned out to be barely a rule at all. Run
   against a vendor with four cards in the list, it let all four through: four
   I2C probes for cards the machine does not have, which is the exact failure
   this exists to prevent.
3. **A family table, currently one entry.** ASUS's Aura USB detectors are named
   for the protocol (`ASUS Aura Motherboard`) while the controller reports the
   *board model* as its device name, so no amount of name matching can connect
   the two. Those three are USB HID with no I2C anywhere near them, which is why
   enabling all three on an ASUS board is safe. Other vendors are not guessed
   at: entries get added when someone reports a real pairing, and until then the
   escape hatch below is the answer.

The escape hatch is `detectors = [...]` under `[rgb]` in `config.toml`, which
overrides all of this. `mimarchy-setup` writes it from the device names OpenRGB
actually reported, which is the best information anyone has.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_OPENRGB_CONFIG = Path.home() / ".config/OpenRGB/OpenRGB.json"


class DetectorConfigError(RuntimeError):
    """OpenRGB's config file is missing or not in the expected shape."""

#: The set this project was developed against: three USB-only ASUS Aura
#: detectors for the motherboard headers, and the single detector matching one
#: exact graphics card. Kept as the fallback for a run with no config to read,
#: so the behaviour of an install that never got as far as `mimarchy-setup` is
#: the behaviour this tool has always had.
REFERENCE_KEEP = frozenset({
    "ASUS Aura Addressable",
    "ASUS Aura Core",
    "ASUS Aura Motherboard",
    # Safe on kernel 7.1.4; the freeze reports were on 6.15, before the AMD I2C
    # patches landed.
    "Sapphire Radeon RX 9070 XT Nitro+",
})

#: ASUS board-model tokens. The Aura USB controller reports the board model, so
#: this is what a device name looks like when the detectors are named `ASUS
#: Aura *`. Matched as whole tokens rather than substrings — `tuf` and `prime`
#: are short enough to turn up inside unrelated words otherwise.
_ASUS_BOARD_TOKENS = frozenset({
    "asus", "aura", "prime", "rog", "strix", "tuf", "proart",
    "maximus", "crosshair", "sabertooth",
})

_ASUS_AURA_USB = ("ASUS Aura Addressable", "ASUS Aura Core",
                  "ASUS Aura Motherboard")


@dataclass
class Match:
    """What one configured device resolved to, and why."""

    device: str
    detectors: set[str] = field(default_factory=set)
    #: Present when the answer is worth explaining: nothing matched, or a match
    #: was thrown away for being too broad. The commands print these — a silent
    #: empty allowlist is how a user ends up with dark LEDs and no idea why.
    note: str | None = None


def _normalise(text: str) -> str:
    """Lowercase, with every run of non-alphanumerics collapsed to one space.

    Detector and device names disagree about punctuation for the same hardware
    (`Nitro+` against `Nitro +`, `X870-P` against `X870 P`), and that difference
    is never meaningful.
    """
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _tokens(text: str) -> set[str]:
    return set(_normalise(text).split())


def detectors_for_device(device: str, detectors: list[str]) -> Match:
    """The detectors that plausibly produced a device named `device`."""
    target = _normalise(device)
    if not target:
        return Match(device, note="empty device name")

    note = None
    hits = {name for name in detectors if _normalise(name) == target}
    if not hits:
        # An exact match beats any superstring: a detector named for a product
        # and a detector named for that product plus a variant suffix are two
        # different things, and the device that reported this name is the first
        # one.
        hits = {name for name in detectors
                if target in _normalise(name) or _normalise(name) in target}
        if len(hits) > 1:
            note = (f"matches {len(hits)} detectors and none of them exactly, "
                    "so it names a product line rather than a product — ignored")
            hits = set()

    # Additive rather than an else-branch: on a board whose device name *is*
    # `ASUS Aura Motherboard`, containment finds one of the three and the other
    # two headers would go dark.
    if _tokens(device) & _ASUS_BOARD_TOKENS:
        hits |= {name for name in detectors if name in _ASUS_AURA_USB}

    if not hits and note is None:
        note = "no detector name resembles it"
    return Match(device, hits, note)


def resolve(devices: list[str], detectors: list[str]) -> list[Match]:
    return [detectors_for_device(device, detectors) for device in devices]


def read_detector_names(path: Path = DEFAULT_OPENRGB_CONFIG) -> list[str]:
    """Every detector OpenRGB knows about, whether enabled or not."""
    return sorted(read_config(path)[1])


def read_config(path: Path = DEFAULT_OPENRGB_CONFIG) -> tuple[dict, dict]:
    """`(whole document, the Detectors map)` — the map is a live view into it.

    Both are returned because the writer has to hand OpenRGB back a document it
    still recognises: flipping the flags and re-dumping the *whole* file keeps
    every setting the GUI has stored, where writing only the detector section
    would quietly reset the rest.

    Raises rather than returning an empty map: "OpenRGB has never run" and
    "OpenRGB reports no detectors" call for completely different advice, and the
    difference is invisible once both are `{}`.
    """
    try:
        data = json.loads(path.read_text())
    except OSError as exc:
        raise DetectorConfigError(
            f"no OpenRGB config at {path} — start OpenRGB once first") from exc
    except ValueError as exc:
        raise DetectorConfigError(f"{path} is not valid JSON: {exc}") from exc

    detectors = data.get("Detectors", {}).get("detectors")
    if detectors is None:
        raise DetectorConfigError(
            f"{path} has no Detectors section — unexpected OpenRGB version?")
    return data, detectors
