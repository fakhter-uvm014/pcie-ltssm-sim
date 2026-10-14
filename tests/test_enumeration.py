"""Enumeration tests: depth-first bus numbering, header types, missing devices.

Author: Farzana Akhter
"""

from __future__ import annotations

from helpers import run, run_custom

from ltssm.enumeration import format_bdf_table
from ltssm.scenario import topology_scenario


def _by_port(trace):
    return {f["port"]: f for f in trace["enumeration"]["functions"]}


def test_basic_topology_bus_numbers_are_assigned_depth_first():
    f = _by_port(run("S01"))
    expected = {
        # port: (BDF, primary, secondary, subordinate)
        "RP": ("00:01.0", 0, 1, 5),
        "USP": ("01:00.0", 1, 2, 5),
        "DSP1": ("02:00.0", 2, 3, 3),
        "DSP2": ("02:01.0", 2, 4, 4),
        "DSP3": ("02:02.0", 2, 5, 5),
    }
    for port, (bdf, pri, sec, sub) in expected.items():
        assert (f[port]["bdf"], f[port]["primary"], f[port]["secondary"], f[port]["subordinate"]) == (
            bdf, pri, sec, sub), port
    assert f["EP1"]["bdf"] == "03:00.0"
    assert f["EP2"]["bdf"] == "04:00.0"


def test_bridges_are_type1_and_endpoints_type0():
    f = _by_port(run("S01"))
    for port in ("RP", "USP", "DSP1", "DSP2", "DSP3"):
        assert f[port]["header"] == "Type 1"
    for port in ("EP1", "EP2"):
        assert f[port]["header"] == "Type 0"
        assert f[port]["secondary"] is None


def test_empty_slot_still_gets_a_bus_number():
    t = run("S01")
    f = _by_port(t)
    assert f["DSP3"]["secondary"] == 5 and f["DSP3"]["children"] == []
    assert any(m["behind"] == "DSP3" and "empty slot" in m["reason"] for m in t["enumeration"]["missing"])


def test_lspci_tree_rendering():
    assert run("S01")["enumeration"]["lspci_t"] == (
        "-[0000:00]---01.0-[01-05]----00.0-[02-05]--+-00.0-[03]----00.0\n"
        "                                           +-01.0-[04]----00.0\n"
        "                                           \\-02.0-[05]--"
    )


def test_link_down_hides_the_whole_subtree():
    t = run("S09")
    f = _by_port(t)
    assert list(f) == ["RP"]
    assert (f["RP"]["primary"], f["RP"]["secondary"], f["RP"]["subordinate"]) == (0, 1, 1)
    assert t["enumeration"]["lspci_t"] == "-[0000:00]---01.0-[01]--"
    assert "0xFFFF" in t["enumeration"]["missing"][0]["reason"]


def test_device_lost_after_training_is_missing():
    f = _by_port(run("S14"))
    assert "EP1" not in f and "EP2" in f
    assert f["DSP1"]["secondary"] == 3  # bus numbering is unchanged


def test_point_to_point_topology():
    t = run_custom(topology_scenario("point_to_point"))
    f = _by_port(t)
    assert set(f) == {"RP", "EP"}
    assert f["EP"]["bdf"] == "01:00.0"
    assert t["enumeration"]["lspci_t"] == "-[0000:00]---01.0-[01]----00.0"


def test_vendor_ids_are_read_and_table_prints():
    t = run("S01")
    rows = format_bdf_table(t["enumeration"])
    assert rows[0].startswith("BDF")
    assert any("00:01.0" in r and "00/01/05" in r for r in rows)
    assert all(f["vendor_id"] != "0xffff" for f in t["enumeration"]["functions"])
