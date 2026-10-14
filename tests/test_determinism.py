"""Determinism tests: the same seed must give a byte-identical trace.

Author: Farzana Akhter
"""

from __future__ import annotations

import json

import pytest

from ltssm.scenario import find_scenario, run_scenario
from ltssm.trace import write_json


def _bytes(tmp_path, sid, seed=None, name="t.json"):
    trace = run_scenario(find_scenario(sid), seed=seed).trace
    path = tmp_path / name
    write_json(path, trace)
    return path.read_bytes()


@pytest.mark.parametrize("sid", ["S01", "S07", "S15"])
def test_same_seed_gives_byte_identical_trace(tmp_path, sid):
    assert _bytes(tmp_path, sid, name="a.json") == _bytes(tmp_path, sid, name="b.json")


def test_different_seed_changes_random_faults(tmp_path):
    a = json.loads(_bytes(tmp_path, "S15", seed=7, name="a.json"))
    b = json.loads(_bytes(tmp_path, "S15", seed=8, name="b.json"))
    times_a = [e["t_ns"] for e in a["events"] if e.get("rule") == "T28"]
    times_b = [e["t_ns"] for e in b["events"] if e.get("rule") == "T28"]
    assert times_a != times_b
    assert a["seed"] == 7 and b["seed"] == 8


def test_trace_has_no_floats_and_uses_lf_line_endings(tmp_path):
    raw = _bytes(tmp_path, "S04")
    assert b"\r\n" not in raw
    data = json.loads(raw)

    def walk(value):
        if isinstance(value, float):
            raise AssertionError(f"float in trace: {value}")
        if isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    walk(data["events"])
    walk(data["links"])


def test_published_traces_match_a_fresh_run():
    """docs/traces/*.json are what run-all produces (regenerate with python -m ltssm run-all)."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    published = root / "docs" / "traces" / "S04.json"
    fresh = run_scenario(find_scenario("S04")).trace
    text = json.dumps(fresh, indent=2, ensure_ascii=False) + "\n"
    assert published.read_text(encoding="utf-8") == text
