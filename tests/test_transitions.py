"""Unit tests for the transition table and for the port's dispatch through it.

Author: Farzana Akhter

* The table is well formed (ids, substates, roles, conditions, actions).
* Every non-optional row is exercised by the built-in scenarios.
* Every optional row is exercised by one of the small EXERCISES below
  (Disabled, Loopback, L0s, L2, Polling.Compliance, timeouts...).
* The port really dispatches through the table: removing a row changes
  behavior.
* docs/guide/transition-table.md matches the table.
"""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path

import pytest
from helpers import assert_link, assert_sequence, make_scenario, rules_fired, run, run_custom

from ltssm.config import Config
from ltssm.port import Port
from ltssm.scenario import list_scenarios
from ltssm.states import Substate
from ltssm.transitions import (
    ANY,
    DSP,
    SUBSTATE_BEHAVIOR,
    TABLE_BY_ID,
    TABLE_BY_SOURCE,
    TRANSITIONS,
    USP,
    table_as_markdown,
)

ROOT = Path(__file__).resolve().parent.parent

# Small targeted runs that reach the optional rows (point-to-point link).
EXERCISES = {
    "silent_lanes_drop_to_x8": (
        make_scenario("X01", faults=[{"type": "silent_lanes", "link": "A", "lanes": "8-15"}], duration_ms=50),
        {"T06"},
    ),
    "silent_partner_polling_compliance": (
        make_scenario("X02", faults=[{"type": "partner_silent", "port": "EP", "until_ms": 40}], duration_ms=70),
        {"T07", "T09"},
    ),
    "noisy_lanes_polling_timeout": (
        make_scenario("X03", faults=[{"type": "lane_errors", "link": "A", "lanes": "0-15",
                                      "corrupt_rate": 0.9, "rates": ["Gen1"]}], duration_ms=40),
        {"T08"},
    ),
    "configuration_timeout": (
        make_scenario("X04", faults=[{"type": "connection_lost", "link": "A", "at_ms": 12.069}], duration_ms=20),
        {"T23"},
    ),
    "rcvrcfg_timeout": (
        make_scenario("X05", faults=[{"type": "connection_lost", "link": "A", "at_ms": 12.0955}], duration_ms=20),
        {"T55"},
    ),
    "recovery_idle_timeout": (
        make_scenario("X06", faults=[{"type": "connection_lost", "link": "A", "at_ms": 12.902}], duration_ms=20),
        {"T71"},
    ),
    "equalization_phase_timeout": (
        make_scenario("X07", faults=[{"type": "connection_lost", "link": "A", "at_ms": 12.3}], duration_ms=50),
        {"T69"},
    ),
    "no_lock_after_speed_change": (
        make_scenario("X08", faults=[{"type": "capability", "port": "EP", "max_speed": "Gen2"},
                                     {"type": "lane_errors", "link": "A", "lanes": "0-15",
                                      "corrupt_rate": 1.0, "rates": ["Gen2"]}], duration_ms=30),
        {"T45"},
    ),
    "disable_and_enable": (
        make_scenario("X09", commands=[{"at_ms": 20, "port": "RP", "command": "disable"},
                                       {"at_ms": 25, "port": "RP", "command": "enable"}], duration_ms=60),
        {"T25", "T49", "T50", "T73", "T74"},
    ),
    "loopback": (
        make_scenario("X10", commands=[{"at_ms": 20, "port": "RP", "command": "loopback"},
                                       {"at_ms": 21, "port": "RP", "command": "loopback_exit"}], duration_ms=40),
        {"T26", "T51", "T52", "T75", "T76", "T77", "T78", "T79"},
    ),
    "retrain_link": (
        make_scenario("X11", commands=[{"at_ms": 20, "port": "RP", "command": "retrain"}], duration_ms=25),
        {"T27"},
    ),
    "aspm_l0s": (make_scenario("X12", config={"aspm_l0s": True}, duration_ms=20), {"T33", "T35", "T36", "T37"}),
    "l2_and_power_restore": (
        make_scenario("X13", commands=[{"at_ms": 20, "port": p, "command": "l23_ready"} for p in ("RP", "EP")]
                      + [{"at_ms": 25, "port": p, "command": "power_restore"} for p in ("RP", "EP")],
                      duration_ms=60),
        {"T34", "T41"},
    ),
}


# ---------------------------------------------------------------------------
# table structure
# ---------------------------------------------------------------------------
def test_rule_ids_are_unique_and_ordered():
    ids = [r.rule_id for r in TRANSITIONS]
    assert len(ids) == len(set(ids))
    assert ids == sorted(ids), "keep rule ids in table order so traces are easy to cross-reference"


def test_every_row_names_existing_conditions_and_actions():
    for row in TRANSITIONS:
        assert callable(getattr(Port, "cond_" + row.event, None)), f"{row.rule_id}: no Port.cond_{row.event}"
        if row.action:
            assert callable(getattr(Port, "act_" + row.action, None)), f"{row.rule_id}: no Port.act_{row.action}"
        assert row.role in (DSP, USP, ANY)
        assert row.guard and row.rule, f"{row.rule_id} needs a guard and a rule comment"
        assert all(isinstance(s, Substate) for s in row.src) and isinstance(row.dst, Substate)


def test_every_substate_has_behavior_and_valid_timer_names():
    names = {f.name for f in fields(Config)}
    for sub in Substate:
        assert sub in SUBSTATE_BEHAVIOR, f"no behavior for {sub}"
        beh = SUBSTATE_BEHAVIOR[sub]
        if beh.timeout:
            assert beh.timeout in names, f"{sub}: unknown config field {beh.timeout}"
        if beh.max_bursts:
            assert beh.max_bursts in names


def test_every_substate_can_be_entered_and_left():
    targets = {r.dst for r in TRANSITIONS} | {Substate.DETECT_QUIET}
    sources = set(TABLE_BY_SOURCE)
    for sub in Substate:
        assert sub in targets, f"{sub} can never be entered"
        assert sub in sources or sub is Substate.L2_IDLE, f"{sub} has no way out"


def test_role_specific_rows_cover_both_roles_where_needed():
    """Configuration and equalization are asymmetric: DSP leads, USP follows."""
    for sub in (Substate.CFG_LINKWIDTH_START, Substate.CFG_LINKWIDTH_ACCEPT, Substate.CFG_LANENUM_WAIT,
                Substate.CFG_LANENUM_ACCEPT, Substate.REC_EQ_PHASE1, Substate.REC_EQ_PHASE2, Substate.REC_EQ_PHASE3):
        roles = {r.role for r in TABLE_BY_SOURCE[sub]}
        assert DSP in roles and USP in roles, f"{sub} should have DSP and USP rows"


# ---------------------------------------------------------------------------
# reachability
# ---------------------------------------------------------------------------
def test_every_required_row_is_exercised_by_the_scenarios():
    seen = set()
    for scn in list_scenarios():
        seen |= rules_fired(run(scn.id))
    required = {r.rule_id for r in TRANSITIONS if not r.optional}
    assert required - seen == set(), f"rows never used by any scenario: {sorted(required - seen)}"
    optional_used = {r.rule_id for r in TRANSITIONS if r.optional} & seen
    assert not optional_used, f"rows used by scenarios should not be marked optional: {sorted(optional_used)}"


def test_every_optional_row_is_exercised_by_a_unit_test():
    covered = set().union(*(expected for _scn, expected in EXERCISES.values()))
    optional = {r.rule_id for r in TRANSITIONS if r.optional}
    assert optional - covered == set(), f"optional rows without an exercise: {sorted(optional - covered)}"


@pytest.mark.parametrize("name", sorted(EXERCISES))
def test_exercise_reaches_its_optional_rows(name):
    scenario, expected = EXERCISES[name]
    trace = run_custom(scenario)
    missing = expected - rules_fired(trace)
    assert not missing, f"{name}: rows {sorted(missing)} did not fire"
    bad = [c["name"] for c in trace["result"]["checks"] if not c["passed"]]
    assert not bad, f"{name}: invariant failures {bad}"


def test_silent_lanes_come_up_narrow_after_polling_timeout():
    trace = run_custom(EXERCISES["silent_lanes_drop_to_x8"][0])
    assert_link(trace, "A", "L0", "Gen4", "x8")
    t06 = [e for e in trace["events"] if e.get("rule") == "T06"]
    assert t06[0]["t_ns"] >= 12_000_000 + 24_000_000  # waited for the 24 ms Polling.Active timeout


def test_silent_partner_sends_port_to_polling_compliance_then_recovers():
    trace = run_custom(EXERCISES["silent_partner_polling_compliance"][0])
    assert_sequence(trace, "RP", ["Polling.Active", "Polling.Compliance", "Polling.Active", "L0@Gen4"])


def test_disable_then_enable_retrains_from_detect():
    trace = run_custom(EXERCISES["disable_and_enable"][0])
    assert_sequence(trace, "RP", ["L0@Gen4", "Disabled", "Detect.Quiet", "L0@Gen4"])
    assert_sequence(trace, "EP", ["L0@Gen4", "Disabled", "Detect.Quiet", "L0@Gen4"])


def test_l0s_partner_stays_in_l0():
    trace = run_custom(EXERCISES["aspm_l0s"][0])
    assert_sequence(trace, "RP", ["L0@Gen4", "L0s.Entry", "L0s.Idle", "L0s.FTS", "L0@Gen4"])


# ---------------------------------------------------------------------------
# the port dispatches through the table, not a hidden if-chain
# ---------------------------------------------------------------------------
def test_removing_a_row_changes_behavior(monkeypatch):
    """Without row T22 (Configuration.Idle -> L0) the link can never come up."""
    rows = [r for r in TABLE_BY_SOURCE[Substate.CFG_IDLE] if r.rule_id != "T22"]
    monkeypatch.setitem(TABLE_BY_SOURCE, Substate.CFG_IDLE, rows)
    trace = run_custom(make_scenario("X20", duration_ms=20))
    assert trace["links"]["A"]["up"] is False
    assert not any(e["to"] == "L0" for e in trace["events"] if e["kind"] == "state")


def test_row_order_sets_priority(monkeypatch):
    """In L0 a pending Hot Reset beats a speed change because its row comes first."""
    rows = TABLE_BY_SOURCE[Substate.L0]
    order = [r.rule_id for r in rows]
    assert order.index("T24") < order.index("T28") < order.index("T30")


def test_trace_rule_ids_point_at_matching_rows():
    trace = run("S01")
    for e in trace["events"]:
        if e["kind"] != "state" or e["rule"] == "init":
            continue
        row = TABLE_BY_ID[e["rule"]]
        assert e["to"] == row.dst.value
        assert e["from"] in {s.value for s in row.src}


def test_transition_table_doc_is_in_sync():
    doc = (ROOT / "docs" / "guide" / "transition-table.md").read_text(encoding="utf-8")
    assert doc.replace("\r\n", "\n").strip() == table_as_markdown().strip(), (
        "regenerate with: python -m ltssm table --markdown > docs/guide/transition-table.md"
    )
