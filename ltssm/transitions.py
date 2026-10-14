"""The LTSSM transition table. Start reading the model here.

Author: Farzana Akhter

Every LTSSM decision in the model comes from one row of ``TRANSITIONS``.
``Port.evaluate()`` (port.py) looks up the rows whose ``src`` matches the
port's current substate, in table order, and asks the port whether the row's
``event`` condition holds (method ``Port.cond_<event>``). The first row whose
condition holds fires: the optional ``action`` runs (``Port.act_<action>``),
the port enters ``dst``, and the trace records the row id in ``rule``.
There is no second, hidden if-chain: if a transition is not in this table,
the model cannot take it.

Column meaning
--------------
rule_id  Stable id printed in every trace event ("rule": "T05").
src      Substate(s) the row applies to.
role     "DSP" (downstream-facing port: root port, switch downstream port),
         "USP" (upstream-facing port: switch upstream port, endpoint) or "any".
event    Name of the condition the port evaluates (Port.cond_<event>).
guard    The condition in words.
dst      Next substate.
action   Optional bookkeeping done on the way (Port.act_<action>).
rule     The behavior the row models, in one sentence.
optional True when no built-in scenario exercises the row; such rows are
         covered by unit tests in tests/test_transitions.py instead.

Row order matters inside one source substate: earlier rows win. For example
in L0 a directed Hot Reset is checked before a receiver error, and both
before a voluntary speed change.

What each substate *transmits* and which timer it starts is in
``SUBSTATE_BEHAVIOR`` at the bottom of this file.

The rules are SIMPLIFIED and are not taken from the PCI-SIG specification
text; see docs/guide/how-it-works.md for the list of simplifications.

Run ``python -m ltssm table`` to print this table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Union

from .ordered_sets import OSType
from .states import Substate as S

DSP, USP, ANY = "DSP", "USP", "any"

SrcSpec = Union[S, Tuple[S, ...]]


@dataclass(frozen=True)
class Transition:
    rule_id: str
    src: Tuple[S, ...]
    event: str
    guard: str
    dst: S
    rule: str
    role: str = ANY
    action: Optional[str] = None
    optional: bool = False

    def applies_to(self, role: str) -> bool:
        return self.role == ANY or self.role == role


def T(
    rule_id: str,
    src: SrcSpec,
    event: str,
    dst: S,
    guard: str,
    rule: str,
    role: str = ANY,
    action: Optional[str] = None,
    optional: bool = False,
) -> Transition:
    """Compact row constructor so the table below reads like a table."""
    src_tuple = src if isinstance(src, tuple) else (src,)
    return Transition(rule_id, src_tuple, event, guard, dst, rule, role, action, optional)


_EQ_ALL = (S.REC_EQ_PHASE0, S.REC_EQ_PHASE1, S.REC_EQ_PHASE2, S.REC_EQ_PHASE3)
_CFG_AFTER_START = (
    S.CFG_LINKWIDTH_ACCEPT,
    S.CFG_LANENUM_WAIT,
    S.CFG_LANENUM_ACCEPT,
    S.CFG_COMPLETE,
    S.CFG_IDLE,
)

# fmt: off
TRANSITIONS: List[Transition] = [
    # ======================================================================
    # DETECT - is there a receiver on the other end of each lane?
    # ======================================================================
    T("T01", S.DETECT_QUIET, "quiet_timeout", S.DETECT_ACTIVE,
      guard="Detect.Quiet timer expired (12 ms) and the port is not held in reset",
      rule="A port waits in Detect.Quiet with its transmitter in Electrical Idle, then tries receiver detection."),
    T("T02", S.DETECT_QUIET, "rx_ei_exit", S.DETECT_ACTIVE,
      guard="a lane left Electrical Idle (the partner is already transmitting) and the port is not held",
      rule="If the partner is already sending, there is no need to wait for the quiet timer."),
    T("T03", S.DETECT_ACTIVE, "receiver_detected", S.POLLING_ACTIVE,
      action="use_detected_lanes",
      guard="receiver termination seen on at least one lane",
      rule="Somebody is connected: train on the lanes where a receiver was found."),
    T("T04", S.DETECT_ACTIVE, "no_receiver", S.DETECT_QUIET,
      action="count_detect_cycle",
      guard="no receiver termination on any lane",
      rule="Nobody answered (empty slot, card not powered or not seated, open lanes): wait and try again."),

    # ======================================================================
    # POLLING - bit/symbol lock at 2.5 GT/s, TS1 then TS2 exchange
    # ======================================================================
    T("T05", S.POLLING_ACTIVE, "polling_active_done", S.POLLING_CONFIGURATION,
      guard="sent >= 1024 TS1 AND received 8 consecutive TS1/TS2 on every lane being trained",
      rule="Both sides hear each other on all lanes; move on to confirm with TS2."),
    T("T06", S.POLLING_ACTIVE, "polling_timeout_partial", S.POLLING_CONFIGURATION,
      action="drop_silent_lanes", optional=True,
      guard="24 ms timeout; some lanes received 8 TS1/TS2, others never did",
      rule="Train on the lanes that work and drop the rest; the link will come up narrower."),
    T("T07", S.POLLING_ACTIVE, "polling_timeout_silent", S.POLLING_COMPLIANCE,
      optional=True,
      guard="24 ms timeout; a receiver was detected but nothing was ever received (silent_partner_to_polling_compliance)",
      rule="A partner that terminates but never talks looks like a passive test load: send the Polling.Compliance test pattern."),
    T("T08", S.POLLING_ACTIVE, "polling_timeout", S.DETECT_QUIET,
      optional=True,
      guard="24 ms timeout and neither rule above applies",
      rule="Polling made no progress; start over from Detect."),
    T("T09", S.POLLING_COMPLIANCE, "rx_ei_exit", S.POLLING_ACTIVE,
      optional=True,
      guard="the partner started transmitting (Electrical Idle exit)",
      rule="Stop sending the test pattern as soon as the partner wakes up."),
    T("T10", S.POLLING_CONFIGURATION, "polling_config_done", S.CFG_LINKWIDTH_START,
      guard="received 8 consecutive TS2 on all lanes AND sent >= 16 TS2 after the first TS2 arrived",
      rule="Both sides confirmed with TS2; start configuring link and lane numbers."),
    T("T11", S.POLLING_CONFIGURATION, "polling_config_timeout", S.DETECT_QUIET,
      guard="48 ms timeout without 8 consecutive TS2 on all lanes",
      rule="The partner never confirmed with TS2; give up and restart from Detect."),

    # ======================================================================
    # CONFIGURATION - link number, lane numbers, width
    # The DSP (downstream-facing port) leads; the USP follows and echoes.
    # ======================================================================
    T("T12", S.CFG_LINKWIDTH_START, "rx_link_number_echo", S.CFG_LINKWIDTH_ACCEPT,
      role=DSP,
      guard="DSP sends TS1 with its link number; 2 consecutive TS1 came back with that number on at least one lane",
      rule="The DSP proposes a link number and waits for the USP to echo it."),
    T("T13", S.CFG_LINKWIDTH_START, "rx_link_number", S.CFG_LINKWIDTH_ACCEPT,
      role=USP, action="adopt_link_number",
      guard="2 consecutive TS1 with a non-PAD link number on at least one lane",
      rule="The USP accepts the DSP's link number and starts echoing it."),
    T("T14", S.CFG_LINKWIDTH_START, "linkwidth_start_timeout", S.DETECT_QUIET,
      guard="24 ms timeout",
      rule="No usable link number exchange; restart from Detect."),
    T("T15", S.CFG_LINKWIDTH_ACCEPT, "width_selected", S.CFG_LANENUM_WAIT,
      role=DSP, action="assign_lane_numbers",
      guard="the link number was echoed; choose the widest supported width whose lanes 0..w-1 all echoed it",
      rule="Width is decided here: x16, x8, x4, x2 or x1 starting at lane 0. Missing lanes shrink the link."),
    T("T16", S.CFG_LINKWIDTH_ACCEPT, "rx_lane_numbers", S.CFG_LANENUM_WAIT,
      role=USP, action="accept_lane_numbers",
      guard="2 consecutive TS1 with non-PAD lane numbers",
      rule="The USP learns its lane numbers; if lane 0 carries the highest number it applies lane reversal."),
    T("T17", S.CFG_LANENUM_WAIT, "rx_lane_numbers_echo", S.CFG_LANENUM_ACCEPT,
      role=DSP,
      guard="2 consecutive TS1 with non-PAD lane numbers on every configured lane",
      rule="The USP is echoing lane numbers; check them."),
    T("T18", S.CFG_LANENUM_WAIT, "rx_lane_numbers_stable", S.CFG_LANENUM_ACCEPT,
      role=USP,
      guard="lane numbers stable on every configured lane for 2 consecutive TS1",
      rule="The proposal is stable; accept it."),
    T("T19", S.CFG_LANENUM_ACCEPT, "lane_numbers_match", S.CFG_COMPLETE,
      role=DSP,
      guard="the echoed lane number on every configured lane matches what the DSP proposed",
      rule="Lane numbering agreed; confirm with TS2."),
    T("T20", S.CFG_LANENUM_ACCEPT, "rx_ts2", S.CFG_COMPLETE,
      role=USP,
      guard="2 consecutive TS2 on every configured lane",
      rule="The DSP moved to Configuration.Complete; follow it."),
    T("T21", S.CFG_COMPLETE, "cfg_complete_done", S.CFG_IDLE,
      action="record_partner_rates",
      guard="received 8 consecutive TS2 AND sent >= 16 TS2 after the first one arrived",
      rule="Numbers confirmed and data rates exchanged (the partner's highest rate is remembered)."),
    T("T22", S.CFG_IDLE, "rx_idle_done", S.L0,
      guard="received 8 consecutive Idle data symbols AND sent >= 16 Idle symbols after the first one arrived",
      rule="Link is up at 2.5 GT/s. DL_Link_Active follows shortly after."),
    T("T23", _CFG_AFTER_START, "config_timeout", S.DETECT_QUIET,
      optional=True,
      guard="2 ms timeout in a Configuration substate (simplified)",
      rule="Configuration stalled; restart from Detect."),

    # ======================================================================
    # L0 - link up. Every way out of L0 is listed here, highest priority first.
    # ======================================================================
    T("T24", S.L0, "directed_hot_reset", S.REC_RCVRLOCK,
      role=DSP,
      guard="software asked for Hot Reset (Secondary Bus Reset) on this downstream port",
      rule="Hot Reset is signaled with TS1s, so the DSP first goes through Recovery."),
    T("T25", S.L0, "directed_disable", S.REC_RCVRLOCK,
      role=DSP, optional=True,
      guard="software set Link Disable",
      rule="Link Disable is signaled with TS1s, so the DSP first goes through Recovery."),
    T("T26", S.L0, "directed_loopback", S.REC_RCVRLOCK,
      role=DSP, optional=True,
      guard="a test asked for loopback",
      rule="Loopback is requested with TS1s from Recovery."),
    T("T27", S.L0, "directed_retrain", S.REC_RCVRLOCK,
      role=DSP, action="consume_retrain", optional=True,
      guard="software set the Retrain Link bit",
      rule="Retrain Link forces a pass through Recovery (this is what 'setpci ... Retrain' does)."),
    T("T28", S.L0, "receiver_error", S.REC_RCVRLOCK,
      action="count_receiver_error",
      guard="the receiver saw an error it cannot ride through (framing error, lost block alignment, loss of signal)",
      rule="Errors in L0 send the link to Recovery to re-acquire lock. Each one is a 'flap'."),
    T("T29", S.L0, "rx_ts_in_l0", S.REC_RCVRLOCK,
      action="follow_partner_into_recovery",
      guard="2 consecutive TS1/TS2 received while in L0",
      rule="Training sets in L0 mean the partner entered Recovery; follow it."),
    T("T30", S.L0, "speed_change_wanted", S.REC_RCVRLOCK,
      role=DSP, action="start_speed_change",
      guard="both sides support a higher rate than the current one and the speed policy allows the next step",
      rule="A directed speed change: the DSP enters Recovery with the speed_change bit set in its TS1s."),
    T("T31", S.L0, "aspm_l1_idle", S.L1_ENTRY,
      role=USP, action="request_l1",
      guard="ASPM L1 enabled and the link has been idle for aspm_l1_idle_ns",
      rule="The upstream port asks to enter L1 (the PM DLLP handshake is symbolic in this model)."),
    T("T32", S.L0, "partner_requested_l1", S.L1_ENTRY,
      role=DSP, action="ack_l1",
      guard="the USP requested ASPM L1",
      rule="The DSP acknowledges and both sides send EIOS."),
    T("T33", S.L0, "aspm_l0s_idle", S.L0S_ENTRY,
      optional=True,
      guard="ASPM L0s enabled and the transmitter has been idle for aspm_l0s_idle_ns",
      rule="L0s is a transmit-side low-power state; the partner stays in L0 (simplified)."),
    T("T34", S.L0, "directed_l23_ready", S.L2_IDLE,
      optional=True,
      guard="the system prepared for power removal (PME_Turn_Off handshake, symbolic)",
      rule="L2 is the main-power-off path; minimal in this model."),

    # ======================================================================
    # L0s (transmitter side, minimal)
    # ======================================================================
    T("T35", S.L0S_ENTRY, "eios_sent", S.L0S_IDLE,
      optional=True,
      guard="EIOS sent and the transmitter settled in Electrical Idle",
      rule="Transmitter is idle."),
    T("T36", S.L0S_IDLE, "l0s_traffic_pending", S.L0S_FTS,
      optional=True,
      guard="there is data to send again",
      rule="Wake the receiver with Fast Training Sequences."),
    T("T37", S.L0S_FTS, "fts_sent", S.L0,
      optional=True,
      guard="N_FTS Fast Training Sequences sent",
      rule="Back to L0 without a full Recovery."),

    # ======================================================================
    # L1 (ASPM, minimal). L1 is always exited through Recovery.
    # ======================================================================
    T("T38", S.L1_ENTRY, "l1_settled", S.L1_IDLE,
      guard="EIOS sent, EIOS received from the partner, and Electrical Idle settled",
      rule="Both transmitters are idle; the link is in L1."),
    T("T39", S.L1_IDLE, "traffic_pending", S.REC_RCVRLOCK,
      role=DSP,
      guard="the host has traffic for the device",
      rule="Leaving L1 requires Recovery to re-acquire bit lock."),
    T("T40", S.L1_IDLE, "rx_ei_exit", S.REC_RCVRLOCK,
      guard="the partner left Electrical Idle (it is sending TS1)",
      rule="The partner is waking the link; follow it into Recovery."),
    T("T41", S.L2_IDLE, "power_restored", S.DETECT_QUIET,
      optional=True,
      guard="main power restored / wake event",
      rule="Coming back from L2 is a full retrain from Detect."),

    # ======================================================================
    # RECOVERY.RCVRLOCK - re-acquire bit/symbol (or block) lock
    # ======================================================================
    T("T42", S.REC_RCVRLOCK, "eq_required", S.REC_EQ_PHASE0,
      role=USP, action="begin_eq",
      guard="the rate just changed to 8.0 or 16.0 GT/s and equalization at this rate is not complete",
      rule="The upstream port starts equalization in Phase 0."),
    T("T43", S.REC_RCVRLOCK, "eq_required", S.REC_EQ_PHASE1,
      role=DSP, action="begin_eq",
      guard="the rate just changed to 8.0 or 16.0 GT/s and equalization at this rate is not complete",
      rule="The downstream port starts equalization in Phase 1 (Phase 0 belongs to the USP)."),
    T("T44", S.REC_RCVRLOCK, "rx_8_ts", S.REC_RCVRCFG,
      guard="8 consecutive TS1/TS2 on all configured lanes with matching link/lane numbers and speed_change bit",
      rule="Lock re-acquired; confirm configuration with TS2."),
    T("T45", S.REC_RCVRLOCK, "lock_timeout_after_speed_change", S.REC_SPEED,
      action="speed_change_failed", optional=True,
      guard="RcvrLock timeout right after a rate change",
      rule="The new rate does not work; go back to the previous rate."),
    T("T46", S.REC_RCVRLOCK, "rcvrlock_timeout", S.DETECT_QUIET,
      guard="RcvrLock timeout (2 ms, simplified) with no lock",
      rule="Recovery failed: the link goes down (DL_Down) and retrains from Detect."),

    # ======================================================================
    # RECOVERY.RCVRCFG - confirm with TS2; decide what comes next
    # ======================================================================
    T("T47", S.REC_RCVRCFG, "directed_hot_reset", S.HOT_RESET,
      role=DSP,
      guard="Hot Reset was requested; TS1s now carry the Hot Reset bit",
      rule="Directed Hot Reset (entry point simplified to RcvrCfg in this model)."),
    T("T48", S.REC_RCVRCFG, "rx_hot_reset", S.HOT_RESET,
      role=USP,
      guard="2 consecutive TS1 with the Hot Reset bit",
      rule="The partner is resetting this link; follow it."),
    T("T49", S.REC_RCVRCFG, "directed_disable", S.DISABLED,
      role=DSP, optional=True,
      guard="Link Disable was requested",
      rule="Send TS1 with the Disable bit, then go idle."),
    T("T50", S.REC_RCVRCFG, "rx_disable", S.DISABLED,
      role=USP, optional=True,
      guard="2 consecutive TS1 with the Disable bit",
      rule="The partner disabled the link."),
    T("T51", S.REC_RCVRCFG, "directed_loopback", S.LOOPBACK_ENTRY,
      role=DSP, optional=True,
      guard="loopback was requested (this port becomes loopback lead)",
      rule="Send TS1 with the Loopback bit."),
    T("T52", S.REC_RCVRCFG, "rx_loopback", S.LOOPBACK_ENTRY,
      role=USP, optional=True,
      guard="2 consecutive TS1 with the Loopback bit",
      rule="Become the loopback follower."),
    T("T53", S.REC_RCVRCFG, "rx_8_ts2_speed_change", S.REC_SPEED,
      action="negotiate_new_rate",
      guard="speed change requested by both sides: 8 consecutive TS2 with speed_change=1 AND >= 16 TS2 sent",
      rule="Both agree to change speed. The new rate is the highest rate both advertised."),
    T("T54", S.REC_RCVRCFG, "rx_8_ts2", S.REC_IDLE,
      guard="8 consecutive TS2 on all lanes AND >= 16 TS2 sent after the first one arrived",
      rule="Nothing else to do; return to L0 through Recovery.Idle."),
    T("T55", S.REC_RCVRCFG, "rcvrcfg_timeout", S.DETECT_QUIET,
      optional=True,
      guard="RcvrCfg timeout (2 ms, simplified)",
      rule="Recovery failed; the link goes down."),

    # ======================================================================
    # RECOVERY.SPEED - electrical idle while the rate switches
    # ======================================================================
    T("T56", S.REC_SPEED, "speed_ei_done", S.REC_RCVRLOCK,
      action="switch_to_new_rate",
      guard="electrical idle time elapsed and the speed change was negotiated",
      rule="Switch to the new rate and re-acquire lock there."),
    T("T57", S.REC_SPEED, "speed_ei_done_revert", S.REC_RCVRLOCK,
      action="revert_rate",
      guard="electrical idle time elapsed after a failed speed change or failed equalization",
      rule="Fall back to the rate that worked before."),

    # ======================================================================
    # RECOVERY.EQUALIZATION - 8.0 and 16.0 GT/s only
    #   Phase 0: USP sends with the preset the DSP told it to use.
    #   Phase 1: both exchange TS1 and confirm they can hear each other.
    #   Phase 2: the USP tunes the DSP's transmitter (USP receiver judges).
    #   Phase 3: the DSP tunes the USP's transmitter (DSP receiver judges).
    # The port that judges the eye is the "requester"; the other is the
    # "responder" and simply applies the presets it is asked for.
    # ======================================================================
    T("T58", S.REC_EQ_PHASE0, "rx_ec_01", S.REC_EQ_PHASE1,
      role=USP,
      guard="2 consecutive TS1 with EC=01 on all lanes (the DSP is in Phase 1)",
      rule="USP has lock with its starting preset; join Phase 1."),
    T("T59", S.REC_EQ_PHASE1, "rx_ec_01", S.REC_EQ_PHASE2,
      role=DSP, action="mark_phase1_ok",
      guard="2 consecutive TS1 with EC=01 on all lanes (the USP is in Phase 1)",
      rule="Phase 1 done; the DSP moves to Phase 2 and waits for the USP's requests."),
    T("T60", S.REC_EQ_PHASE1, "rx_ec_10", S.REC_EQ_PHASE2,
      role=USP, action="mark_phase1_ok",
      guard="2 consecutive TS1 with EC=10 on all lanes (the DSP is in Phase 2)",
      rule="Phase 1 done; the USP starts tuning the DSP transmitter."),
    T("T61", S.REC_EQ_PHASE2, "preset_search_passed", S.REC_EQ_PHASE3,
      role=USP, action="mark_phase2_ok",
      guard="a requested DSP transmitter preset reached the figure-of-merit target",
      rule="Downstream direction (DSP TX -> USP RX) is tuned."),
    T("T62", S.REC_EQ_PHASE2, "preset_search_failed", S.REC_SPEED,
      role=USP, action="eq_failed",
      guard="no preset reached the target within eq_max_presets_per_phase trials",
      rule="Equalization failed in Phase 2: go back to the previous rate."),
    T("T63", S.REC_EQ_PHASE2, "rx_ec_11", S.REC_EQ_PHASE3,
      role=DSP, action="mark_phase2_ok",
      guard="2 consecutive TS1 with EC=11 (the USP finished Phase 2)",
      rule="The DSP now tunes the USP transmitter."),
    T("T64", S.REC_EQ_PHASE3, "preset_search_passed", S.REC_RCVRLOCK,
      role=DSP, action="eq_complete",
      guard="a requested USP transmitter preset reached the figure-of-merit target",
      rule="Upstream direction tuned; equalization complete at this rate."),
    T("T65", S.REC_EQ_PHASE3, "preset_search_failed", S.REC_SPEED,
      role=DSP, action="eq_failed",
      guard="no preset reached the target within eq_max_presets_per_phase trials",
      rule="Equalization failed in Phase 3: go back to the previous rate."),
    T("T66", S.REC_EQ_PHASE3, "rx_ec_00", S.REC_RCVRLOCK,
      role=USP, action="eq_complete",
      guard="2 consecutive TS1 with EC=00 (the DSP left equalization)",
      rule="Equalization complete at this rate."),
    T("T67", S.REC_EQ_PHASE2, "partner_abandoned_eq", S.REC_SPEED,
      role=DSP, action="eq_failed",
      guard="EIOS received: the USP gave up on Phase 2",
      rule="The responder follows the requester back to the previous rate."),
    T("T68", S.REC_EQ_PHASE3, "partner_abandoned_eq", S.REC_SPEED,
      role=USP, action="eq_failed",
      guard="EIOS received: the DSP gave up on Phase 3",
      rule="The responder follows the requester back to the previous rate."),
    T("T69", _EQ_ALL, "eq_phase_timeout", S.REC_SPEED,
      action="eq_failed", optional=True,
      guard="phase timer expired (12 ms for phases 0/1, 24 ms for 2/3, simplified)",
      rule="A stuck equalization phase counts as an equalization failure."),

    # ======================================================================
    # RECOVERY.IDLE
    # ======================================================================
    T("T70", S.REC_IDLE, "rx_idle_done", S.L0,
      guard="received 8 consecutive Idle data symbols AND sent >= 16 Idle symbols after the first one arrived",
      rule="Back in L0, at whatever rate Recovery ended on."),
    T("T71", S.REC_IDLE, "recovery_idle_timeout", S.DETECT_QUIET,
      optional=True,
      guard="Recovery.Idle timeout (2 ms, simplified)",
      rule="Recovery failed; the link goes down."),

    # ======================================================================
    # HOT RESET, DISABLED, LOOPBACK (minimal, directed by software or a test)
    # ======================================================================
    T("T72", S.HOT_RESET, "hot_reset_done", S.DETECT_QUIET,
      guard="2 ms Hot Reset timer expired",
      rule="After Hot Reset the link retrains from Detect, starting again at 2.5 GT/s."),
    T("T73", S.DISABLED, "directed_enable", S.DETECT_QUIET,
      role=DSP, optional=True,
      guard="software cleared Link Disable",
      rule="Re-enabled links retrain from Detect."),
    T("T74", S.DISABLED, "rx_ei_exit", S.DETECT_QUIET,
      role=USP, optional=True,
      guard="the partner left Electrical Idle (anything other than TS1 with the Disable bit)",
      rule="The partner is training again; follow it."),
    T("T75", S.LOOPBACK_ENTRY, "rx_loopback_echo", S.LOOPBACK_ACTIVE,
      role=DSP, optional=True,
      guard="2 consecutive TS1 with the Loopback bit came back",
      rule="The follower is looping data back."),
    T("T76", S.LOOPBACK_ENTRY, "loopback_entered", S.LOOPBACK_ACTIVE,
      role=USP, optional=True,
      guard="loopback path set up",
      rule="Start echoing received data."),
    T("T77", S.LOOPBACK_ACTIVE, "directed_loopback_exit", S.LOOPBACK_EXIT,
      role=DSP, optional=True,
      guard="the test finished",
      rule="The lead ends loopback with EIOS."),
    T("T78", S.LOOPBACK_ACTIVE, "rx_eios", S.LOOPBACK_EXIT,
      role=USP, optional=True,
      guard="EIOS received",
      rule="The lead ended loopback."),
    T("T79", S.LOOPBACK_EXIT, "loopback_exit_done", S.DETECT_QUIET,
      optional=True,
      guard="exit timer expired",
      rule="After loopback the link retrains from Detect."),
]
# fmt: on


def _index(rows: List[Transition]) -> Dict[S, List[Transition]]:
    by_src: Dict[S, List[Transition]] = {}
    for row in rows:
        for src in row.src:
            by_src.setdefault(src, []).append(row)
    return by_src


#: Rows grouped by source substate, preserving table order (priority).
TABLE_BY_SOURCE: Dict[S, List[Transition]] = _index(TRANSITIONS)

#: Rows by id, used to validate traces ("rule": "T05").
TABLE_BY_ID: Dict[str, Transition] = {row.rule_id: row for row in TRANSITIONS}


# ---------------------------------------------------------------------------
# What a port does while it sits in each substate
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Behavior:
    tx: Optional[OSType]  # what the transmitter sends (None = Electrical Idle)
    repeat: bool = True  # keep sending bursts (False = one burst, then idle)
    timeout: Optional[str] = None  # Config field holding the substate timer
    tx_after_rx_of: Optional[OSType] = None  # count TX after the first RX of this type
    max_bursts: Optional[str] = None  # Config field: stop after this many bursts (L0 idle)
    note: str = ""


# fmt: off
SUBSTATE_BEHAVIOR: Dict[S, Behavior] = {
    S.DETECT_QUIET:          Behavior(None, timeout="detect_quiet_ns", note="transmitter in Electrical Idle"),
    S.DETECT_ACTIVE:         Behavior(None, timeout="detect_active_ns", note="receiver-detection pulse on every lane"),
    S.POLLING_ACTIVE:        Behavior(OSType.TS1, timeout="polling_active_ns", note="TS1 with PAD link/lane numbers"),
    S.POLLING_COMPLIANCE:    Behavior(OSType.TEST_PATTERN, note="Polling.Compliance test pattern"),
    S.POLLING_CONFIGURATION: Behavior(OSType.TS2, timeout="polling_configuration_ns", tx_after_rx_of=OSType.TS2,
                                      note="TS2 with PAD link/lane numbers"),
    S.CFG_LINKWIDTH_START:   Behavior(OSType.TS1, timeout="cfg_linkwidth_start_ns", note="DSP: TS1 with link number; USP: PAD"),
    S.CFG_LINKWIDTH_ACCEPT:  Behavior(OSType.TS1, timeout="cfg_substate_timeout_ns", note="TS1 with link number"),
    S.CFG_LANENUM_WAIT:      Behavior(OSType.TS1, timeout="cfg_substate_timeout_ns", note="TS1 with link and lane numbers"),
    S.CFG_LANENUM_ACCEPT:    Behavior(OSType.TS1, timeout="cfg_substate_timeout_ns", note="TS1 with link and lane numbers"),
    S.CFG_COMPLETE:          Behavior(OSType.TS2, timeout="cfg_substate_timeout_ns", tx_after_rx_of=OSType.TS2,
                                      note="TS2 with link/lane numbers and supported rates"),
    S.CFG_IDLE:              Behavior(OSType.IDLE, timeout="cfg_substate_timeout_ns", tx_after_rx_of=OSType.IDLE,
                                      note="Idle data"),
    S.L0:                    Behavior(OSType.IDLE, max_bursts="l0_idle_bursts", note="data/idle (only the first few bursts are simulated)"),
    S.L0S_ENTRY:             Behavior(OSType.EIOS, repeat=False, timeout="l1_entry_settle_ns", note="EIOS, then idle"),
    S.L0S_IDLE:              Behavior(None, timeout="l0s_residency_ns", note="transmitter in Electrical Idle"),
    S.L0S_FTS:               Behavior(OSType.FTS, note="Fast Training Sequences"),
    S.L1_ENTRY:              Behavior(OSType.EIOS, repeat=False, timeout="l1_entry_settle_ns", note="EIOS, then idle"),
    S.L1_IDLE:               Behavior(None, note="both directions in Electrical Idle"),
    S.L2_IDLE:               Behavior(None, note="main power off (minimal)"),
    S.REC_RCVRLOCK:          Behavior(OSType.TS1, timeout="recovery_rcvrlock_ns", note="TS1 (speed_change bit if directed)"),
    S.REC_RCVRCFG:           Behavior(OSType.TS2, timeout="recovery_rcvrcfg_ns", tx_after_rx_of=OSType.TS2, note="TS2"),
    S.REC_SPEED:             Behavior(OSType.EIOS, repeat=False, timeout="recovery_speed_ei_ns",
                                      note="EIOS, Electrical Idle, rate switch"),
    S.REC_EQ_PHASE0:         Behavior(OSType.TS1, timeout="eq_phase01_timeout_ns", note="TS1 EC=00 with the starting preset"),
    S.REC_EQ_PHASE1:         Behavior(OSType.TS1, timeout="eq_phase01_timeout_ns", note="TS1 EC=01"),
    S.REC_EQ_PHASE2:         Behavior(OSType.TS1, timeout="eq_phase23_timeout_ns", note="TS1 EC=10 with requested/applied preset"),
    S.REC_EQ_PHASE3:         Behavior(OSType.TS1, timeout="eq_phase23_timeout_ns", note="TS1 EC=11 with requested/applied preset"),
    S.REC_IDLE:              Behavior(OSType.IDLE, timeout="recovery_idle_ns", tx_after_rx_of=OSType.IDLE, note="Idle data"),
    S.HOT_RESET:             Behavior(OSType.TS1, timeout="hot_reset_ns", note="TS1 with the Hot Reset bit"),
    S.DISABLED:              Behavior(OSType.TS1, max_bursts="disabled_ts1_bursts",
                                      note="a few bursts of TS1 with the Disable bit, then Electrical Idle"),
    S.LOOPBACK_ENTRY:        Behavior(OSType.TS1, timeout="loopback_exit_ns", note="TS1 with the Loopback bit"),
    S.LOOPBACK_ACTIVE:       Behavior(OSType.TS1, note="looped-back data (represented by TS1 with the Loopback bit)"),
    S.LOOPBACK_EXIT:         Behavior(OSType.EIOS, repeat=False, timeout="loopback_exit_ns", note="EIOS"),
}
# fmt: on


def table_as_text() -> str:
    """Plain-text rendering for ``python -m ltssm table``."""
    lines = []
    for row in TRANSITIONS:
        src = " | ".join(s.value for s in row.src)
        role = "" if row.role == ANY else f" [{row.role}]"
        opt = "  (optional)" if row.optional else ""
        lines.append(f"{row.rule_id}{role}  {src}  --{row.event}-->  {row.dst.value}{opt}")
        lines.append(f"       when: {row.guard}")
        if row.action:
            lines.append(f"       action: {row.action}")
        lines.append(f"       why:  {row.rule}")
    return "\n".join(lines)


def table_as_markdown() -> str:
    """Markdown rendering used for docs/guide/transition-table.md."""
    out = [
        "# Transition table",
        "",
        "This file is produced by `python -m ltssm table --markdown` from",
        "`ltssm/transitions.py`. A test checks that it stays in sync.",
        "",
        "Role: DSP = downstream-facing port (root port, switch downstream port);",
        "USP = upstream-facing port (switch upstream port, endpoint).",
        "Optional rows are not used by the built-in scenarios; unit tests cover them.",
        "",
        "| Rule | From | Role | Event | When | To | Action | Why |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in TRANSITIONS:
        src = "<br>".join(s.value for s in row.src)
        rid = row.rule_id + (" (opt)" if row.optional else "")
        out.append(
            f"| {rid} | {src} | {row.role} | `{row.event}` | {row.guard} | {row.dst.value} "
            f"| {row.action or ''} | {row.rule} |"
        )
    out.append("")
    out.append("## What each substate transmits")
    out.append("")
    out.append("| Substate | Transmits | Timer |")
    out.append("|---|---|---|")
    for sub, beh in SUBSTATE_BEHAVIOR.items():
        timer = f"`{beh.timeout}`" if beh.timeout else ""
        out.append(f"| {sub.value} | {beh.note} | {timer} |")
    out.append("")
    return "\n".join(out)
