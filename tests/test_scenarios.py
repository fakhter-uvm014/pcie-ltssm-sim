"""Scenario validation tests: one test per scenario, written like a test plan.

Author: Farzana Akhter

Each test documents Test ID, Purpose, Stimulus and Expected result, then
checks the final link state, the state sequence of the key ports, forbidden
transitions, and counters. A second group of tests applies the global
"forbidden transition" rules to every scenario.
"""

from __future__ import annotations

import pytest
from helpers import (
    assert_link,
    assert_never,
    assert_sequence,
    counter,
    entered,
    run,
)

from ltssm.scenario import list_scenarios

ALL_IDS = [s.id for s in list_scenarios()]


# ---------------------------------------------------------------------------
# One test per scenario
# ---------------------------------------------------------------------------
def test_S01_clean_gen4_bringup_trains_every_link_to_gen4():
    """
    Test ID: S01
    Purpose: Baseline. A fault-free root port -> switch -> endpoints topology
             trains every populated link to the best common rate and width.
    Stimulus: None (power-on, empty slot on link D).
    Expected result: A and B at Gen4 x16, C at Gen4 x4, D stays in Detect;
             each link goes Gen1 -> Gen3 (EQ) -> Gen4 (EQ); 7 functions enumerate.
    """
    t = run("S01")
    assert_link(t, "A", "L0", "Gen4", "x16")
    assert_link(t, "B", "L0", "Gen4", "x16")
    assert_link(t, "C", "L0", "Gen4", "x4")
    assert_link(t, "D", "Detect")
    assert_sequence(t, "RP", ["Detect.Quiet", "Detect.Active", "Polling.Active", "Polling.Configuration",
                              "Configuration.Linkwidth.Start", "Configuration.Linkwidth.Accept",
                              "Configuration.Lanenum.Wait", "Configuration.Lanenum.Accept",
                              "Configuration.Complete", "Configuration.Idle", "L0@Gen1",
                              "Recovery.RcvrLock", "Recovery.RcvrCfg", "Recovery.Speed",
                              "Recovery.Equalization.Phase1", "Recovery.Equalization.Phase2",
                              "Recovery.Equalization.Phase3", "L0@Gen3", "L0@Gen4"])
    assert_sequence(t, "USP", ["Recovery.Equalization.Phase0", "Recovery.Equalization.Phase1",
                               "Recovery.Equalization.Phase2", "Recovery.Equalization.Phase3", "L0@Gen3"])
    for link in "ABC":
        assert counter(t, link, "flap_count") == 0
        assert counter(t, link, "eq_failures.Gen3") == 0
        assert counter(t, link, "eq_failures.Gen4") == 0
        assert counter(t, link, "speed_changes") == 2
    assert t["enumeration"]["device_count"] == 7
    assert t["result"]["passed"]


def test_S02_gen2_only_endpoint_trains_link_c_at_gen2():
    """
    Test ID: S02
    Purpose: A link runs at the highest rate BOTH ends advertise.
    Stimulus: Endpoint 2 capability limited to Gen2 (5.0 GT/s).
    Expected result: Link C at Gen2 x4 with no equalization; A and B at Gen4.
    """
    t = run("S02")
    assert_link(t, "C", "L0", "Gen2", "x4")
    assert_link(t, "A", "L0", "Gen4", "x16")
    assert_link(t, "B", "L0", "Gen4", "x16")
    assert_sequence(t, "DSP2", ["L0@Gen1", "Recovery.Speed", "L0@Gen2"])
    assert_never(t, entered("DSP2", "Recovery.Equalization"), "equalization at 5 GT/s")
    assert counter(t, "C", "speed_changes") == 1
    assert t["ports"]["EP2"]["max_speed"] == "Gen2"


def test_S03_empty_slot_cycles_detect_and_never_polls():
    """
    Test ID: S03
    Purpose: Receiver detection gates everything; an empty slot never trains.
    Stimulus: No device on link D.
    Expected result: DSP3 alternates Detect.Quiet/Detect.Active, never enters
             Polling; detect_cycles grows; the other links are unaffected.
    """
    t = run("S03")
    assert_link(t, "D", "Detect")
    assert_never(t, lambda e: e["kind"] == "state" and e["port"] == "DSP3" and not e["to"].startswith("Detect"),
                 "DSP3 left Detect")
    assert counter(t, "D", "detect_cycles") >= 2
    assert_sequence(t, "DSP3", ["Detect.Active", "Detect.Quiet", "Detect.Active", "Detect.Quiet"])
    assert_link(t, "B", "L0", "Gen4", "x16")


def test_S04_gen3_eq_failure_falls_back_to_gen2():
    """
    Test ID: S04
    Purpose: Equalization failure handling and fallback policy.
    Stimulus: 8.0 GT/s equalization on link B cannot pass Phase 2.
    Expected result: two failed Gen3 EQ attempts, rate ceiling lowered to Gen2,
             link B ends at Gen2 x16; it never reaches L0 at Gen3 or Gen4; the
             reason is in the trace.
    """
    t = run("S04")
    assert_link(t, "B", "L0", "Gen2", "x16")
    assert_sequence(t, "EP1", ["L0@Gen1", "Recovery.Equalization.Phase2", "Recovery.Speed", "L0@Gen1",
                               "Recovery.Equalization.Phase2", "Recovery.Speed", "L0@Gen2"])
    assert_never(t, entered("DSP1", "L0@Gen3"), "L0 at Gen3 after failed EQ")
    assert_never(t, entered("DSP1", "L0@Gen4"), "L0 at Gen4 after failed EQ")
    assert counter(t, "B", "eq_attempts.Gen3") == 2
    assert counter(t, "B", "eq_failures.Gen3") == 2
    assert counter(t, "B", "eq_attempts.Gen4") == 0
    reasons = [e["reason"] for e in t["events"] if e["port"] == "DSP1"]
    assert any("rate ceiling lowered to Gen2" in r for r in reasons)
    assert any("Phase 2 FAILED" in e["reason"] for e in t["events"] if e["port"] == "EP1")
    eq = t["ports"]["DSP1"]["eq"]["Gen3"]
    assert eq["complete"] is False and eq["phase1"] is True and eq["phase2"] is False
    assert eq["link_eq_request"] is True


def test_S05_gen4_eq_failure_settles_at_gen3():
    """
    Test ID: S05
    Purpose: Each rate equalizes separately; a Gen4 failure keeps Gen3.
    Stimulus: 16.0 GT/s equalization on link B cannot pass Phase 3.
    Expected result: link B at Gen3 x16; 8 GT/s EQ complete, 16 GT/s EQ not;
             the link reverts to Gen3 (not Gen1) after each failure.
    """
    t = run("S05")
    assert_link(t, "B", "L0", "Gen3", "x16")
    assert t["ports"]["DSP1"]["eq_8gt_complete"] is True
    assert t["ports"]["DSP1"]["eq_16gt_complete"] is False
    assert_sequence(t, "DSP1", ["L0@Gen3", "Recovery.Equalization.Phase3", "Recovery.Speed", "L0@Gen3",
                                "Recovery.Equalization.Phase3", "Recovery.Speed", "L0@Gen3"])
    assert_never(t, entered("EP1", "L0@Gen4"), "L0 at Gen4 after failed 16 GT/s EQ")
    assert counter(t, "B", "eq_failures.Gen4") == 2
    eq16 = t["ports"]["DSP1"]["eq"]["Gen4"]
    assert eq16["phase1"] and eq16["phase2"] and not eq16["phase3"]


def test_S06_lanes_8_to_15_dead_trains_x8():
    """
    Test ID: S06
    Purpose: Width negotiation uses only lanes that work on both sides.
    Stimulus: lanes 8-15 of link A open (no termination, no signal).
    Expected result: link A trains at Gen4 x8 (full speed, half width).
    """
    t = run("S06")
    assert_link(t, "A", "L0", "Gen4", "x8")
    assert t["ports"]["RP"]["width"] == "x8"
    assert t["ports"]["USP"]["width"] == "x8"
    detect = [e for e in t["events"] if e["port"] == "RP" and e.get("rule") == "T03"]
    assert "lanes 0-7 (8 of 16)" in detect[0]["reason"]
    assert_link(t, "B", "L0", "Gen4", "x16")


def test_S07_periodic_l0_errors_counted_as_flaps():
    """
    Test ID: S07
    Purpose: Errors in L0 cause Recovery; the counters separate flaps from
             planned recoveries.
    Stimulus: six receiver errors on link B, every 3 ms from 32 ms.
    Expected result: flap_count 6, link stays Gen4 x16, never goes down, no
             speed change during the flaps.
    """
    t = run("S07")
    assert_link(t, "B", "L0", "Gen4", "x16")
    assert counter(t, "B", "flap_count") == 6
    assert counter(t, "B", "recovery_count") == 8  # 6 flaps + 2 speed changes
    assert counter(t, "B", "link_down_count") == 0
    assert counter(t, "B", "rx_errors") == 6
    assert_sequence(t, "DSP1", ["L0@Gen4"] + ["Recovery.RcvrLock", "Recovery.RcvrCfg", "Recovery.Idle", "L0@Gen4"] * 6)
    assert_never(t, entered("EP1", "Recovery.Speed@Gen4"), "speed change during flaps")
    rx_errors = t["ports"]["DSP1"]["rx_errors"] + t["ports"]["EP1"]["rx_errors"]
    assert rx_errors == 6


def test_S08_partner_never_sends_ts2_times_out_to_detect():
    """
    Test ID: S08
    Purpose: Polling.Configuration needs TS2 from the partner.
    Stimulus: the endpoint transmits TS1 wherever it should send TS2.
    Expected result: RP times out of Polling.Configuration (48 ms) to Detect,
             never reaches Configuration or L0; only the root port enumerates.
    """
    t = run("S08")
    assert_sequence(t, "RP", ["Polling.Active", "Polling.Configuration", "Detect.Quiet"])
    timeout = [e for e in t["events"] if e["port"] == "RP" and e.get("rule") == "T11"]
    assert timeout, "expected the Polling.Configuration timeout rule T11 on RP"
    assert timeout[0]["t_ns"] - next(
        e["t_ns"] for e in t["events"] if e["port"] == "RP" and e["to"] == "Polling.Configuration"
    ) == 48_000_000
    assert "TS1" in timeout[0]["reason"]
    assert_never(t, entered("RP", "Configuration"), "RP reached Configuration")
    assert_never(t, entered(None, "L0"), "link came up")
    assert t["enumeration"]["device_count"] == 1


def test_S09_upstream_link_down_hides_everything_below():
    """
    Test ID: S09
    Purpose: A down upstream link hides the whole subtree; switch downstream
             ports wait for the upstream link (wait_for_upstream).
    Stimulus: every lane of link A open.
    Expected result: link A cycles in Detect; DSP1-3 never leave
             Detect.Quiet; endpoints never reach Polling; only the root port
             enumerates.
    """
    t = run("S09")
    assert_link(t, "A", "Detect")
    for dsp in ("DSP1", "DSP2", "DSP3"):
        assert_never(t, lambda e, d=dsp: e["kind"] == "state" and e["port"] == d and e["to"] != "Detect.Quiet",
                     f"{dsp} left Detect.Quiet")
        assert t["ports"][dsp]["held"] is True
    assert_never(t, entered("EP1", "Polling"), "EP1 reached Polling")
    assert t["enumeration"]["device_count"] == 1
    assert counter(t, "A", "detect_cycles") >= 6


def test_S10_hot_reset_retrains_all_switch_links():
    """
    Test ID: S10
    Purpose: Hot Reset propagates through the switch and every link retrains.
    Stimulus: software Hot Reset on RP at 35 ms.
    Expected result: RP, USP, DSP1/2 and EP1/2 pass through Hot Reset and
             Detect, then retrain; final state equals S01.
    """
    t = run("S10")
    for link, width in (("A", "x16"), ("B", "x16"), ("C", "x4")):
        assert_link(t, link, "L0", "Gen4", width)
        assert counter(t, link, "link_down_count") == 1
    assert_sequence(t, "RP", ["L0@Gen4", "Recovery.RcvrLock", "Recovery.RcvrCfg", "Hot Reset", "Detect.Quiet",
                              "L0@Gen1", "L0@Gen3", "L0@Gen4"])
    for port in ("USP", "DSP1", "DSP2", "EP1", "EP2"):
        assert_sequence(t, port, ["L0@Gen4", "Hot Reset", "Detect.Quiet", "L0@Gen4"])
    s01 = run("S01")
    for name in ("A", "B", "C", "D"):
        assert t["links"][name]["state"].split(".")[0] == s01["links"][name]["state"].split(".")[0]
        assert t["links"][name]["width"] == s01["links"][name]["width"]
    assert t["enumeration"]["device_count"] == s01["enumeration"]["device_count"]


def test_S11_aspm_l1_exits_through_recovery():
    """
    Test ID: S11
    Purpose: ASPM L1 entry/exit; L1 exits are Recovery passes, not errors.
    Stimulus: ASPM L1 enabled; idle traffic for 2 ms, 3 ms L1 residency, twice.
    Expected result: L0 -> L1.Entry -> L1.Idle -> Recovery -> L0 twice;
             flap_count 0; DL_Link_Active stays set.
    """
    t = run("S11")
    assert_link(t, "A", "L0", "Gen4", "x16")
    loop = ["L1.Entry", "L1.Idle", "Recovery.RcvrLock", "Recovery.RcvrCfg", "Recovery.Idle", "L0@Gen4"]
    assert_sequence(t, "EP", ["L0@Gen4"] + loop * 2)
    assert_sequence(t, "RP", ["L0@Gen4"] + loop * 2)
    assert counter(t, "A", "l1_entries") == 2
    assert counter(t, "A", "flap_count") == 0
    assert counter(t, "A", "recovery_causes")["L1 exit"] == 2
    assert_never(t, lambda e: e["kind"] == "status" and e["port"] == "EP" and "DL_Down" in e["reason"],
                 "DL went down during L1")


def test_S12_lane_reversal_trains_full_width():
    """
    Test ID: S12
    Purpose: Lane reversal is detected in Configuration and costs nothing.
    Stimulus: link A wired with lanes in reverse order.
    Expected result: Gen4 x16; the endpoint reports lane reversal applied and
             maps physical lane 0 to logical lane 15.
    """
    t = run("S12")
    assert_link(t, "A", "L0", "Gen4", "x16")
    assert t["ports"]["EP"]["lane_reversed"] is True
    assert t["ports"]["RP"]["lane_reversed"] is False
    status = [e for e in t["events"] if e["port"] == "EP" and "lane reversal applied" in e["reason"]]
    assert status and status[0]["detail"]["lane_map"]["0"] == 15


def test_S13_gen2_step_policy_visits_every_rate():
    """
    Test ID: S13
    Purpose: The speed-change policy is configurable (gen2_step).
    Stimulus: gen2_step: true.
    Expected result: every populated link visits L0 at Gen1, Gen2, Gen3, Gen4;
             three speed changes; final state equals S01 but later.
    """
    t = run("S13")
    for port in ("RP", "DSP1", "EP2"):
        assert_sequence(t, port, ["L0@Gen1", "L0@Gen2", "L0@Gen3", "L0@Gen4"])
    assert counter(t, "A", "speed_changes") == 3
    s01 = run("S01")
    assert counter(t, "A", "time_to_final_speed_ns") > counter(s01, "A", "time_to_final_speed_ns")
    assert_link(t, "C", "L0", "Gen4", "x4")


def test_S14_recovery_failure_takes_link_down():
    """
    Test ID: S14
    Purpose: Recovery that cannot re-lock ends in Detect (link down).
    Stimulus: every lane of link B opens at 32 ms.
    Expected result: L0 -> Recovery.RcvrLock -> (2 ms) -> Detect.Quiet on both
             ends; DL_Link_Active clears; EP1 disappears from enumeration.
    """
    t = run("S14")
    assert_link(t, "B", "Detect")
    for port in ("DSP1", "EP1"):
        assert_sequence(t, port, ["L0@Gen4", "Recovery.RcvrLock", "Detect.Quiet"])
        assert t["ports"][port]["dl_link_active"] is False
    down = [e for e in t["events"] if e["port"] == "DSP1" and e.get("rule") == "T46"]
    lock = [e for e in t["events"] if e["port"] == "DSP1" and e["to"] == "Recovery.RcvrLock" and e["from"] == "L0"]
    assert down[0]["t_ns"] - lock[-1]["t_ns"] == 2_000_000
    assert counter(t, "B", "link_down_count") == 1
    found = {f["port"] for f in t["enumeration"]["functions"]}
    assert "EP1" not in found and "DSP1" in found


def test_S15_marginal_lane_causes_recovery_storm():
    """
    Test ID: S15
    Purpose: One marginal lane at one rate shows up as frequent recoveries.
    Stimulus: lane 5 of link B corrupts 0.1 percent of ordered sets at Gen4.
    Expected result: many flaps (>= 10), all blamed on lane 5, no errors
             before Gen4, link never goes down, other links quiet.
    """
    t = run("S15")
    assert counter(t, "B", "flap_count") >= 10
    assert counter(t, "B", "link_down_count") == 0
    errors = [e for e in t["events"] if e.get("rule") == "T28"]
    assert errors and all(e["link"] == "B" and "lane 5" in e["reason"] and e["speed"] == "Gen4" for e in errors)
    assert counter(t, "C", "flap_count") == 0
    assert t["links"]["B"]["speed"] == "Gen4"


def test_S16_target_link_speed_caps_link_at_gen3():
    """
    Test ID: S16
    Purpose: Software can cap a link below what both ends support.
    Stimulus: DSP1 Target Link Speed (LnkCtl2) = Gen3.
    Expected result: link B at Gen3 x16 with 8 GT/s EQ complete, no Gen4
             attempt, no failures; the reason names Target Link Speed.
    """
    t = run("S16")
    assert_link(t, "B", "L0", "Gen3", "x16")
    assert counter(t, "B", "eq_attempts.Gen4") == 0
    assert counter(t, "B", "eq_failures.Gen3") == 0
    assert t["ports"]["DSP1"]["target_link_speed"] == "Gen3"
    assert any("Target Link Speed (LnkCtl2) is Gen3" in e["reason"] for e in t["events"] if e["port"] == "DSP1")
    assert_link(t, "C", "L0", "Gen4", "x4")


# ---------------------------------------------------------------------------
# Rules that must hold in every scenario
# ---------------------------------------------------------------------------
def _replay_eq_flags(trace):
    """Yield (event, eq8, eq16) with the port's EQ-complete flags before the event."""
    eq8, eq16 = {}, {}
    for e in trace["events"]:
        yield e, eq8.get(e["port"], False), eq16.get(e["port"], False)
        flags = (e.get("detail") or {}).get("flags") or {}
        if "eq_8gt_complete" in flags:
            eq8[e["port"]] = flags["eq_8gt_complete"]
        if "eq_16gt_complete" in flags:
            eq16[e["port"]] = flags["eq_16gt_complete"]


@pytest.mark.parametrize("sid", ALL_IDS)
def test_scenario_matches_its_expected_outcome(sid):
    """Every expected check in the scenario file passes (the same checks run-all prints)."""
    t = run(sid)
    failed = [f"{c['name']} ({c['detail']})" for c in t["result"]["checks"] if not c["passed"]]
    assert not failed, "\n".join(failed)


@pytest.mark.parametrize("sid", ALL_IDS)
def test_never_l0_at_gen4_without_16gt_equalization(sid):
    t = run(sid)
    bad = [e for e, _eq8, eq16 in _replay_eq_flags(t)
           if e["kind"] == "state" and e["to"] == "L0" and e["speed"] == "Gen4" and not eq16]
    assert not bad, f"L0 at Gen4 without 16 GT/s EQ: {bad[0]}"


@pytest.mark.parametrize("sid", ALL_IDS)
def test_never_l0_at_gen3_without_8gt_equalization(sid):
    t = run(sid)
    bad = [e for e, eq8, _eq16 in _replay_eq_flags(t)
           if e["kind"] == "state" and e["to"] == "L0" and e["speed"] == "Gen3" and not eq8]
    assert not bad, f"L0 at Gen3 without 8 GT/s EQ: {bad[0]}"


@pytest.mark.parametrize("sid", ALL_IDS)
def test_never_attempt_gen4_before_gen3_equalization(sid):
    t = run(sid)
    bad = [e for e, eq8, _eq16 in _replay_eq_flags(t) if e["kind"] == "state" and e["speed"] == "Gen4" and not eq8]
    assert not bad, f"16 GT/s attempted before 8 GT/s EQ complete: {bad[0]}"


@pytest.mark.parametrize("sid", [s for s in ALL_IDS if run(s)["topology"]["name"] == "basic"])
def test_switch_dsps_never_leave_detect_before_link_a_is_up(sid):
    t = run(sid)
    if t["config"]["dsp_start"] != "wait_for_upstream":
        pytest.skip("scenario uses dsp_start=together")
    usp_up = False
    for e in t["events"]:
        if e["kind"] != "state":
            continue
        if e["port"] == "USP":
            if e["to"] == "L0":
                usp_up = True
            elif e["to"] in ("Detect.Quiet", "Hot Reset"):
                usp_up = False
        if e["port"] in ("DSP1", "DSP2", "DSP3") and e["from"] == "Detect.Quiet":
            assert usp_up, f"{e['port']} left Detect.Quiet at t={e['t_ns']} before link A was up"


@pytest.mark.parametrize("sid", ALL_IDS)
def test_every_event_has_a_reason_and_valid_fields(sid):
    t = run(sid)
    last = 0
    for e in t["events"]:
        assert e["reason"].strip(), e
        assert e["kind"] in ("state", "status", "eq", "fault")
        assert e["speed"] in ("Gen1", "Gen2", "Gen3", "Gen4")
        assert e["width"] in ("x0", "x1", "x2", "x4", "x8", "x16")
        assert e["t_ns"] >= last
        last = e["t_ns"]
        if e["kind"] == "state":
            assert e.get("rule"), e


@pytest.mark.parametrize("sid", ALL_IDS)
def test_final_speed_and_width_never_exceed_either_end(sid):
    t = run(sid)
    for link in t["links"].values():
        if not link["up"]:
            continue
        dsp, usp = t["ports"][link["dsp"]], t["ports"][link["usp"]]
        assert int(link["speed"][3:]) <= min(int(dsp["max_speed"][3:]), int(usp["max_speed"][3:]))
        assert int(link["width"][1:]) <= min(int(dsp["max_width"][1:]), int(usp["max_width"][1:]))
