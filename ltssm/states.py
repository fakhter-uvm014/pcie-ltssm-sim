"""LTSSM states and substates used by the model.

Author: Farzana Akhter

Each substate name follows the dotted naming used in PCIe textbooks
("Polling.Active", "Recovery.RcvrLock", ...). The *family* of a substate is the
top-level state it belongs to; the viewer colors timeline bars by family.

This is a behavioral model for learning and demonstration. The set of
substates is simplified (see docs/guide/how-it-works.md).
"""

from __future__ import annotations

from enum import Enum


class Substate(str, Enum):
    """Every substate a port can be in. The value is the display name."""

    # Detect: is anybody connected?
    DETECT_QUIET = "Detect.Quiet"
    DETECT_ACTIVE = "Detect.Active"

    # Polling: bit lock and symbol lock at 2.5 GT/s, exchange of TS1/TS2
    POLLING_ACTIVE = "Polling.Active"
    POLLING_COMPLIANCE = "Polling.Compliance"
    POLLING_CONFIGURATION = "Polling.Configuration"

    # Configuration: link number, lane numbers, width
    CFG_LINKWIDTH_START = "Configuration.Linkwidth.Start"
    CFG_LINKWIDTH_ACCEPT = "Configuration.Linkwidth.Accept"
    CFG_LANENUM_WAIT = "Configuration.Lanenum.Wait"
    CFG_LANENUM_ACCEPT = "Configuration.Lanenum.Accept"
    CFG_COMPLETE = "Configuration.Complete"
    CFG_IDLE = "Configuration.Idle"

    # Link up
    L0 = "L0"

    # Recovery: re-lock, speed change, equalization
    REC_RCVRLOCK = "Recovery.RcvrLock"
    REC_RCVRCFG = "Recovery.RcvrCfg"
    REC_SPEED = "Recovery.Speed"
    REC_EQ_PHASE0 = "Recovery.Equalization.Phase0"
    REC_EQ_PHASE1 = "Recovery.Equalization.Phase1"
    REC_EQ_PHASE2 = "Recovery.Equalization.Phase2"
    REC_EQ_PHASE3 = "Recovery.Equalization.Phase3"
    REC_IDLE = "Recovery.Idle"

    # Power management (minimal)
    L0S_ENTRY = "L0s.Entry"
    L0S_IDLE = "L0s.Idle"
    L0S_FTS = "L0s.FTS"
    L1_ENTRY = "L1.Entry"
    L1_IDLE = "L1.Idle"
    L2_IDLE = "L2.Idle"

    # Directed states (minimal)
    HOT_RESET = "Hot Reset"
    DISABLED = "Disabled"
    LOOPBACK_ENTRY = "Loopback.Entry"
    LOOPBACK_ACTIVE = "Loopback.Active"
    LOOPBACK_EXIT = "Loopback.Exit"

    def __str__(self) -> str:  # print the display name, not "Substate.X"
        return self.value

    @property
    def family(self) -> str:
        """Top-level group used for coloring and summaries."""
        return family_of(self.value)


# Family names, in the order the viewer legend shows them.
FAMILIES = (
    "Detect",
    "Polling",
    "Configuration",
    "L0",
    "Recovery",
    "Equalization",
    "L0s",
    "L1",
    "L2",
    "Polling.Compliance",
    "Hot Reset",
    "Disabled",
    "Loopback",
)


def family_of(name: str) -> str:
    """Map a substate display name to its family.

    Recovery.Equalization.* gets its own family ("Equalization") because it
    is the part of Recovery that learners most often need to spot.
    Polling.Compliance gets its own family because it usually signals a
    problem (or a passive test load) rather than normal training.
    """
    if name.startswith("Recovery.Equalization"):
        return "Equalization"
    if name == "Polling.Compliance":
        return "Polling.Compliance"
    if name.startswith("Loopback"):
        return "Loopback"
    head = name.split(".")[0]
    return head


def parse_substate(name: str) -> Substate:
    """Return the Substate with this display name (raises ValueError)."""
    for s in Substate:
        if s.value == name:
            return s
    raise ValueError(f"unknown LTSSM substate: {name!r}")


CONFIGURATION_SUBSTATES = (
    Substate.CFG_LINKWIDTH_START,
    Substate.CFG_LINKWIDTH_ACCEPT,
    Substate.CFG_LANENUM_WAIT,
    Substate.CFG_LANENUM_ACCEPT,
    Substate.CFG_COMPLETE,
    Substate.CFG_IDLE,
)

RECOVERY_SUBSTATES = (
    Substate.REC_RCVRLOCK,
    Substate.REC_RCVRCFG,
    Substate.REC_SPEED,
    Substate.REC_EQ_PHASE0,
    Substate.REC_EQ_PHASE1,
    Substate.REC_EQ_PHASE2,
    Substate.REC_EQ_PHASE3,
    Substate.REC_IDLE,
)

EQ_PHASES = {
    Substate.REC_EQ_PHASE0: 0,
    Substate.REC_EQ_PHASE1: 1,
    Substate.REC_EQ_PHASE2: 2,
    Substate.REC_EQ_PHASE3: 3,
}


def is_link_training(sub: Substate) -> bool:
    """Mirror of the Link Status 'Link Training' bit: set in Configuration or Recovery."""
    return sub in CONFIGURATION_SUBSTATES or sub in RECOVERY_SUBSTATES
