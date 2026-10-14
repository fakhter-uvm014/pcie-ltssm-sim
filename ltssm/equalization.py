"""Equalization (8.0 and 16.0 GT/s) and the speed-change policy.

Author: Farzana Akhter

Equalization in the model
-------------------------
At 8.0 GT/s and 16.0 GT/s a link must tune its transmitters before it can
run reliably. The model keeps the four phases and who does what in each:

* Phase 0 - the USP transmits with the preset the DSP asked for; it waits
  until it can hear the DSP.
* Phase 1 - both sides exchange TS1 (EC=01) to show they can hear each other.
* Phase 2 - the **USP is the requester**: it asks the DSP to try transmitter
  presets and judges each one with its own receiver (downstream direction).
* Phase 3 - the **DSP is the requester**: it asks the USP to try presets and
  judges them with its receiver (upstream direction).

Real equalization adjusts FIR coefficients and measures the eye. Here each
preset trial produces a symbolic *figure of merit* (FOM, 0..100). A preset
passes when its FOM reaches ``Config.eq_fom_target``. The FOM depends on the
rate, the distance from a "best" preset, a seeded random wobble, and any
``eq_fail`` fault on the link (which subtracts a large penalty). Presets
P0..P10 are names only; they do not encode real de-emphasis or preshoot.

Speed-change policy
-------------------
Links train at 2.5 GT/s first. The DSP then steps up:

* ``gen2_step: false`` (default): Gen1 -> Gen3 -> Gen4. Gen2 is used only if
  it is the best both sides can do, or as a fallback.
* ``gen2_step: true``: Gen1 -> Gen2 -> Gen3 -> Gen4.
* 8.0 GT/s equalization must complete before 16.0 GT/s is attempted.
* After ``eq_max_attempts`` failed equalizations at a rate, the DSP lowers
  its *rate ceiling* below that rate and tries the next lower untried rate.
  The reason is written to the trace.
* Software can cap the rate with Target Link Speed (LnkCtl2).
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Optional, Tuple

from .config import GEN_NAMES, RATE_GTS

if TYPE_CHECKING:  # pragma: no cover
    from .port import Port

PRESETS = [f"P{i}" for i in range(11)]

# Symbolic channel response: FOM of the best preset on a nominal channel, and
# which preset is best. 16 GT/s has less margin than 8 GT/s on purpose.
NOMINAL_FOM = {3: 82, 4: 72}
BEST_PRESET = {3: "P7", 4: "P8"}
FOM_PER_PRESET_STEP = 7  # FOM lost per preset index away from the best one
EQ_FAIL_PENALTY = 45  # what an ``eq_fail`` fault subtracts

PHASE_DIRECTION = {
    2: "downstream direction: DSP TX -> USP RX",
    3: "upstream direction: USP TX -> DSP RX",
}


def figure_of_merit(rate: int, preset: str, penalty: int, rng: random.Random) -> int:
    """Symbolic eye quality for one preset trial (0..100)."""
    distance = abs(PRESETS.index(preset) - PRESETS.index(BEST_PRESET[rate]))
    value = NOMINAL_FOM[rate] - FOM_PER_PRESET_STEP * distance - penalty + rng.randint(-2, 2)
    return max(0, min(100, value))


class EqStatus:
    """Per-rate equalization status bits, like Link Status 2 / 16 GT/s status."""

    __slots__ = ("complete", "phase1", "phase2", "phase3", "request")

    def __init__(self) -> None:
        self.clear()

    def clear(self) -> None:
        self.complete = False
        self.phase1 = False
        self.phase2 = False
        self.phase3 = False
        self.request = False

    def any_set(self) -> bool:
        return self.complete or self.phase1 or self.phase2 or self.phase3 or self.request

    def to_dict(self) -> dict:
        return {
            "complete": self.complete,
            "phase1": self.phase1,
            "phase2": self.phase2,
            "phase3": self.phase3,
            "link_eq_request": self.request,
        }


def rate_cap(port: "Port") -> Tuple[int, str]:
    """Highest rate the DSP is allowed to aim for, and which limit sets it."""
    limits = [
        (port.max_speed, f"{port.name} supports up to {GEN_NAMES[port.max_speed]}"),
        (port.partner_max_rate, f"partner advertised up to {GEN_NAMES[port.partner_max_rate]}"),
        (
            port.target_link_speed,
            f"Target Link Speed (LnkCtl2) is {GEN_NAMES[port.target_link_speed]}",
        ),
        (port.rate_ceiling, f"rate ceiling {GEN_NAMES[port.rate_ceiling]} after failures"),
    ]
    value = min(v for v, _ in limits)
    reasons = [why for v, why in limits if v == value]
    if value == port.max_speed == port.partner_max_rate and len(reasons) >= 2:
        return value, f"both ends support {GEN_NAMES[value]}"
    return value, " and ".join(reasons)


def next_speed_step(port: "Port", gen2_step: bool) -> Optional[Tuple[int, str]]:
    """Return ``(rate_to_advertise, explanation)`` for the next directed speed
    change, or ``None`` when the link is already at the best allowed rate."""
    cap, why = rate_cap(port)
    current = port.rate
    if current >= cap:
        return None
    if gen2_step:
        target = current + 1
        note = "gen2_step policy: one rate at a time"
    else:
        target = cap
        note = "straight to the highest allowed rate"
        if current < 3 and cap == 4:
            target = 3
            note = "8.0 GT/s first, because 8 GT/s equalization must complete before 16 GT/s is tried"
    if target == 4 and not port.eq[3].complete:
        target = 3
        note = "8 GT/s equalization is not complete yet, so 16 GT/s is not tried"
    text = (
        f"speed change {GEN_NAMES[current]} -> {GEN_NAMES[target]} ({RATE_GTS[target]}): "
        f"{note}; link target is {GEN_NAMES[cap]} because {why}"
    )
    return target, text
