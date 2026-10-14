"""Symbolic ordered sets (TS1, TS2, EIOS, EIEOS, SKP, FTS) and idle data.

Author: Farzana Akhter

Real ordered sets are 16-symbol bit patterns. Here each one is a small,
immutable Python object that carries only the fields the model needs. Fields
that do not apply are ``None`` (shown as "PAD" for link and lane numbers,
which is what a real TS1 carries before numbers are assigned).

Traffic is batched: a port sends a *burst* of ``Config.os_per_burst``
identical ordered sets per lane in one engine event. The receiver keeps, per
lane, the last ordered set seen and how many identical ones arrived in a row.
Rules such as "8 consecutive TS1" are checked against that count.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Tuple


class OSType(str, Enum):
    """What a lane carries. IDLE and TEST_PATTERN are not ordered sets, but
    they flow through the same channel so they are modeled the same way."""

    TS1 = "TS1"  # training set 1: link/lane numbers, rates, speed change, EQ fields
    TS2 = "TS2"  # training set 2: "I have what I need, confirm"
    EIOS = "EIOS"  # Electrical Idle Ordered Set: "my transmitter is going idle"
    EIEOS = "EIEOS"  # Electrical Idle Exit Ordered Set (used at 8 GT/s and above)
    SKP = "SKP"  # clock compensation; logged only, no behavior in the model
    FTS = "FTS"  # Fast Training Sequence: leaves L0s
    IDLE = "IDLE"  # logical idle data (the "8 idle symbols" rules)
    TEST_PATTERN = "TEST_PATTERN"  # the test pattern a port sends in Polling.Compliance

    def __str__(self) -> str:
        return self.value


TRAINING_SETS = (OSType.TS1, OSType.TS2)


@dataclass(frozen=True)
class OrderedSet:
    """One symbolic ordered set as seen on one lane.

    Two ordered sets are "identical" (for consecutive counting) when every
    field matches; the dataclass equality does exactly that.
    """

    type: OSType
    link_num: Optional[int] = None  # None means PAD
    lane_num: Optional[int] = None  # None means PAD
    rates: Optional[Tuple[int, ...]] = None  # data rates advertised, e.g. (1, 2, 3, 4)
    speed_change: Optional[bool] = None  # "I want to change speed" bit
    eq_phase: Optional[int] = None  # EC field in TS1 at 8 GT/s and above (0..3)
    preset: Optional[str] = None  # transmitter preset requested / in use (symbolic)
    hot_reset: Optional[bool] = None  # Training Control bits
    disable: Optional[bool] = None
    loopback: Optional[bool] = None

    @property
    def is_training(self) -> bool:
        return self.type in TRAINING_SETS

    @property
    def max_rate(self) -> int:
        return max(self.rates) if self.rates else 1

    def describe(self) -> str:
        """Short human-readable form used in trace details and tests."""
        if self.type not in TRAINING_SETS:
            return self.type.value
        link = "PAD" if self.link_num is None else str(self.link_num)
        lane = "PAD" if self.lane_num is None else str(self.lane_num)
        parts = [f"{self.type.value}(link={link}, lane={lane}"]
        if self.rates:
            parts.append(f", rates=Gen1..Gen{max(self.rates)}")
        if self.speed_change:
            parts.append(", speed_change=1")
        if self.eq_phase is not None:
            parts.append(f", EC={self.eq_phase:02b}")
        if self.preset:
            parts.append(f", preset={self.preset}")
        for bit in ("hot_reset", "disable", "loopback"):
            if getattr(self, bit):
                parts.append(f", {bit}=1")
        parts.append(")")
        return "".join(parts)


def make_ts(kind: OSType, **fields) -> OrderedSet:
    """Helper used by ports and tests: ``make_ts(OSType.TS1, link_num=0)``."""
    return OrderedSet(type=kind, **fields)


EIOS = OrderedSet(OSType.EIOS)
EIEOS = OrderedSet(OSType.EIEOS)
IDLE = OrderedSet(OSType.IDLE)
TEST_PATTERN = OrderedSet(OSType.TEST_PATTERN)
FTS = OrderedSet(OSType.FTS)
SKP = OrderedSet(OSType.SKP)


# A received burst on one lane: (lane, ordered set, count, per-OS good flags or None).
# ``flags`` is None when every ordered set in the burst arrived intact; otherwise it
# is a list of booleans, one per ordered set, False meaning dropped or corrupted.
RxLane = Tuple[int, OrderedSet, int, Optional[List[bool]]]


@dataclass
class RxBurst:
    """What a receiver gets from the Link in one delivery event."""

    rate: int  # data rate the sender used
    lanes: List[RxLane]

    def by_lane(self) -> Dict[int, OrderedSet]:
        return {lane: os for lane, os, _, _ in self.lanes}


class LaneReceiver:
    """Per-lane receive state: last ordered set, current run, best runs.

    This class holds the rule "N consecutive identical ordered sets". A
    corrupted or dropped ordered set breaks the run; the next good one starts
    a new run of length 1.

    ``best`` remembers, for every distinct ordered set seen since the last
    reset (the last substate entry), the longest run achieved. Rules like
    "received 8 consecutive TS2" are *latched*: once achieved in a substate
    they stay true even if the partner has already moved on to sending
    something else. Without this, two ports that move at slightly different
    times could miss each other.
    """

    __slots__ = ("last", "consec", "errors", "best")

    def __init__(self) -> None:
        self.last: Optional[OrderedSet] = None
        self.consec = 0
        self.errors = 0
        self.best: Dict[OrderedSet, int] = {}

    def reset(self) -> None:
        self.last = None
        self.consec = 0
        self.best = {}

    def break_run(self) -> None:
        """Electrical Idle or undecodable symbols: the current run ends, latched runs stay."""
        self.last = None
        self.consec = 0

    def accept(self, os: OrderedSet, count: int, flags: Optional[List[bool]]) -> None:
        run = self.consec if self.last == os else 0
        if flags is None:
            run += count
        else:
            longest = 0
            for good in flags:
                if good:
                    run += 1
                    if run > longest:
                        longest = run
                else:
                    run = 0
                    self.errors += 1
            if longest > self.best.get(os, 0):
                self.best[os] = longest
        self.last = os
        self.consec = run
        if run > self.best.get(os, 0):
            self.best[os] = run

    def achieved(self, pred, n: int) -> Optional[OrderedSet]:
        """Return an ordered set that satisfied ``pred`` with a run >= n, if any."""
        for os, run in self.best.items():
            if run >= n and pred(os):
                return os
        return None
