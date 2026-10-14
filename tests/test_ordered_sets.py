"""Unit tests for ordered sets, per-lane receive counting, the Link and parsing.

Author: Farzana Akhter
"""

from __future__ import annotations

import pytest

from ltssm.config import Config, format_lanes, parse_gen, parse_lanes, parse_width
from ltssm.engine import Engine
from ltssm.link import LaneErrorFault, Link, TxQuirk
from ltssm.ordered_sets import EIOS, IDLE, LaneReceiver, OrderedSet, OSType, RxBurst, make_ts
from ltssm.port import Port
from ltssm.trace import Trace


# ---------------------------------------------------------------------------
# ordered sets
# ---------------------------------------------------------------------------
def test_unused_fields_are_none_and_print_as_pad():
    ts1 = make_ts(OSType.TS1, rates=(1, 2, 3))
    assert ts1.link_num is None and ts1.lane_num is None and ts1.eq_phase is None
    assert ts1.describe() == "TS1(link=PAD, lane=PAD, rates=Gen1..Gen3)"
    assert ts1.max_rate == 3


def test_identical_ordered_sets_compare_equal_and_hash_equal():
    a = make_ts(OSType.TS2, link_num=1, lane_num=4, rates=(1, 2, 3, 4), speed_change=True)
    b = make_ts(OSType.TS2, link_num=1, lane_num=4, rates=(1, 2, 3, 4), speed_change=True)
    c = make_ts(OSType.TS2, link_num=1, lane_num=5, rates=(1, 2, 3, 4), speed_change=True)
    assert a == b and hash(a) == hash(b)
    assert a != c
    assert "speed_change=1" in a.describe()


def test_eq_fields_and_training_control_bits_in_describe():
    ts = make_ts(OSType.TS1, link_num=0, lane_num=0, eq_phase=2, preset="P7", hot_reset=True)
    text = ts.describe()
    assert "EC=10" in text and "preset=P7" in text and "hot_reset=1" in text
    assert EIOS.describe() == "EIOS" and not EIOS.is_training and ts.is_training


# ---------------------------------------------------------------------------
# per-lane counting
# ---------------------------------------------------------------------------
def test_consecutive_count_accumulates_and_resets_on_change():
    rx = LaneReceiver()
    ts1, ts2 = make_ts(OSType.TS1), make_ts(OSType.TS2)
    rx.accept(ts1, 5, None)
    rx.accept(ts1, 5, None)
    assert rx.consec == 10
    rx.accept(ts2, 3, None)
    assert rx.consec == 3 and rx.last == ts2


def test_corrupted_ordered_set_breaks_the_run():
    rx = LaneReceiver()
    ts1 = make_ts(OSType.TS1)
    rx.accept(ts1, 6, [True, True, True, False, True, True])
    assert rx.consec == 2
    assert rx.errors == 1
    assert rx.best[ts1] == 3


def test_runs_are_latched_until_reset():
    """'Received 8 consecutive TS2' stays true after the partner moves on."""
    rx = LaneReceiver()
    ts2 = make_ts(OSType.TS2)
    rx.accept(ts2, 32, None)
    rx.accept(IDLE, 32, None)
    assert rx.achieved(lambda o: o.type is OSType.TS2, 8) == ts2
    rx.break_run()  # EIOS / undecodable: latched runs survive
    assert rx.achieved(lambda o: o.type is OSType.TS2, 8) == ts2
    rx.reset()  # new substate: start counting again
    assert rx.achieved(lambda o: o.type is OSType.TS2, 8) is None


def test_burst_durations_match_the_data_rate():
    cfg = Config()
    assert cfg.burst_ns(1) == 32 * 64  # 64 ns per TS at 2.5 GT/s (8b/10b)
    assert cfg.burst_ns(2) == 32 * 32
    assert cfg.burst_ns(3) == 520  # 16.25 ns per TS at 8 GT/s (128b/130b)
    assert cfg.burst_ns(4) == 260


# ---------------------------------------------------------------------------
# the Link (wiring and faults)
# ---------------------------------------------------------------------------
class _Sink:
    """Collects bursts the Link delivers to a fake receiver."""


def _pair(width=4, seed=1):
    cfg, eng, tr = Config(), Engine(), Trace()
    dsp = Port("D", "DSP", "dev", "D", 4, width, cfg, eng, tr)
    usp = Port("U", "USP", "dev", "U", 4, width, cfg, eng, tr)
    link = Link("L", dsp, usp, cfg, eng, tr, seed)
    got = []
    usp.receive = lambda burst: got.append(burst)  # type: ignore[assignment]
    dsp.receive = lambda burst: got.append(burst)  # type: ignore[assignment]
    return link, dsp, usp, eng, got


def _send(link, port, eng, lanes, count=4):
    ts = {lane: make_ts(OSType.TS1, lane_num=lane) for lane in lanes}
    link.transmit(port, ts, count)
    eng.run(10_000)


def test_dead_lanes_carry_nothing_and_have_no_termination():
    link, dsp, usp, eng, got = _pair()
    link.dead_lanes = {2, 3}
    _send(link, dsp, eng, [0, 1, 2, 3])
    assert [lane for lane, *_ in got[0].lanes] == [0, 1]
    assert link.detect_receivers(dsp) == [0, 1]


def test_lane_reversal_maps_lane_i_to_n_minus_1_minus_i():
    link, dsp, usp, eng, got = _pair()
    link.reversed = True
    _send(link, dsp, eng, [0, 1, 2, 3])
    mapping = {os.lane_num: lane for lane, os, _, _ in got[0].lanes}
    assert mapping == {0: 3, 1: 2, 2: 1, 3: 0}
    assert link.to_dsp_lane(usp, 0) == 3


def test_held_partner_presents_no_termination():
    link, dsp, usp, *_ = _pair()
    usp.held = True
    assert link.detect_receivers(dsp) == []


def test_lane_errors_are_seeded_and_deterministic():
    def flags(seed):
        link, dsp, usp, eng, got = _pair(seed=seed)
        link.lane_errors.append(LaneErrorFault(lanes=[1], corrupt_rate=0.5))
        _send(link, dsp, eng, [0, 1], count=32)
        by_lane = {lane: f for lane, _, _, f in got[0].lanes}
        assert by_lane[0] is None  # clean lane
        return by_lane[1]

    assert flags(1) == flags(1)
    assert flags(1) != flags(2)


def test_lane_errors_can_be_limited_to_one_rate():
    fault = LaneErrorFault(lanes=[5], corrupt_rate=0.01, rates=[4])
    assert fault.applies(4, True) and not fault.applies(3, True)
    down_only = LaneErrorFault(lanes=[5], corrupt_rate=0.01, direction="down")
    assert down_only.applies(1, True) and not down_only.applies(1, False)


def test_ts2_as_ts1_quirk_rewrites_only_ts2():
    link, dsp, usp, eng, got = _pair()
    link.tx_quirks["U"] = TxQuirk(ts2_as_ts1=True)
    link.transmit(usp, {0: make_ts(OSType.TS2, rates=(1, 2))}, 1)
    link.transmit(usp, {0: IDLE}, 1)
    eng.run(10_000)
    assert got[0].lanes[0][1].type is OSType.TS1
    assert got[0].lanes[0][1].rates == (1, 2)
    assert got[1].lanes[0][1] is IDLE


def test_rx_burst_carries_the_sender_rate():
    link, dsp, usp, eng, got = _pair()
    dsp.rate = 3
    _send(link, dsp, eng, [0])
    assert isinstance(got[0], RxBurst) and got[0].rate == 3


# ---------------------------------------------------------------------------
# parsing helpers
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text,gen", [("Gen1", 1), ("gen3", 3), ("8GT/s", 3), ("16.0 GT/s", 4), (2, 2), ("5", 2)])
def test_parse_gen(text, gen):
    assert parse_gen(text) == gen


def test_parse_gen_rejects_gen5():
    with pytest.raises(ValueError):
        parse_gen("Gen5")


def test_parse_width_and_lanes():
    assert parse_width("x8") == 8 and parse_width(16) == 16
    with pytest.raises(ValueError):
        parse_width("x3")
    assert parse_lanes("8-11,14") == [8, 9, 10, 11, 14]
    assert parse_lanes([5, "0-1"]) == [0, 1, 5]
    assert format_lanes([0, 1, 2, 3, 8, 10, 11]) == "0-3,8,10-11"


def test_config_overrides_accept_ms_suffix_and_reject_unknown_keys():
    cfg = Config().with_overrides({"detect_quiet_ms": 6, "gen2_step": True})
    assert cfg.detect_quiet_ns == 6_000_000 and cfg.gen2_step is True
    with pytest.raises(KeyError):
        Config().with_overrides({"no_such_option": 1})
    with pytest.raises(ValueError):
        Config().with_overrides({"dsp_start": "sometimes"})


def test_ordered_set_is_immutable():
    ts = OrderedSet(OSType.TS1)
    with pytest.raises(Exception):
        ts.link_num = 3  # type: ignore[misc]
