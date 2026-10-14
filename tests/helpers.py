"""Assertion helpers used by the validation tests.

Author: Farzana Akhter

These helpers read a trace dictionary (the same JSON the viewer loads):

* ``run(id)`` runs a scenario once per test session and caches the trace.
* ``assert_sequence(trace, port, [...])`` checks that a port visits the
  given substates in order (other states may appear in between).
  ``"L0@Gen3"`` means "entered L0 while at Gen3"; ``"Detect"`` matches any
  Detect substate.
* ``assert_never(trace, predicate)`` checks that no event satisfies a
  predicate (forbidden transitions).
* ``assert_link(trace, name, ...)`` checks a link's final state.
"""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ltssm.checks import find_sequence, first_missing, state_events, state_matches  # noqa: E402
from ltssm.scenario import Scenario, find_scenario, run_scenario  # noqa: E402

Trace = Dict[str, Any]


@lru_cache(maxsize=None)
def run(scenario_id: str, seed: Optional[int] = None) -> Trace:
    """Run a scenario (cached per session). Do not modify the returned trace."""
    return run_scenario(find_scenario(scenario_id), seed=seed).trace


def run_custom(scenario: Scenario) -> Trace:
    return run_scenario(scenario).trace


def make_scenario(
    sid: str,
    topology: str = "point_to_point",
    faults: Sequence[dict] = (),
    commands: Sequence[dict] = (),
    config: Optional[dict] = None,
    duration_ms: float = 40.0,
) -> Scenario:
    """Build an in-memory scenario for unit tests."""
    return Scenario(
        id=sid,
        title=sid,
        description=f"unit-test scenario {sid}",
        topology=topology,
        faults=list(faults),
        commands=list(commands),
        config=dict(config or {}),
        duration_ms=duration_ms,
        expected={"summary": "", "checks": []},
        quiz=False,
    )


def path_of(trace: Trace, port: str) -> List[str]:
    """Compact "state@speed" list, used in assertion messages."""
    return [f"{e['to']}@{e['speed']}" for e in state_events(trace, port)]


def assert_sequence(trace: Trace, port: str, states: Sequence[str]) -> None:
    if find_sequence(trace, port, states) is None:
        missing = first_missing(trace, port, states)
        raise AssertionError(
            f"{port} did not go through {list(states)} in order; first missing step: {missing!r}\n"
            f"{port} actually went through: {path_of(trace, port)}"
        )


def assert_never(trace: Trace, predicate: Callable[[Dict[str, Any]], bool], what: str = "forbidden event") -> None:
    bad = [e for e in trace["events"] if predicate(e)]
    if bad:
        first = bad[0]
        raise AssertionError(
            f"{what}: {len(bad)} event(s), first at t={first['t_ns']} ns on {first['port']}: "
            f"{first['from']} -> {first['to']} ({first['reason']})"
        )


def entered(port: Optional[str], pattern: str) -> Callable[[Dict[str, Any]], bool]:
    """Predicate: a state event where ``port`` (any port if None) enters ``pattern``."""

    def pred(e: Dict[str, Any]) -> bool:
        return (
            e["kind"] == "state"
            and (port is None or e["port"] == port)
            and state_matches(pattern, e["to"], e["speed"])
        )

    return pred


def assert_link(trace: Trace, name: str, state: Optional[str] = None, speed: Optional[str] = None,
                width: Optional[str] = None) -> None:
    link = trace["links"][name]
    actual = f"{link['state']} {link['speed']} {link['width']}"
    if state is not None:
        assert state_matches(state, link["state"]), f"link {name}: expected state {state}, got {actual}"
    if speed is not None:
        assert link["speed"] == speed, f"link {name}: expected {speed}, got {actual}"
    if width is not None:
        assert link["width"] == width, f"link {name}: expected {width}, got {actual}"


def counter(trace: Trace, link: str, name: str) -> Any:
    value: Any = trace["links"][link]["counters"]
    for part in name.split("."):
        value = value[part]
    return value


def rules_fired(trace: Trace) -> set:
    return {e.get("rule") for e in trace["events"] if e["kind"] == "state"}
