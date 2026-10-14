"""One LTSSM instance (one end of a link) plus its Link Status fields.

Author: Farzana Akhter

How a port decides what to do
-----------------------------
``evaluate()`` is the only place where a port changes substate. It walks the
rows of ``transitions.TABLE_BY_SOURCE[current substate]`` in order and, for
each row whose role matches, calls ``cond_<row.event>()``. A condition
returns ``None`` (not now) or a human-readable reason (optionally with a
detail dict). The first row that returns a reason fires:

    1. ``act_<row.action>()`` runs, if the row has an action;
    2. the port enters ``row.dst`` and the trace records the reason and
       the row id;
    3. evaluation repeats, because some substates exit immediately.

``evaluate()`` is called whenever something a condition might look at has
changed: a burst arrived, a burst was sent, a timer expired, or software
issued a command. The conditions only *read* port state; everything that
changes state lives in the ``act_*`` methods and the entry hooks.

Link Status fields kept here mirror the real register: ``rate`` (Current
Link Speed), ``width`` (Negotiated Link Width), ``link_training`` and
``dl_link_active``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Tuple

from .config import GEN_NAMES, RATE_GTS, WIDTHS, Config, fmt_ms, format_lanes
from .engine import Engine
from .equalization import PHASE_DIRECTION, EqStatus, figure_of_merit, next_speed_step
from .ordered_sets import (
    TEST_PATTERN,
    EIEOS,
    EIOS,
    FTS,
    IDLE,
    SKP,
    LaneReceiver,
    OrderedSet,
    OSType,
    RxBurst,
)
from .states import (
    EQ_PHASES,
    RECOVERY_SUBSTATES,
    Substate as S,
    is_link_training,
)
from .trace import Trace
from .transitions import DSP, SUBSTATE_BEHAVIOR, TABLE_BY_SOURCE, USP, Transition

if TYPE_CHECKING:  # pragma: no cover
    from .link import Link

_CONST_OS = {
    OSType.EIOS: EIOS,
    OSType.EIEOS: EIEOS,
    OSType.IDLE: IDLE,
    OSType.TEST_PATTERN: TEST_PATTERN,
    OSType.FTS: FTS,
    OSType.SKP: SKP,
}
_RATES = {n: tuple(range(1, n + 1)) for n in range(1, 5)}

# Software commands and the substate that "uses them up".
_CONSUMED_ON_ENTRY = {
    S.HOT_RESET: ("hot_reset",),
    S.DISABLED: ("disable",),
    S.LOOPBACK_ENTRY: ("loopback",),
    S.LOOPBACK_EXIT: ("loopback_exit",),
    S.L2_IDLE: ("l23_ready",),
    S.DETECT_QUIET: ("enable", "power_restore", "hot_reset", "disable", "loopback", "retrain"),
}

COMMAND_TEXT = {
    "hot_reset": "Hot Reset requested (Secondary Bus Reset)",
    "disable": "Link Disable set",
    "enable": "Link Disable cleared",
    "loopback": "loopback requested",
    "loopback_exit": "loopback exit requested",
    "retrain": "Retrain Link bit set",
    "l23_ready": "PME_Turn_Off handshake done; preparing for power removal (L2/L3 Ready)",
    "power_restore": "main power restored",
}

Listener = Callable[["Port", S, S, Optional[str]], None]


class Port:
    """One LTSSM. ``role`` is "DSP" (downstream-facing) or "USP" (upstream-facing)."""

    def __init__(
        self,
        name: str,
        role: str,
        device: str,
        label: str,
        max_speed: int,
        max_width: int,
        config: Config,
        engine: Engine,
        trace: Trace,
        port_number: int = 0,
        target_link_speed: Optional[int] = None,
    ) -> None:
        if role not in (DSP, USP):
            raise ValueError(f"role must be DSP or USP, not {role!r}")
        self.name = name
        self.role = role
        self.device = device
        self.label = label
        self.max_speed = max_speed
        self.max_width = max_width
        self.port_number = port_number
        self.target_link_speed = target_link_speed or max_speed
        self.cfg = config
        self.engine = engine
        self.trace = trace
        self.link: Optional["Link"] = None
        self.listeners: List[Listener] = []
        self.hold_check: Optional[Callable[["Port"], Optional[str]]] = None

        # --- LTSSM bookkeeping
        self.substate = S.DETECT_QUIET
        self.gen = 0  # bumped on every entry; stale timers/ticks check it
        self.expired: set = set()
        self.started = False
        self._evaluating = False
        self._eq_was_set = False

        # --- Link Status / Capabilities mirror
        self.rate = 1
        self.width = 0
        self.active_lanes: List[int] = []
        self.link_num: Optional[int] = None
        self.lane_map: Dict[int, int] = {}
        self.lane_reversed = False
        self.dl_link_active = False
        self.partner_max_rate = 1
        self.partner_rates_seen: Optional[Tuple[int, ...]] = None
        self.eq: Dict[int, EqStatus] = {3: EqStatus(), 4: EqStatus()}
        self.eq_failures = {3: 0, 4: 0}
        self.rate_ceiling = 4
        self.held = False
        self._hold_reason = ""
        self.powered = True
        self.aspm_l1 = config.aspm_l1
        self.aspm_l0s = config.aspm_l0s

        # --- speed change / equalization state
        self.directed: Optional[str] = None
        self.directed_speed_change = False
        self.speed_target: Optional[int] = None
        self.prev_rate = 1
        self.next_rate: Optional[int] = None
        self.changed_speed = False
        self.revert_pending = False
        self._revert_why = ""
        self.eq_search: Optional[str] = None
        self.eq_trials: List[dict] = []
        self.eq_requested: Optional[str] = None
        self.eq_applied: Optional[str] = None
        self._eq_order: List[str] = []
        self._eq_index = 0
        self.eq_selected: Dict[str, str] = {}
        self.pm_l1_requested = False
        self.l1_cycles_done = 0
        self.pending_rx_error: Optional[Tuple[str, dict]] = None

        # --- receive / transmit bookkeeping (reset on every substate entry)
        self.rx: Dict[int, LaneReceiver] = {lane: LaneReceiver() for lane in range(max_width)}
        self.rx_activity = False
        self.rx_eios = False
        self.rx_first = False
        self.tx_count = 0
        self.tx_after_rx = 0
        self.bursts_sent = 0
        self._tx_started = False
        self._tx_cache: Optional[Dict[int, OrderedSet]] = None
        self.detect_result: Optional[List[int]] = None
        self._width_choice: Optional[Tuple[int, List[int]]] = None

        # --- counters
        self.detect_cycles = 0
        self.rx_errors = 0

    # ================================================================== setup
    def attach(self, link: "Link") -> None:
        self.link = link

    @property
    def link_name(self) -> str:
        return self.link.name if self.link else "-"

    @property
    def partner(self) -> Optional["Port"]:
        return self.link.partner(self) if self.link else None

    @property
    def width_text(self) -> str:
        if self.width:
            return f"x{self.width}"
        if self.active_lanes:
            return f"x{len(self.active_lanes)}"
        return "x0"

    @property
    def link_training(self) -> bool:
        return is_link_training(self.substate)

    def start(self) -> None:
        """Fundamental reset released: enter Detect.Quiet."""
        self.started = True
        self.trace.add(
            self.engine.now, "state", self.name, self.link_name, "Reset", S.DETECT_QUIET.value,
            "fundamental reset released (PERST# deasserted): LTSSM starts in Detect.Quiet",
            GEN_NAMES[self.rate], "x0", rule="init",
        )
        self.substate = S.DETECT_QUIET
        self._reset_for(S.DETECT_QUIET)
        self._on_enter(S.DETECT_QUIET, S.DETECT_QUIET)
        self._arm_main_timer()
        self.evaluate()

    # ============================================================== dispatch
    def evaluate(self) -> None:
        """Fire the first matching transition-table row; repeat until stable."""
        if self._evaluating or not self.started:
            return
        self._evaluating = True
        try:
            for _ in range(32):
                row, reason, detail = self._first_matching_row()
                if row is None:
                    break
                self._take(row, reason, detail)
            self._ensure_tx()
        finally:
            self._evaluating = False

    def _first_matching_row(self) -> Tuple[Optional[Transition], str, Optional[dict]]:
        for row in TABLE_BY_SOURCE.get(self.substate, ()):
            if not row.applies_to(self.role):
                continue
            result = getattr(self, "cond_" + row.event)()
            if result:
                if isinstance(result, tuple):
                    return row, result[0], result[1]
                return row, result, None
        return None, "", None

    def _take(self, row: Transition, reason: str, detail: Optional[dict]) -> None:
        if row.action:
            getattr(self, "act_" + row.action)()
        self._enter(row.dst, reason, row.rule_id, row.event, detail)

    def _enter(self, new: S, reason: str, rule: str, event: Optional[str], detail: Optional[dict]) -> None:
        old = self.substate
        self.substate = new
        self._reset_for(new)
        self.trace.add(
            self.engine.now, "state", self.name, self.link_name, old.value, new.value, reason,
            GEN_NAMES[self.rate], self.width_text, rule=rule, detail=detail,
        )
        self._on_enter(new, old)
        self._arm_main_timer()
        if self.link is not None:
            self.link.on_state_change(self, old, new, event)
        for listener in self.listeners:
            listener(self, old, new, event)

    # ====================================================== entry and timers
    def _reset_for(self, new: S) -> None:
        """Silent per-entry resets (done before the state event is logged)."""
        self.gen += 1
        self.expired = set()
        for rx in self.rx.values():
            rx.reset()
        self.rx_activity = False
        self.rx_eios = False
        self.rx_first = False
        self.tx_count = 0
        self.tx_after_rx = 0
        self.bursts_sent = 0
        self._tx_started = False
        self._tx_cache = None
        for cmd in _CONSUMED_ON_ENTRY.get(new, ()):
            if self.directed == cmd:
                self.directed = None
        if new is S.DETECT_QUIET:
            self._eq_was_set = any(st.any_set() for st in self.eq.values())
            self.rate = 1
            self.width = 0
            self.active_lanes = []
            self.lane_map = {}
            self.lane_reversed = False
            if self.role == USP:
                self.link_num = None
            else:
                self.link_num = self.port_number
            for st in self.eq.values():
                st.clear()
            self.eq_failures = {3: 0, 4: 0}
            self.rate_ceiling = 4
            self.directed_speed_change = False
            self.speed_target = None
            self.changed_speed = False
            self.revert_pending = False
            self.pm_l1_requested = False
            self.pending_rx_error = None
            self.detect_result = None
            reason = self.hold_check(self) if self.hold_check else None
            if reason:
                self.held = True
                self._hold_reason = reason

    def _on_enter(self, new: S, old: S) -> None:
        """Entry hooks that may log status events or start extra timers."""
        if new in (S.DETECT_QUIET, S.HOT_RESET, S.DISABLED, S.L2_IDLE):
            if self.dl_link_active:
                self._set_dl(False, f"DL_Down: link left L0 for {new.value}")
        if new is S.DETECT_QUIET:
            if getattr(self, "_eq_was_set", False):
                self._status(
                    "equalization status bits cleared (link went through Detect)",
                    {"flags": {"eq_8gt_complete": False, "eq_16gt_complete": False}},
                )
            if self.held:
                self._status(f"held in Detect.Quiet: {self._hold_reason}", {"held": True})
        elif new is S.L0:
            if not self.dl_link_active:
                self._start_timer("dl_active", self.cfg.dl_active_delay_ns)
            if self.role == DSP:
                self._start_timer("speed_check", self.cfg.speed_change_delay_ns)
            if self.role == USP and self.aspm_l1 and self.l1_cycles_done < self.cfg.aspm_l1_cycles:
                self._start_timer("aspm", self.cfg.aspm_l1_idle_ns)
            if self.aspm_l0s:
                self._start_timer("l0s", self.cfg.aspm_l0s_idle_ns)
        elif new is S.L1_IDLE:
            if self.role == DSP:
                self._start_timer("traffic", self.cfg.l1_residency_ns)
        elif new is S.REC_RCVRCFG:
            self.changed_speed = False
        elif new is S.REC_IDLE:
            self.speed_target = None
        elif (new is S.REC_EQ_PHASE2 and self.role == USP) or (new is S.REC_EQ_PHASE3 and self.role == DSP):
            self._start_eq_search()
        elif new is S.L1_ENTRY:
            pass

    def _arm_main_timer(self) -> None:
        beh = SUBSTATE_BEHAVIOR[self.substate]
        if beh.timeout is None:
            return
        if self.substate is S.DETECT_QUIET and self.held:
            return  # a held port does not time out of Detect.Quiet
        self._start_timer("timeout", getattr(self.cfg, beh.timeout))

    def _start_timer(self, name: str, delay_ns: int) -> None:
        self.engine.schedule(delay_ns, self._timer_fired, name, self.gen)

    def _timer_fired(self, name: str, gen: int) -> None:
        if gen != self.gen:
            return  # the port left the substate that started this timer
        if name == "dl_active":
            self._set_dl(True, "DL_Link_Active: data link layer initialized (timed flag in this model)")
            return
        if self.substate is S.DETECT_ACTIVE and name == "timeout" and self.link is not None:
            self.detect_result = self.link.detect_receivers(self)
        self.expired.add(name)
        self.evaluate()

    # ============================================================ transmit
    def _ensure_tx(self) -> None:
        beh = SUBSTATE_BEHAVIOR[self.substate]
        if beh.tx is None or self._tx_started:
            return
        self._tx_started = True
        self._send_burst()
        if beh.repeat:
            self.engine.schedule(self.cfg.burst_ns(self.rate), self._tick, self.gen)

    def _batch(self) -> int:
        """Long waits in one substate send the same ordered sets in bigger batches.

        The first ``burst_batch_after`` bursts of a substate are sent one by
        one (handshakes happen there). After that nothing new is being said,
        so bursts are batched ``burst_batch_factor`` times larger: the same
        ordered sets and counts, far fewer engine events.
        """
        return 1 if self.bursts_sent < self.cfg.burst_batch_after else self.cfg.burst_batch_factor

    def _tick(self, gen: int) -> None:
        if gen != self.gen:
            return
        beh = SUBSTATE_BEHAVIOR[self.substate]
        if beh.max_bursts is not None and self.bursts_sent >= getattr(self.cfg, beh.max_bursts):
            return  # L0: after a few idle bursts the data stream is not simulated
        batch = self._batch()
        self._send_burst(batch)
        self.evaluate()
        if gen == self.gen:
            self.engine.schedule(self.cfg.burst_ns(self.rate) * self._batch(), self._tick, gen)

    def _send_burst(self, batch: int = 1) -> None:
        beh = SUBSTATE_BEHAVIOR[self.substate]
        count = self.cfg.os_per_burst * batch if beh.repeat else 1
        if self._tx_cache is None:
            self._tx_cache = {lane: self._build_os(lane) for lane in self.active_lanes}
        if self.link is not None and self._tx_cache:
            self.link.transmit(self, self._tx_cache, count)
        self.bursts_sent += 1
        self.tx_count += count
        if self.rx_first:
            self.tx_after_rx += count

    def advertised_rates(self) -> Tuple[int, ...]:
        top = self.max_speed
        if self.role == DSP:
            top = min(top, self.target_link_speed)
            if self.directed_speed_change and self.speed_target:
                top = min(top, self.speed_target)
        return _RATES[top]

    def _build_os(self, lane: int) -> OrderedSet:
        sub = self.substate
        kind = SUBSTATE_BEHAVIOR[sub].tx
        if kind not in (OSType.TS1, OSType.TS2):
            return _CONST_OS[kind]
        numbered = sub not in (S.POLLING_ACTIVE, S.POLLING_CONFIGURATION, S.POLLING_COMPLIANCE)
        link_num = self.link_num if numbered else None
        lane_num = self.lane_map.get(lane) if numbered else None
        speed_change = self.directed_speed_change if sub in (S.REC_RCVRLOCK, S.REC_RCVRCFG) else None
        eq_phase = preset = None
        if kind is OSType.TS1 and self.rate >= 3 and sub in RECOVERY_SUBSTATES:
            eq_phase = EQ_PHASES.get(sub, 0)
            if sub in EQ_PHASES:
                preset = self._tx_preset()
        signalling = sub in (S.REC_RCVRLOCK, S.REC_RCVRCFG)
        hot_reset = True if (sub is S.HOT_RESET or (signalling and self.directed == "hot_reset")) else None
        disable = True if (sub is S.DISABLED or (signalling and self.directed == "disable")) else None
        loopback = (
            True
            if (sub in (S.LOOPBACK_ENTRY, S.LOOPBACK_ACTIVE) or (signalling and self.directed == "loopback"))
            else None
        )
        return OrderedSet(
            kind, link_num, lane_num, self.advertised_rates(), speed_change, eq_phase, preset,
            hot_reset, disable, loopback,
        )

    def _tx_preset(self) -> Optional[str]:
        requester = (self.substate is S.REC_EQ_PHASE2 and self.role == USP) or (
            self.substate is S.REC_EQ_PHASE3 and self.role == DSP
        )
        if requester:
            return self.eq_requested
        return self.eq_applied or self.cfg.eq_search_order[0]

    # ============================================================== receive
    def receive(self, burst: RxBurst) -> None:
        """Called by the Link one flight-delay after the partner sent a burst."""
        if not self.powered:
            return
        mismatch = burst.rate != self.rate
        last_good: Optional[OrderedSet] = None
        for lane, os, count, flags in burst.lanes:
            rx = self.rx.get(lane)
            if rx is None:
                continue
            if os.type is OSType.EIOS:
                self.rx_eios = True
                rx.break_run()
                continue
            if not (self.substate is S.DISABLED and os.disable):
                self.rx_activity = True  # TS1 with the Disable bit is not "training again"
            if mismatch:
                rx.break_run()  # cannot decode a different data rate
                continue
            rx.accept(os, count, flags)
            last_good = os
        if last_good is not None:
            beh = SUBSTATE_BEHAVIOR[self.substate]
            if beh.tx_after_rx_of is not None and last_good.type is beh.tx_after_rx_of:
                self.rx_first = True
            if last_good.rates:
                self.partner_rates_seen = last_good.rates
            sub = self.substate
            if (
                self.role == USP
                and sub in (S.REC_RCVRLOCK, S.REC_RCVRCFG)
                and last_good.speed_change
                and not self.directed_speed_change
                and self._can_go_faster(last_good)
            ):
                self.directed_speed_change = True  # USP echoes the speed_change bit
                self._tx_cache = None
            if last_good.preset and (
                (sub is S.REC_EQ_PHASE2 and self.role == DSP) or (sub is S.REC_EQ_PHASE3 and self.role == USP)
            ):
                if self.eq_applied != last_good.preset:
                    self.eq_applied = last_good.preset  # responder applies the requested preset
                    self._tx_cache = None
        self.evaluate()

    def _can_go_faster(self, os: OrderedSet) -> bool:
        return os.max_rate > self.rate and self.max_speed > self.rate

    # ------------------------------------------------------------ rx helpers
    def _lanes_matching(self, pred: Callable[[OrderedSet], bool], n: int, lanes: Optional[List[int]] = None) -> List[int]:
        out = []
        for lane in self.active_lanes if lanes is None else lanes:
            if self.rx[lane].achieved(pred, n) is not None:
                out.append(lane)
        return out

    def _achieved_os(self, lane: int, pred: Callable[[OrderedSet], bool], n: int) -> Optional[OrderedSet]:
        return self.rx[lane].achieved(pred, n)

    def _all_lanes(self, pred: Callable[[OrderedSet], bool], n: int) -> bool:
        if not self.active_lanes:
            return False
        return len(self._lanes_matching(pred, n)) == len(self.active_lanes)

    def _last_rx_text(self) -> str:
        for lane in self.active_lanes:
            if self.rx[lane].last is not None:
                return self.rx[lane].last.describe()
        return "nothing"

    @property
    def _n8(self) -> int:
        return self.cfg.rx_consecutive_required

    @property
    def _n2(self) -> int:
        return self.cfg.rx_consecutive_short

    def _lanes_text(self) -> str:
        return f"{len(self.active_lanes)} lane{'s' if len(self.active_lanes) != 1 else ''}"

    # ================================================== status and commands
    def _status(self, reason: str, detail: Optional[dict] = None) -> None:
        self.trace.add(
            self.engine.now, "status", self.name, self.link_name, self.substate.value,
            self.substate.value, reason, GEN_NAMES[self.rate], self.width_text, detail=detail,
        )

    def _set_dl(self, value: bool, reason: str) -> None:
        if self.dl_link_active == value:
            return
        self.dl_link_active = value
        self._status(reason, {"flags": {"dl_link_active": value}})

    def hold(self, reason: str) -> None:
        """Show the port as held in Detect.Quiet. A real switch keeps such a port
        in reset, which is why its receiver termination is high-impedance
        (the Link reports no receiver to the partner while ``held`` is set)."""
        if self.held:
            return
        self.held = True
        self._hold_reason = reason
        if self.substate is S.DETECT_QUIET:
            self.gen += 1
            self.expired = set()
        self._status(f"held in Detect.Quiet: {reason}", {"held": True})

    def release(self, reason: str) -> None:
        if not self.held:
            return
        self.held = False
        self._status(f"released: {reason}", {"held": False})
        if self.substate is S.DETECT_QUIET:
            self.gen += 1
            self.expired = set()
            self._arm_main_timer()
            self.evaluate()

    def direct(self, command: str, note: str = "", by: str = "software", **kwargs) -> None:
        """A directed command (Hot Reset, Disable, Retrain, ...) from software or the switch."""
        text = COMMAND_TEXT.get(command, command)
        if command == "set_target_speed":
            self.target_link_speed = int(kwargs["speed"])
            text = f"Target Link Speed set to {GEN_NAMES[self.target_link_speed]} and Retrain Link set"
            command = "retrain"
        self.directed = command
        detail = {"command": command, "by": by}
        if note:
            detail["note"] = note
        self._status(f"{by}: {text}", detail)
        self.evaluate()

    def inject_rx_error(self, reason: str, cause: str, lane: Optional[int] = None) -> None:
        if self.substate is not S.L0:
            return
        detail = {"cause": cause}
        if lane is not None:
            detail["lane"] = lane
        self.pending_rx_error = (reason, detail)
        self.evaluate()

    def _pm_request_l1(self) -> None:
        self.pm_l1_requested = True
        self.evaluate()

    # ======================================================== equalization
    def _start_eq_search(self) -> None:
        self.eq_search = None
        self.eq_trials = []
        self._eq_order = list(self.cfg.eq_search_order[: self.cfg.eq_max_presets_per_phase])
        self._eq_index = 0
        self.eq_requested = self._eq_order[0]
        self._tx_cache = None
        self.engine.schedule(self.cfg.eq_preset_eval_ns, self._eq_evaluate, self.gen)

    def _eq_evaluate(self, gen: int) -> None:
        if gen != self.gen or self.link is None:
            return
        phase = EQ_PHASES[self.substate]
        preset = self.eq_requested or self._eq_order[0]
        penalty = self.link.eq_penalty(self.rate, phase)
        fom = figure_of_merit(self.rate, preset, penalty, self.link.rng)
        target = self.cfg.eq_fom_target
        ok = fom >= target
        partner = self.partner
        pname = partner.name if partner else "partner"
        self.eq_trials.append({"preset": preset, "fom": fom, "accepted": ok})
        detail = {"phase": phase, "preset": preset, "fom": fom, "target": target, "accepted": ok}
        if penalty:
            detail["cause"] = f"eq_fail fault at {GEN_NAMES[self.rate]} phase {phase}"
        self.trace.add(
            self.engine.now, "eq", self.name, self.link_name, self.substate.value, self.substate.value,
            f"Phase {phase}: {pname} TX preset {preset} -> FOM {fom} "
            f"{'>=' if ok else '<'} target {target}: {'accepted' if ok else 'rejected'}",
            GEN_NAMES[self.rate], self.width_text, detail=detail,
        )
        if ok:
            self.eq_search = "pass"
            self.eq_selected[f"{GEN_NAMES[self.rate]}.phase{phase}"] = preset
        else:
            self._eq_index += 1
            if self._eq_index >= len(self._eq_order):
                self.eq_search = "fail"
            else:
                self.eq_requested = self._eq_order[self._eq_index]
                self._tx_cache = None
                self.engine.schedule(self.cfg.eq_preset_eval_ns, self._eq_evaluate, gen)
        self.evaluate()

    def _eq_trials_text(self) -> str:
        return ", ".join(f"{t['preset']}={t['fom']}" for t in self.eq_trials)

    # ======================================================================
    # CONDITIONS - one per event name in the transition table.
    # Each returns None, a reason string, or (reason, detail).
    # They read state only.
    # ======================================================================
    def _timeout(self) -> bool:
        return "timeout" in self.expired

    def _timeout_text(self, field: str) -> str:
        return fmt_ms(getattr(self.cfg, field))

    # --- Detect
    def cond_quiet_timeout(self):
        if self._timeout() and not self.held and self.powered:
            return f"Detect.Quiet timer expired ({self._timeout_text('detect_quiet_ns')})"
        return None

    def cond_rx_ei_exit(self):
        if self.rx_activity and not self.held:
            what = {
                S.DETECT_QUIET: "Electrical Idle exit detected: the partner is already transmitting",
                S.POLLING_COMPLIANCE: "Electrical Idle exit detected: the partner started transmitting",
                S.L1_IDLE: "Electrical Idle exit detected: the partner is leaving L1 (TS1 received)",
                S.DISABLED: "Electrical Idle exit detected: the partner is training again",
            }
            return what.get(self.substate, "Electrical Idle exit detected")
        return None

    def cond_receiver_detected(self):
        if self._timeout() and self.detect_result:
            lanes = self.detect_result
            return (
                f"receiver termination detected on lanes {format_lanes(lanes)} ({len(lanes)} of {self.max_width})",
                {"lanes_detected": format_lanes(lanes)},
            )
        return None

    def cond_no_receiver(self):
        if self._timeout() and not self.detect_result:
            return "no receiver termination detected on any lane"
        return None

    # --- Polling
    def cond_polling_active_done(self):
        need_tx = self.cfg.polling_active_min_ts1_tx
        if self.tx_count >= need_tx and self._all_lanes(lambda o: o.is_training, self._n8):
            return f"sent {self.tx_count} TS1 (>= {need_tx}); received {self._n8} consecutive TS1/TS2 on all {self._lanes_text()}"
        return None

    def _polling_ok_lanes(self) -> List[int]:
        return self._lanes_matching(lambda o: o.is_training, self._n8)

    def cond_polling_timeout_partial(self):
        if not self._timeout():
            return None
        ok = self._polling_ok_lanes()
        if ok and len(ok) < len(self.active_lanes):
            missing = sorted(set(self.active_lanes) - set(ok))
            return (
                f"Polling.Active timeout ({self._timeout_text('polling_active_ns')}): lanes {format_lanes(ok)} "
                f"trained, lanes {format_lanes(missing)} never received TS1/TS2 and are dropped",
                {"lanes_dropped": format_lanes(missing)},
            )
        return None

    def cond_polling_timeout_silent(self):
        if self._timeout() and self.cfg.silent_partner_to_polling_compliance and not self.rx_activity:
            return (
                f"Polling.Active timeout ({self._timeout_text('polling_active_ns')}): receiver detected "
                f"but no lane ever left Electrical Idle"
            )
        return None

    def cond_polling_timeout(self):
        if self._timeout():
            return (
                f"Polling.Active timeout ({self._timeout_text('polling_active_ns')}) without 8 consecutive "
                f"TS1/TS2 on any lane; last received: {self._last_rx_text()}"
            )
        return None

    def cond_polling_config_done(self):
        need = self.cfg.polling_cfg_min_ts2_tx_after_rx
        if self.tx_after_rx >= need and self._all_lanes(lambda o: o.type is OSType.TS2, self._n8):
            return f"received {self._n8} consecutive TS2 on all {self._lanes_text()}; sent {self.tx_after_rx} TS2 after the first one"
        return None

    def cond_polling_config_timeout(self):
        if self._timeout():
            return (
                f"Polling.Configuration timeout ({self._timeout_text('polling_configuration_ns')}) without "
                f"{self._n8} consecutive TS2; last received: {self._last_rx_text()}",
                {"last_rx": self._last_rx_text()},
            )
        return None

    # --- Configuration
    def cond_rx_link_number_echo(self):
        lanes = self._lanes_matching(lambda o: o.type is OSType.TS1 and o.link_num == self.link_num, self._n2)
        if lanes:
            return f"link number {self.link_num} echoed by the partner on lanes {format_lanes(lanes)}"
        return None

    def cond_rx_link_number(self):
        lanes = self._lanes_matching(lambda o: o.type is OSType.TS1 and o.link_num is not None, self._n2)
        if lanes:
            num = self._achieved_os(lanes[0], lambda o: o.type is OSType.TS1 and o.link_num is not None, self._n2).link_num
            return f"received {self._n2} TS1 with link number {num} on lanes {format_lanes(lanes)}"
        return None

    def cond_linkwidth_start_timeout(self):
        if self._timeout():
            return (
                f"Configuration.Linkwidth.Start timeout ({self._timeout_text('cfg_linkwidth_start_ns')}); "
                f"last received: {self._last_rx_text()}"
            )
        return None

    def _choose_width(self) -> Optional[Tuple[int, List[int]]]:
        echo = set(self._lanes_matching(lambda o: o.type is OSType.TS1 and o.link_num == self.link_num, self._n2))
        if not echo:
            return None
        for w in WIDTHS:
            if w <= self.max_width and all(lane in echo for lane in range(w)):
                return w, sorted(echo)
        return None

    def cond_width_selected(self):
        choice = self._choose_width()
        if choice is None:
            return None
        w, echo = choice
        text = f"link number echoed on lanes {format_lanes(echo)}; width x{w} selected (widest supported width starting at lane 0)"
        if w < self.max_width:
            text += f"; port supports x{self.max_width}"
        return text, {"width": f"x{w}", "lanes_echoed": format_lanes(echo)}

    def cond_rx_lane_numbers(self):
        lanes = self._lanes_matching(lambda o: o.type is OSType.TS1 and o.lane_num is not None, self._n2)
        if lanes:
            return f"received lane numbers on lanes {format_lanes(lanes)}"
        return None

    def cond_rx_lane_numbers_echo(self):
        if self._all_lanes(lambda o: o.type is OSType.TS1 and o.lane_num is not None, self._n2):
            return f"partner echoes lane numbers on all {self._lanes_text()}"
        return None

    def cond_rx_lane_numbers_stable(self):
        if self._all_lanes(lambda o: o.type is OSType.TS1 and o.lane_num is not None, self._n2):
            return f"lane numbers stable on all {self._lanes_text()}"
        return None

    def cond_lane_numbers_match(self):
        def ok(lane: int) -> bool:
            want = self.lane_map.get(lane)
            return self._achieved_os(lane, lambda o: o.type is OSType.TS1 and o.lane_num == want, self._n2) is not None

        if self.active_lanes and all(ok(lane) for lane in self.active_lanes):
            return f"echoed lane numbers match the proposal on all {self._lanes_text()} (x{self.width})"
        return None

    def cond_rx_ts2(self):
        if self._all_lanes(lambda o: o.type is OSType.TS2, self._n2):
            return f"received {self._n2} consecutive TS2 on all {self._lanes_text()}"
        return None

    def cond_cfg_complete_done(self):
        need = self.cfg.cfg_complete_min_ts2_tx_after_rx
        if self.tx_after_rx >= need and self._all_lanes(lambda o: o.type is OSType.TS2, self._n8):
            rates = self.partner_rates_seen or (1,)
            return (
                f"received {self._n8} consecutive TS2 and sent {self.tx_after_rx} after the first; "
                f"partner advertises up to {GEN_NAMES[max(rates)]} ({RATE_GTS[max(rates)]})"
            )
        return None

    def cond_rx_idle_done(self):
        need = self.cfg.idle_min_tx_after_rx
        if self.tx_after_rx >= need and self._all_lanes(lambda o: o.type is OSType.IDLE, self._n8):
            return (
                f"received {self._n8} consecutive Idle symbols and sent {self.tx_after_rx} after the first: "
                f"link up at {GEN_NAMES[self.rate]} ({RATE_GTS[self.rate]}) x{self.width}"
            )
        return None

    def cond_config_timeout(self):
        if self._timeout():
            return (
                f"{self.substate.value} timeout ({self._timeout_text('cfg_substate_timeout_ns')}); "
                f"last received: {self._last_rx_text()}"
            )
        return None

    # --- L0 and directed commands
    def _directed_reason(self, command: str, where: str) -> Optional[str]:
        if self.directed == command:
            return f"{COMMAND_TEXT[command]}: {where}"
        return None

    def cond_directed_hot_reset(self):
        if self.substate is S.L0:
            return self._directed_reason("hot_reset", "enter Recovery to signal it with TS1s")
        return self._directed_reason("hot_reset", "sending TS1 with the Hot Reset bit")

    def cond_directed_disable(self):
        if self.substate is S.L0:
            return self._directed_reason("disable", "enter Recovery to signal it with TS1s")
        return self._directed_reason("disable", "sending TS1 with the Disable bit")

    def cond_directed_loopback(self):
        if self.substate is S.L0:
            return self._directed_reason("loopback", "enter Recovery to signal it with TS1s")
        return self._directed_reason("loopback", "sending TS1 with the Loopback bit")

    def cond_directed_retrain(self):
        return self._directed_reason("retrain", "retraining the link through Recovery")

    def cond_directed_enable(self):
        return self._directed_reason("enable", "retrain from Detect")

    def cond_directed_l23_ready(self):
        return self._directed_reason("l23_ready", "entering L2")

    def cond_power_restored(self):
        return self._directed_reason("power_restore", "retrain from Detect")

    def cond_directed_loopback_exit(self):
        return self._directed_reason("loopback_exit", "leaving loopback")

    def cond_receiver_error(self):
        if self.pending_rx_error is not None:
            return self.pending_rx_error
        return None

    def cond_rx_ts_in_l0(self):
        lanes = self._lanes_matching(lambda o: o.is_training, self._n2)
        if lanes:
            last = self._achieved_os(lanes[0], lambda o: o.is_training, self._n2)
            text = f"received {last.type.value} while in L0: the partner entered Recovery"
            if last.speed_change:
                text += " to change speed"
            return text
        return None

    def cond_speed_change_wanted(self):
        if "speed_check" not in self.expired:
            return None
        step = next_speed_step(self, self.cfg.gen2_step)
        if step is None:
            return None
        return step[1]

    def cond_aspm_l1_idle(self):
        if "aspm" in self.expired and self.aspm_l1 and self.l1_cycles_done < self.cfg.aspm_l1_cycles:
            return f"ASPM L1: link idle for {fmt_ms(self.cfg.aspm_l1_idle_ns)}; requesting L1 (PM_Active_State_Request_L1, symbolic)"
        return None

    def cond_partner_requested_l1(self):
        if self.pm_l1_requested:
            return "partner requested ASPM L1; acknowledging (PM_Request_Ack, symbolic) and sending EIOS"
        return None

    def cond_aspm_l0s_idle(self):
        if "l0s" in self.expired and self.aspm_l0s:
            return f"ASPM L0s: transmitter idle for {fmt_ms(self.cfg.aspm_l0s_idle_ns)}"
        return None

    # --- L0s / L1
    def cond_eios_sent(self):
        if self._timeout():
            return "EIOS sent; transmitter in Electrical Idle"
        return None

    def cond_l0s_traffic_pending(self):
        if self._timeout():
            return "data to send again: sending FTS"
        return None

    def cond_fts_sent(self):
        if self.tx_count >= self.cfg.n_fts:
            return f"sent {self.tx_count} FTS (N_FTS = {self.cfg.n_fts}); back to L0"
        return None

    def cond_l1_settled(self):
        if self._timeout() and self.rx_eios:
            return "EIOS sent and received; both directions in Electrical Idle"
        return None

    def cond_traffic_pending(self):
        if "traffic" in self.expired:
            return f"host has traffic after {fmt_ms(self.cfg.l1_residency_ns)} in L1: waking the link through Recovery"
        return None

    # --- Recovery
    def cond_eq_required(self):
        if self.changed_speed and self.rate >= 3 and not self.eq[self.rate].complete:
            role_text = "starts in Phase 0" if self.role == USP else "starts in Phase 1 (Phase 0 is the USP's)"
            return f"now at {RATE_GTS[self.rate]} without equalization at this rate: equalization {role_text}"
        return None

    def _ts_ok_for_lock(self, o: OrderedSet) -> bool:
        return (
            o.is_training
            and o.link_num == self.link_num
            and bool(o.speed_change) == self.directed_speed_change
        )

    def cond_rx_8_ts(self):
        if self._all_lanes(self._ts_ok_for_lock, self._n8):
            text = f"received {self._n8} consecutive TS1/TS2 on all {self._lanes_text()} at {GEN_NAMES[self.rate]}"
            if self.directed_speed_change:
                text += " with speed_change=1"
            return text
        return None

    def cond_lock_timeout_after_speed_change(self):
        if self._timeout() and self.changed_speed:
            return f"no lock at {RATE_GTS[self.rate]} within {self._timeout_text('recovery_rcvrlock_ns')} after the rate change: revert"
        return None

    def cond_rcvrlock_timeout(self):
        if self._timeout():
            return (
                f"Recovery.RcvrLock timeout ({self._timeout_text('recovery_rcvrlock_ns')}): no lock; "
                f"last received: {self._last_rx_text()}; link down"
            )
        return None

    def _rx_bit(self, bit: str) -> List[int]:
        return self._lanes_matching(lambda o: o.type is OSType.TS1 and bool(getattr(o, bit)), self._n2)

    def cond_rx_hot_reset(self):
        if self._rx_bit("hot_reset"):
            return f"received {self._n2} TS1 with the Hot Reset bit"
        return None

    def cond_rx_disable(self):
        if self._rx_bit("disable"):
            return f"received {self._n2} TS1 with the Disable bit"
        return None

    def cond_rx_loopback(self):
        if self._rx_bit("loopback"):
            return f"received {self._n2} TS1 with the Loopback bit"
        return None

    def cond_rx_loopback_echo(self):
        if self._rx_bit("loopback"):
            return "the follower echoes TS1 with the Loopback bit"
        return None

    def cond_loopback_entered(self):
        if self._timeout():
            return "loopback path set up: echoing received data"
        return None

    def cond_rx_eios(self):
        if self.rx_eios:
            return "EIOS received"
        return None

    def cond_loopback_exit_done(self):
        if self._timeout():
            return "loopback exit complete"
        return None

    def cond_rx_8_ts2_speed_change(self):
        need = self.cfg.rcvrcfg_min_ts2_tx_after_rx
        if (
            self.directed_speed_change
            and self.tx_after_rx >= need
            and self._all_lanes(lambda o: o.type is OSType.TS2 and bool(o.speed_change), self._n8)
        ):
            rates = self.partner_rates_seen or (1,)
            new = min(max(self.advertised_rates()), max(rates))
            return (
                f"received {self._n8} TS2 with speed_change=1 on all {self._lanes_text()}; "
                f"highest common advertised rate is {GEN_NAMES[new]} ({RATE_GTS[new]})"
            )
        return None

    def cond_rx_8_ts2(self):
        need = self.cfg.rcvrcfg_min_ts2_tx_after_rx
        if self.tx_after_rx >= need and self._all_lanes(lambda o: o.type is OSType.TS2, self._n8):
            text = f"received {self._n8} consecutive TS2 on all {self._lanes_text()}"
            if self.directed_speed_change:
                text += "; partner did not echo speed_change, staying at the current rate"
            return text
        return None

    def cond_rcvrcfg_timeout(self):
        if self._timeout():
            return f"Recovery.RcvrCfg timeout ({self._timeout_text('recovery_rcvrcfg_ns')}); link down"
        return None

    def cond_speed_ei_done(self):
        if self._timeout() and not self.revert_pending and self.next_rate:
            return (
                f"electrical idle done; switching {GEN_NAMES[self.rate]} -> {GEN_NAMES[self.next_rate]} "
                f"({RATE_GTS[self.next_rate]})"
            )
        return None

    def cond_speed_ei_done_revert(self):
        if self._timeout() and self.revert_pending:
            return (
                f"electrical idle done; reverting {GEN_NAMES[self.rate]} -> {GEN_NAMES[self.prev_rate]} "
                f"({RATE_GTS[self.prev_rate]}): {self._revert_why}"
            )
        return None

    # --- Equalization
    def _rx_ec(self, ec: int):
        if self._all_lanes(lambda o: o.type is OSType.TS1 and o.eq_phase == ec, self._n2):
            return f"received {self._n2} TS1 with EC={ec:02b} on all {self._lanes_text()}"
        return None

    def cond_rx_ec_00(self):
        r = self._rx_ec(0)
        return f"{r}: the DSP finished Phase 3" if r else None

    def cond_rx_ec_01(self):
        r = self._rx_ec(1)
        return f"{r}: partner is in Phase 1" if r else None

    def cond_rx_ec_10(self):
        r = self._rx_ec(2)
        return f"{r}: the DSP is in Phase 2" if r else None

    def cond_rx_ec_11(self):
        r = self._rx_ec(3)
        return f"{r}: the USP finished Phase 2" if r else None

    def cond_preset_search_passed(self):
        if self.eq_search == "pass":
            phase = EQ_PHASES[self.substate]
            last = self.eq_trials[-1]
            pname = self.partner.name if self.partner else "partner"
            return (
                f"Phase {phase} done ({PHASE_DIRECTION[phase]}): {pname} TX preset {last['preset']} gives "
                f"FOM {last['fom']} >= {self.cfg.eq_fom_target} after {len(self.eq_trials)} trial(s)",
                {"phase": phase, "trials": list(self.eq_trials)},
            )
        return None

    def cond_preset_search_failed(self):
        if self.eq_search == "fail":
            phase = EQ_PHASES[self.substate]
            best = max(self.eq_trials, key=lambda t: t["fom"])
            detail = {"phase": phase, "trials": list(self.eq_trials)}
            penalty = self.link.eq_penalty(self.rate, phase) if self.link else 0
            if penalty:
                detail["cause"] = f"eq_fail fault at {GEN_NAMES[self.rate]} phase {phase}"
            return (
                f"Phase {phase} FAILED at {RATE_GTS[self.rate]} ({PHASE_DIRECTION[phase]}): none of "
                f"{len(self.eq_trials)} presets reached FOM {self.cfg.eq_fom_target} "
                f"(best {best['preset']} = {best['fom']}); reverting to the previous rate",
                detail,
            )
        return None

    def cond_partner_abandoned_eq(self):
        if self.rx_eios:
            phase = EQ_PHASES[self.substate]
            return f"EIOS received during Phase {phase}: the partner gave up on equalization at {RATE_GTS[self.rate]}; reverting"
        return None

    def cond_eq_phase_timeout(self):
        if self._timeout():
            return f"{self.substate.value} timeout: equalization stuck at {RATE_GTS[self.rate]}; reverting"
        return None

    def cond_recovery_idle_timeout(self):
        if self._timeout():
            return f"Recovery.Idle timeout ({self._timeout_text('recovery_idle_ns')}); link down"
        return None

    def cond_hot_reset_done(self):
        if self._timeout():
            return f"Hot Reset timer expired ({self._timeout_text('hot_reset_ns')}): retrain from Detect"
        return None

    # ======================================================================
    # ACTIONS - named in the transition table, run before entering dst.
    # ======================================================================
    def act_use_detected_lanes(self) -> None:
        self.active_lanes = list(self.detect_result or [])

    def act_count_detect_cycle(self) -> None:
        self.detect_cycles += 1
        if self.link is not None:
            self.link.counters.detect_cycles += 1

    def act_drop_silent_lanes(self) -> None:
        self.active_lanes = self._polling_ok_lanes()

    def act_adopt_link_number(self) -> None:
        lanes = self._lanes_matching(lambda o: o.type is OSType.TS1 and o.link_num is not None, self._n2)
        self.link_num = self._achieved_os(
            lanes[0], lambda o: o.type is OSType.TS1 and o.link_num is not None, self._n2
        ).link_num

    def act_assign_lane_numbers(self) -> None:
        choice = self._choose_width()
        assert choice is not None
        w, _ = choice
        self.width = w
        self.active_lanes = list(range(w))
        self.lane_map = {lane: lane for lane in range(w)}

    def act_accept_lane_numbers(self) -> None:
        lanes = self._lanes_matching(lambda o: o.type is OSType.TS1 and o.lane_num is not None, self._n2)
        pred = lambda o: o.type is OSType.TS1 and o.lane_num is not None  # noqa: E731
        self.lane_map = {lane: self._achieved_os(lane, pred, self._n2).lane_num for lane in lanes}
        self.active_lanes = sorted(lanes)
        self.width = len(lanes)
        first = self.active_lanes[0]
        self.lane_reversed = self.width > 1 and self.lane_map[first] == self.width - 1
        if self.lane_reversed:
            self._status(
                f"lane reversal applied: physical lane {first} carries logical lane {self.lane_map[first]}",
                {"lane_reversal": True, "lane_map": {str(k): v for k, v in sorted(self.lane_map.items())}},
            )

    def act_record_partner_rates(self) -> None:
        rates = self.partner_rates_seen or (1,)
        self.partner_max_rate = max(rates)

    def act_consume_retrain(self) -> None:
        self.directed = None

    def act_count_receiver_error(self) -> None:
        self.rx_errors += 1
        if self.link is not None:
            self.link.counters.rx_errors += 1
        self.pending_rx_error = None

    def act_follow_partner_into_recovery(self) -> None:
        lanes = self._lanes_matching(lambda o: o.is_training, self._n2)
        last = self._achieved_os(lanes[0], lambda o: o.is_training, self._n2) if lanes else None
        if self.role == USP and last is not None and last.speed_change and self._can_go_faster(last):
            self.directed_speed_change = True

    def act_start_speed_change(self) -> None:
        step = next_speed_step(self, self.cfg.gen2_step)
        assert step is not None
        self.directed_speed_change = True
        self.speed_target = step[0]

    def act_request_l1(self) -> None:
        self.l1_cycles_done += 1
        partner = self.partner
        if partner is not None:
            self.engine.schedule(0, partner._pm_request_l1)

    def act_ack_l1(self) -> None:
        self.pm_l1_requested = False

    def act_begin_eq(self) -> None:
        st = self.eq[self.rate]
        st.phase1 = st.phase2 = st.phase3 = False
        self.eq_applied = None
        self.eq_requested = None
        if self.role == DSP and self.link is not None:
            self.link.counters.eq_attempts[GEN_NAMES[self.rate]] += 1

    def act_mark_phase1_ok(self) -> None:
        self.eq[self.rate].phase1 = True

    def act_mark_phase2_ok(self) -> None:
        self.eq[self.rate].phase2 = True

    def act_eq_complete(self) -> None:
        st = self.eq[self.rate]
        st.phase3 = True
        st.complete = True
        flag = "eq_8gt_complete" if self.rate == 3 else "eq_16gt_complete"
        self._status(
            f"Equalization {RATE_GTS[self.rate]} Complete+ (Phase1+ Phase2+ Phase3+)",
            {"flags": {flag: True}},
        )

    def act_eq_failed(self) -> None:
        rate = self.rate
        gname = GEN_NAMES[rate]
        phase = EQ_PHASES.get(self.substate, -1)
        self.eq_failures[rate] += 1
        self.eq[rate].request = True
        self.revert_pending = True
        self._revert_why = f"equalization at {RATE_GTS[rate]} failed in phase {phase}"
        if self.role != DSP or self.link is None:
            return
        self.link.counters.eq_failures[gname] += 1
        n = self.eq_failures[rate]
        if n >= self.cfg.eq_max_attempts and self.rate_ceiling >= rate:
            self.rate_ceiling = rate - 1
            self._status(
                f"rate ceiling lowered to {GEN_NAMES[rate - 1]}: equalization at {gname} ({RATE_GTS[rate]}) "
                f"failed {n} time(s) (phase {phase}); {gname} and above will not be tried again",
                {"rate_ceiling": GEN_NAMES[rate - 1], "failed_rate": gname, "phase": phase},
            )
        else:
            self._status(
                f"equalization attempt {n} of {self.cfg.eq_max_attempts} at {gname} failed (phase {phase}); "
                f"will retry",
                {"failed_rate": gname, "phase": phase, "attempt": n},
            )

    def act_speed_change_failed(self) -> None:
        rate = self.rate
        self.revert_pending = True
        self._revert_why = f"could not lock at {RATE_GTS[rate]}"
        if self.role == DSP and self.rate_ceiling >= rate:
            self.rate_ceiling = rate - 1
            self._status(
                f"rate ceiling lowered to {GEN_NAMES[rate - 1]}: no lock at {GEN_NAMES[rate]}",
                {"rate_ceiling": GEN_NAMES[rate - 1], "failed_rate": GEN_NAMES[rate]},
            )

    def act_negotiate_new_rate(self) -> None:
        self.prev_rate = self.rate
        rates = self.partner_rates_seen or (1,)
        self.next_rate = min(max(self.advertised_rates()), max(rates))

    def act_switch_to_new_rate(self) -> None:
        old = self.rate
        self.rate = self.next_rate or old
        self.changed_speed = True
        self.directed_speed_change = False
        self.speed_target = None
        if self.role == DSP and self.link is not None:
            self.link.counters.speed_changes += 1
        self._status_after_rate_change(old, "data rate switched")

    def act_revert_rate(self) -> None:
        old = self.rate
        self.rate = self.prev_rate
        self.revert_pending = False
        self.changed_speed = False
        self.directed_speed_change = False
        self.speed_target = None
        self._status_after_rate_change(old, f"data rate reverted ({self._revert_why})")

    def _status_after_rate_change(self, old: int, what: str) -> None:
        self._status(
            f"{what}: {GEN_NAMES[old]} ({RATE_GTS[old]}) -> {GEN_NAMES[self.rate]} ({RATE_GTS[self.rate]})",
            {"rate_from": GEN_NAMES[old], "rate_to": GEN_NAMES[self.rate]},
        )

    # ======================================================================
    # Summary
    # ======================================================================
    def final_summary(self) -> dict:
        up = self.substate is S.L0 or self.dl_link_active
        return {
            "name": self.name,
            "role": self.role,
            "device": self.device,
            "label": self.label,
            "link": self.link_name,
            "state": self.substate.value,
            "speed": GEN_NAMES[self.rate],
            "width": self.width_text if up else "x0",
            "link_training": self.link_training,
            "dl_link_active": self.dl_link_active,
            "max_speed": GEN_NAMES[self.max_speed],
            "max_width": f"x{self.max_width}",
            "target_link_speed": GEN_NAMES[self.target_link_speed],
            "lane_reversed": self.lane_reversed,
            "eq_8gt_complete": self.eq[3].complete,
            "eq_16gt_complete": self.eq[4].complete,
            "eq": {"Gen3": self.eq[3].to_dict(), "Gen4": self.eq[4].to_dict()},
            "eq_presets": dict(sorted(self.eq_selected.items())),
            "rate_ceiling": GEN_NAMES[self.rate_ceiling],
            "rx_errors": self.rx_errors,
            "detect_cycles": self.detect_cycles,
            "held": self.held,
            "aspm_l1": self.aspm_l1,
        }
