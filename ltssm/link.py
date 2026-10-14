"""A physical link between two ports: lanes, wiring and fault injection.

Author: Farzana Akhter

The Link carries bursts of ordered sets from one port to the other after a
small flight delay. On the way it applies the link's faults:

* ``dead_lanes``       open lanes: no receiver termination and no signal.
* ``silent_lanes``     termination present but no signal (a broken transmit path).
* ``reversed``         lane i at the DSP is wired to lane N-1-i at the USP.
* ``lane_errors``      per-lane drop/corrupt probability, optionally only at
                       some data rates; uses the link's seeded RNG.
* ``eq_fail``          equalization at a given rate/phase cannot reach the
                       figure-of-merit target.
* ``tx_quirks``        a port's transmitter misbehaves: ``ts2_as_ts1``
                       (never sends TS2) or ``silent_until_ns``.
* ``periodic_errors``  receiver errors injected in L0 at fixed times.
* ``connection_lost``  at a given time every lane opens (surprise link down).

The Link also keeps the link-level counters printed in the run summary
(recoveries, flaps, detect cycles, equalization attempts, timings).

Lane numbering: fault lanes are given in the DSP's lane numbers.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional, Set

from .config import GEN_NAMES, Config, format_lanes
from .engine import Engine
from .ordered_sets import OrderedSet, OSType, RxBurst, RxLane
from .states import Substate as S
from .trace import Trace

if TYPE_CHECKING:  # pragma: no cover
    from .port import Port


@dataclass
class LaneErrorFault:
    lanes: List[int]
    corrupt_rate: float = 0.0
    drop_rate: float = 0.0
    rates: Optional[List[int]] = None  # None = every rate
    direction: str = "both"  # "both", "down" (DSP->USP) or "up" (USP->DSP)

    def applies(self, rate: int, downstream: bool) -> bool:
        if self.rates is not None and rate not in self.rates:
            return False
        if self.direction == "down" and not downstream:
            return False
        if self.direction == "up" and downstream:
            return False
        return True

    @property
    def p_bad(self) -> float:
        return min(1.0, self.corrupt_rate + self.drop_rate)


@dataclass
class TxQuirk:
    ts2_as_ts1: bool = False
    silent_until_ns: Optional[int] = None


@dataclass
class LinkCounters:
    detect_cycles: int = 0
    recovery_count: int = 0
    flap_count: int = 0
    speed_changes: int = 0
    link_down_count: int = 0
    l1_entries: int = 0
    rx_errors: int = 0
    eq_attempts: Dict[str, int] = field(default_factory=lambda: {"Gen3": 0, "Gen4": 0})
    eq_failures: Dict[str, int] = field(default_factory=lambda: {"Gen3": 0, "Gen4": 0})
    time_to_l0_ns: Optional[int] = None
    time_to_final_speed_ns: Optional[int] = None
    recovery_causes: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "time_to_l0_ns": self.time_to_l0_ns,
            "time_to_final_speed_ns": self.time_to_final_speed_ns,
            "recovery_count": self.recovery_count,
            "flap_count": self.flap_count,
            "recovery_causes": dict(sorted(self.recovery_causes.items())),
            "speed_changes": self.speed_changes,
            "detect_cycles": self.detect_cycles,
            "link_down_count": self.link_down_count,
            "l1_entries": self.l1_entries,
            "rx_errors": self.rx_errors,
            "eq_attempts": dict(self.eq_attempts),
            "eq_failures": dict(self.eq_failures),
        }


# Why a link left L0 for Recovery, keyed by the transition-table event.
RECOVERY_CAUSE = {
    "receiver_error": "error",
    "speed_change_wanted": "speed change",
    "directed_hot_reset": "directed",
    "directed_disable": "directed",
    "directed_loopback": "directed",
    "directed_retrain": "retrain",
    "traffic_pending": "L1 exit",
    "rx_ei_exit": "L1 exit",
    "rx_ts_in_l0": "partner",
}


class Link:
    """Channel between a DSP and a USP (the USP may be absent: empty slot)."""

    def __init__(
        self,
        name: str,
        dsp: "Port",
        usp: Optional["Port"],
        config: Config,
        engine: Engine,
        trace: Trace,
        seed: int,
    ) -> None:
        self.name = name
        self.dsp = dsp
        self.usp = usp
        self.config = config
        self.engine = engine
        self.trace = trace
        # str seeds are hashed with SHA-512 by random.Random: stable across runs and OSes.
        self.rng = random.Random(f"{seed}:{name}")
        self.lanes = min(dsp.max_width, usp.max_width) if usp else dsp.max_width

        # faults
        self.dead_lanes: Set[int] = set()
        self.silent_lanes: Set[int] = set()
        self.reversed = False
        self.lane_errors: List[LaneErrorFault] = []
        self.eq_fail: Dict[tuple, int] = {}  # (rate, phase) -> FOM penalty
        self.tx_quirks: Dict[str, TxQuirk] = {}
        self.connection_lost = False

        self.counters = LinkCounters()
        self.up = False  # both ports reached L0 and DL is (about to be) active
        self._episode_active = False
        self._last_up_speed: Optional[int] = None
        self._monitor_gen = 0
        self._monitor_running = False

        dsp.attach(self)
        if usp is not None:
            usp.attach(self)

    # ------------------------------------------------------------------ wiring
    def partner(self, port: "Port") -> Optional["Port"]:
        return self.usp if port is self.dsp else self.dsp

    def ports(self) -> List["Port"]:
        return [p for p in (self.dsp, self.usp) if p is not None]

    def to_dsp_lane(self, port: "Port", lane: int) -> int:
        """Translate a port's physical lane to the DSP's lane number."""
        if port is self.dsp or not self.reversed:
            return lane
        return self.lanes - 1 - lane

    def to_partner_lane(self, port: "Port", lane: int) -> int:
        if not self.reversed:
            return lane
        return self.lanes - 1 - lane

    # ---------------------------------------------------------------- detection
    def detect_receivers(self, port: "Port") -> List[int]:
        """Lanes (in ``port``'s numbering) where the partner presents termination."""
        partner = self.partner(port)
        if partner is None or self.connection_lost:
            return []
        if partner.held or not partner.powered:
            return []  # a port held in reset keeps its receiver high-impedance
        found = []
        for lane in range(min(port.max_width, self.lanes)):
            if self.to_dsp_lane(port, lane) in self.dead_lanes:
                continue
            found.append(lane)
        return found

    # ----------------------------------------------------------------- traffic
    def transmit(self, sender: "Port", lanes_os: Dict[int, OrderedSet], count: int) -> None:
        """Carry one burst from ``sender`` to its partner, applying faults."""
        partner = self.partner(sender)
        if partner is None or self.connection_lost:
            return
        quirk = self.tx_quirks.get(sender.name)
        if quirk is not None and quirk.silent_until_ns is not None:
            if self.engine.now < quirk.silent_until_ns:
                return
        downstream = sender is self.dsp
        out: List[RxLane] = []
        for lane, os in lanes_os.items():
            if lane >= self.lanes:
                continue
            dsp_lane = self.to_dsp_lane(sender, lane)
            if dsp_lane in self.dead_lanes or dsp_lane in self.silent_lanes:
                continue
            if quirk is not None and quirk.ts2_as_ts1 and os.type is OSType.TS2:
                os = OrderedSet(
                    OSType.TS1, os.link_num, os.lane_num, os.rates, os.speed_change,
                    os.eq_phase, os.preset, os.hot_reset, os.disable, os.loopback,
                )
            flags = None
            for fault in self.lane_errors:
                if dsp_lane in fault.lanes and fault.applies(sender.rate, downstream):
                    p_bad = fault.p_bad
                    flags = [self.rng.random() >= p_bad for _ in range(count)]
                    break
            out.append((self.to_partner_lane(sender, lane), os, count, flags))
        if out:
            self.engine.schedule(self.config.link_delay_ns, partner.receive, RxBurst(sender.rate, out))

    # ------------------------------------------------------------ equalization
    def eq_penalty(self, rate: int, phase: int) -> int:
        return self.eq_fail.get((rate, phase), 0)

    # ---------------------------------------------------------- fault actions
    def schedule_periodic_errors(self, start_ns: int, period_ns: int, count: int) -> None:
        for k in range(count):
            self.engine.schedule_at(start_ns + k * period_ns, self._periodic_error, k, count)

    def _periodic_error(self, k: int, count: int) -> None:
        # Alternate which receiver sees the error: even -> USP, odd -> DSP.
        receiver = self.usp if k % 2 == 0 else self.dsp
        if receiver is None:
            return
        cause = f"injected periodic L0 error {k + 1} of {count} on link {self.name}"
        if receiver.substate is S.L0:
            receiver.inject_rx_error(
                "receiver error in L0 (framing error / lost block alignment)", cause
            )
        else:
            self.trace.add(
                self.engine.now, "fault", receiver.name, self.name, receiver.substate.value,
                receiver.substate.value,
                f"error injection skipped: port is in {receiver.substate.value}, not L0",
                GEN_NAMES[receiver.rate], receiver.width_text, detail={"cause": cause},
            )

    def schedule_connection_loss(self, at_ns: int) -> None:
        self.engine.schedule_at(at_ns, self._lose_connection)

    def _lose_connection(self) -> None:
        self.connection_lost = True
        self.trace.add(
            self.engine.now, "fault", self.dsp.name, self.name, self.dsp.substate.value,
            self.dsp.substate.value, f"fault: every lane of link {self.name} opens (connection lost)",
            GEN_NAMES[self.dsp.rate], self.dsp.width_text,
            detail={"cause": "connection_lost fault: card removed, cable unplugged or power lost"},
        )
        for port in self.ports():
            if port.substate is S.L0:
                port.inject_rx_error(
                    "receiver lost signal: all lanes dropped to Electrical Idle without EIOS",
                    "connection_lost fault",
                )

    # ------------------------------------------------------ L0 error sampling
    def _start_monitor(self) -> None:
        if self._monitor_running or not self.lane_errors:
            return
        self._monitor_running = True
        self._monitor_gen += 1
        self.engine.schedule(self.config.l0_error_check_ns, self._monitor, self._monitor_gen)

    def _monitor(self, gen: int) -> None:
        if gen != self._monitor_gen:
            return
        if self.usp is None or self.dsp.substate is not S.L0 or self.usp.substate is not S.L0:
            self._monitor_running = False
            return
        n = self.config.os_per_burst
        hit = None
        for fault in self.lane_errors:
            for downstream in (True, False):
                if not fault.applies(self.dsp.rate, downstream):
                    continue
                p_window = 1.0 - (1.0 - fault.p_bad) ** n
                for lane in fault.lanes:
                    if lane >= self.lanes or lane >= self.dsp.width:
                        continue
                    if self.rng.random() < p_window and hit is None:
                        hit = (downstream, lane)
        if hit is not None:
            downstream, lane = hit
            receiver = self.usp if downstream else self.dsp
            receiver.inject_rx_error(
                f"receiver error in L0 on lane {lane} (symbol/block error)",
                f"lane_errors fault on lane {lane} at {GEN_NAMES[self.dsp.rate]}",
                lane=lane,
            )
            self._monitor_running = False
            return
        self.engine.schedule(self.config.l0_error_check_ns, self._monitor, gen)

    # -------------------------------------------------------------- bookkeeping
    def on_state_change(self, port: "Port", old: S, new: S, event: Optional[str]) -> None:
        """Called by both ports after every transition; keeps link counters."""
        c = self.counters
        now = self.engine.now
        partner = self.partner(port)
        if new is S.REC_RCVRLOCK and old in (S.L0, S.L1_IDLE) and not self._episode_active:
            self._episode_active = True
            cause = RECOVERY_CAUSE.get(event or "", "other")
            c.recovery_count += 1
            c.recovery_causes[cause] = c.recovery_causes.get(cause, 0) + 1
            if cause == "error":
                c.flap_count += 1
        if new is S.L0 and partner is not None and partner.substate is S.L0:
            self._episode_active = False
            if not self.up:
                self.up = True
                if c.time_to_l0_ns is None:
                    c.time_to_l0_ns = now
            if self._last_up_speed != self.dsp.rate:
                self._last_up_speed = self.dsp.rate
                c.time_to_final_speed_ns = now
            self._start_monitor()
        if new in (S.DETECT_QUIET, S.HOT_RESET, S.DISABLED) and self.up:
            self.up = False
            self._episode_active = False
            self._last_up_speed = None
            c.link_down_count += 1
        if new is S.L1_IDLE and port is self.dsp:
            c.l1_entries += 1

    def final_summary(self) -> dict:
        dsp, usp = self.dsp, self.usp
        if usp is None:
            state = dsp.substate.value
        elif dsp.substate is S.L0 and usp.substate is S.L0:
            state = "L0"
        else:
            state = dsp.substate.value
        up = usp is not None and dsp.substate is S.L0 and usp.substate is S.L0
        return {
            "name": self.name,
            "dsp": dsp.name,
            "usp": usp.name if usp else None,
            "physical_lanes": self.lanes,
            "state": state,
            "speed": GEN_NAMES[dsp.rate],
            "width": dsp.width_text if up else "x0",
            "up": up,
            "dl_link_active": bool(dsp.dl_link_active and (usp is not None and usp.dl_link_active)),
            "lane_reversal": bool(usp is not None and usp.lane_reversed),
            "faults": self.describe_faults(),
            "counters": self.counters.to_dict(),
        }

    def describe_faults(self) -> List[str]:
        out = []
        if self.usp is None:
            out.append("empty slot: no device on this link")
        if self.dead_lanes:
            out.append(f"lanes {format_lanes(sorted(self.dead_lanes))} open (no termination, no signal)")
        if self.silent_lanes:
            out.append(f"lanes {format_lanes(sorted(self.silent_lanes))} terminate but never carry signal")
        if self.reversed:
            out.append("lanes wired in reverse order")
        for f in self.lane_errors:
            rates = "all rates" if f.rates is None else ",".join(GEN_NAMES[r] for r in f.rates)
            out.append(
                f"lanes {format_lanes(f.lanes)} corrupt {f.corrupt_rate:g} / drop {f.drop_rate:g} "
                f"per ordered set at {rates} ({f.direction})"
            )
        for (rate, phase), _pen in sorted(self.eq_fail.items()):
            out.append(f"equalization at {GEN_NAMES[rate]} cannot pass phase {phase}")
        for name, q in sorted(self.tx_quirks.items()):
            if q.ts2_as_ts1:
                out.append(f"{name} transmitter never sends TS2 (sends TS1 instead)")
            if q.silent_until_ns is not None:
                out.append(f"{name} transmitter silent until {q.silent_until_ns} ns")
        return out
