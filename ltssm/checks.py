"""Expected-vs-actual checks and global invariants, evaluated on a trace.

Author: Farzana Akhter

Everything here reads only the trace dictionary (the same JSON the viewer
loads), so a check that passes in the console also passes when someone
re-checks a saved trace file.

Scenario checks (``expected.checks`` in a scenario YAML)::

    - {check: link, link: B, state: L0, speed: Gen2, width: x16}
    - {check: port, port: EP, lane_reversed: true}
    - {check: sequence, port: DSP1, states: [Recovery.Equalization.Phase2, Recovery.Speed, "L0@Gen2"]}
    - {check: never, port: DSP3, state: Polling}
    - {check: only, port: DSP1, states: [Detect]}
    - {check: counter, link: B, counter: eq_failures.Gen3, op: ">=", value: 1}
    - {check: reason, port: DSP1, contains: "equalization"}
    - {check: devices, count: 7, absent: [EP1]}

State patterns: "Detect" matches "Detect.Quiet" and "Detect.Active";
"L0@Gen2" matches an entry into L0 while the speed is Gen2.

Global invariants (added to every run as "invariant: ..."):

* no port enters L0 at 8.0 GT/s without 8 GT/s equalization complete;
* no port enters L0 at 16.0 GT/s without 16 GT/s equalization complete;
* no port reaches 16.0 GT/s before 8 GT/s equalization is complete;
* with dsp_start=wait_for_upstream, switch downstream ports never leave
  Detect.Quiet while the switch upstream link is not up;
* every state transition matches a row of the transition table;
* event times never go backwards.
"""

from __future__ import annotations

import operator
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from .transitions import TABLE_BY_ID

Check = Dict[str, Any]

_OPS: Dict[str, Callable[[Any, Any], bool]] = {
    "==": operator.eq,
    "!=": operator.ne,
    ">=": operator.ge,
    "<=": operator.le,
    ">": operator.gt,
    "<": operator.lt,
}


# ----------------------------------------------------------------------------
# small helpers shared with tests/helpers.py
# ----------------------------------------------------------------------------
def state_matches(pattern: str, state: str, speed: Optional[str] = None) -> bool:
    """``pattern`` may be "L0", "Detect" (family prefix) or "L0@Gen3"."""
    want_speed = None
    if "@" in pattern:
        pattern, want_speed = pattern.split("@", 1)
    if want_speed is not None and speed != want_speed:
        return False
    return state == pattern or state.startswith(pattern + ".")


def state_events(trace: Dict[str, Any], port: Optional[str] = None) -> List[Dict[str, Any]]:
    return [e for e in trace["events"] if e["kind"] == "state" and (port is None or e["port"] == port)]


def find_sequence(trace: Dict[str, Any], port: str, states: Sequence[str]) -> Optional[List[Dict[str, Any]]]:
    """Return the events matching ``states`` in order (gaps allowed), or None."""
    found: List[Dict[str, Any]] = []
    want = list(states)
    for event in state_events(trace, port):
        if not want:
            break
        if state_matches(want[0], event["to"], event["speed"]):
            found.append(event)
            want.pop(0)
    return found if not want else None


def first_missing(trace: Dict[str, Any], port: str, states: Sequence[str]) -> str:
    want = list(states)
    for event in state_events(trace, port):
        if want and state_matches(want[0], event["to"], event["speed"]):
            want.pop(0)
    return want[0] if want else ""


def _get_path(data: Dict[str, Any], path: str) -> Any:
    cur: Any = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


# ----------------------------------------------------------------------------
# scenario checks
# ----------------------------------------------------------------------------
def _result(name: str, passed: bool, detail: str) -> Check:
    return {"name": name, "passed": bool(passed), "detail": detail}


def check_link(trace: Dict[str, Any], spec: Dict[str, Any]) -> Check:
    name = spec["link"]
    link = trace["links"].get(name)
    if link is None:
        return _result(f"link {name} exists", False, f"no link named {name}")
    want_states = _as_list(spec.get("state"))
    parts = []
    ok = True
    if want_states:
        parts.append("/".join(want_states))
        ok &= any(state_matches(s, link["state"]) for s in want_states)
    for key in ("speed", "width"):
        if key in spec:
            parts.append(str(spec[key]))
            ok &= str(link[key]) == str(spec[key])
    if "up" in spec:
        ok &= bool(link["up"]) == bool(spec["up"])
        parts.append("up" if spec["up"] else "down")
    label = f"link {name} ends in {' '.join(parts)}"
    actual = f"actual: {link['state']} {link['speed']} {link['width']} ({'up' if link['up'] else 'down'})"
    return _result(label, ok, actual)


def check_port(trace: Dict[str, Any], spec: Dict[str, Any]) -> Check:
    name = spec["port"]
    port = trace["ports"].get(name)
    if port is None:
        return _result(f"port {name} exists", False, f"no port named {name}")
    ok = True
    wanted, actual = [], []
    for key, value in spec.items():
        if key in ("check", "port", "name"):
            continue
        have = port.get(key)
        if key == "state":
            good = any(state_matches(s, port["state"]) for s in _as_list(value))
        else:
            good = have == value
        ok &= good
        wanted.append(f"{key}={value}")
        actual.append(f"{key}={have}")
    return _result(f"port {name} {' '.join(wanted)}", ok, "actual: " + " ".join(actual))


def check_sequence(trace: Dict[str, Any], spec: Dict[str, Any]) -> Check:
    port, states = spec["port"], spec["states"]
    found = find_sequence(trace, port, states)
    label = f"{port} goes through " + " -> ".join(states)
    if found is not None:
        return _result(label, True, f"seen in order, last at t={found[-1]['t_ns']} ns")
    return _result(label, False, f"never saw {first_missing(trace, port, states)!r} in that order")


def _ports_of(trace: Dict[str, Any], spec: Dict[str, Any]) -> List[str]:
    value = spec.get("port", spec.get("ports", "any"))
    if value == "any":
        return list(trace["ports"].keys())
    return _as_list(value)


def check_never(trace: Dict[str, Any], spec: Dict[str, Any]) -> Check:
    ports = _ports_of(trace, spec)
    patterns = _as_list(spec.get("state", spec.get("states")))
    label = f"{'/'.join(ports)} never enters {' or '.join(patterns)}"
    for event in state_events(trace):
        if event["port"] in ports and any(state_matches(p, event["to"], event["speed"]) for p in patterns):
            return _result(label, False, f"{event['port']} entered {event['to']} at t={event['t_ns']} ns")
    return _result(label, True, "not seen")


def check_only(trace: Dict[str, Any], spec: Dict[str, Any]) -> Check:
    ports = _ports_of(trace, spec)
    patterns = _as_list(spec["states"])
    label = f"{'/'.join(ports)} stays in {' or '.join(patterns)}"
    for event in state_events(trace):
        if event["port"] in ports and not any(state_matches(p, event["to"]) for p in patterns):
            return _result(label, False, f"{event['port']} entered {event['to']} at t={event['t_ns']} ns")
    return _result(label, True, "no other state seen")


def check_counter(trace: Dict[str, Any], spec: Dict[str, Any]) -> Check:
    link, counter = spec["link"], spec["counter"]
    op, value = spec.get("op", "=="), spec["value"]
    have = _get_path(trace["links"][link]["counters"], counter)
    label = f"link {link} {counter} {op} {value}"
    if have is None:
        return _result(label, False, "counter not available")
    return _result(label, _OPS[op](have, value), f"actual: {have}")


def check_reason(trace: Dict[str, Any], spec: Dict[str, Any]) -> Check:
    ports = _ports_of(trace, spec)
    text = spec["contains"].lower()
    label = f"{'/'.join(ports)} trace mentions {spec['contains']!r}"
    for event in trace["events"]:
        if event["port"] in ports and text in event["reason"].lower():
            return _result(label, True, f"t={event['t_ns']} ns: {event['reason']}")
    return _result(label, False, "no event reason contains that text")


def check_devices(trace: Dict[str, Any], spec: Dict[str, Any]) -> Check:
    enum = trace["enumeration"]
    found = {f["port"] for f in enum["functions"]}
    ok, parts = True, []
    if "count" in spec:
        ok &= enum["device_count"] == spec["count"]
        parts.append(f"{spec['count']} functions")
    for p in _as_list(spec.get("present")):
        ok &= p in found
        parts.append(f"{p} present")
    for p in _as_list(spec.get("absent")):
        ok &= p not in found
        parts.append(f"{p} absent")
    return _result(
        "enumeration finds " + ", ".join(parts), ok,
        f"actual: {enum['device_count']} functions ({', '.join(sorted(found)) or 'none'})",
    )


def describe_check(spec: Dict[str, Any]) -> str:
    """Human-readable form of a check spec (used by ``explain``; no trace needed)."""
    kind = spec.get("check")
    if spec.get("name"):
        return str(spec["name"])
    if kind == "link":
        parts = ["/".join(_as_list(spec.get("state")))] + [str(spec[k]) for k in ("speed", "width") if k in spec]
        if "up" in spec:
            parts.append("up" if spec["up"] else "down")
        return f"link {spec['link']} ends in " + " ".join(p for p in parts if p)
    if kind == "port":
        fields = " ".join(f"{k}={v}" for k, v in spec.items() if k not in ("check", "port", "name"))
        return f"port {spec['port']} {fields}"
    if kind == "sequence":
        return f"{spec['port']} goes through " + " -> ".join(spec["states"])
    if kind == "never":
        ports = "/".join(_as_list(spec.get("port", spec.get("ports", "any"))))
        return f"{ports} never enters " + " or ".join(_as_list(spec.get("state", spec.get("states"))))
    if kind == "only":
        ports = "/".join(_as_list(spec.get("port", spec.get("ports", "any"))))
        return f"{ports} stays in " + " or ".join(_as_list(spec["states"]))
    if kind == "counter":
        return f"link {spec['link']} {spec['counter']} {spec.get('op', '==')} {spec['value']}"
    if kind == "reason":
        ports = "/".join(_as_list(spec.get("port", spec.get("ports", "any"))))
        return f"{ports} trace mentions {spec['contains']!r}"
    if kind == "devices":
        parts = []
        if "count" in spec:
            parts.append(f"{spec['count']} functions")
        parts += [f"{p} present" for p in _as_list(spec.get("present"))]
        parts += [f"{p} absent" for p in _as_list(spec.get("absent"))]
        return "enumeration finds " + ", ".join(parts)
    return str(spec)


CHECKS: Dict[str, Callable[[Dict[str, Any], Dict[str, Any]], Check]] = {
    "link": check_link,
    "port": check_port,
    "sequence": check_sequence,
    "never": check_never,
    "only": check_only,
    "counter": check_counter,
    "reason": check_reason,
    "devices": check_devices,
}


def run_checks(trace: Dict[str, Any], specs: Iterable[Dict[str, Any]]) -> List[Check]:
    out = []
    for spec in specs:
        kind = spec.get("check")
        if kind not in CHECKS:
            out.append(_result(f"unknown check {kind!r}", False, "fix the scenario file"))
            continue
        result = CHECKS[kind](trace, spec)
        result["name"] = describe_check(spec)
        out.append(result)
    return out


# ----------------------------------------------------------------------------
# global invariants
# ----------------------------------------------------------------------------
def _flag_walk(trace: Dict[str, Any]) -> List[str]:
    """Replay status flags and state events; return invariant violations."""
    eq8: Dict[str, bool] = {}
    eq16: Dict[str, bool] = {}
    problems_l0_8, problems_l0_16, problems_gen4 = [], [], []
    for e in trace["events"]:
        port = e["port"]
        if e["kind"] == "status":
            flags = (e.get("detail") or {}).get("flags") or {}
            if "eq_8gt_complete" in flags:
                eq8[port] = bool(flags["eq_8gt_complete"])
            if "eq_16gt_complete" in flags:
                eq16[port] = bool(flags["eq_16gt_complete"])
            continue
        if e["kind"] != "state":
            continue
        if e["to"] == "L0" and e["speed"] == "Gen3" and not eq8.get(port):
            problems_l0_8.append(f"{port} at t={e['t_ns']}")
        if e["to"] == "L0" and e["speed"] == "Gen4" and not eq16.get(port):
            problems_l0_16.append(f"{port} at t={e['t_ns']}")
        if e["speed"] == "Gen4" and not eq8.get(port):
            problems_gen4.append(f"{port} at t={e['t_ns']}")
    return [problems_l0_8, problems_l0_16, problems_gen4]  # type: ignore[list-item]


def _dsp_wait_violations(trace: Dict[str, Any]) -> Optional[List[str]]:
    if trace["config"].get("dsp_start") != "wait_for_upstream":
        return None
    ports = {p["name"]: p for p in trace["topology"]["ports"]}
    switch_usp = {p["device"]: p["name"] for p in ports.values() if p["kind"] == "switch_upstream"}
    if not switch_usp:
        return None
    up = {name: False for name in switch_usp.values()}
    bad = []
    for e in state_events(trace):
        p = ports.get(e["port"])
        if p is None:
            continue
        if p["kind"] == "switch_upstream":
            if e["to"] == "L0":
                up[p["name"]] = True
            elif e["to"] in ("Detect.Quiet", "Hot Reset", "Disabled"):
                up[p["name"]] = False
        elif p["kind"] == "switch_downstream" and e["from"] == "Detect.Quiet":
            if not up[switch_usp[p["device"]]]:
                bad.append(f"{p['name']} left Detect.Quiet at t={e['t_ns']}")
    return bad


def _rule_violations(trace: Dict[str, Any]) -> List[str]:
    bad = []
    for e in state_events(trace):
        rule = e.get("rule")
        if rule == "init":
            continue
        row = TABLE_BY_ID.get(rule or "")
        if row is None:
            bad.append(f"{e['port']} t={e['t_ns']}: unknown rule {rule!r}")
            continue
        if e["to"] != row.dst.value or e["from"] not in {s.value for s in row.src}:
            bad.append(f"{e['port']} t={e['t_ns']}: {e['from']} -> {e['to']} does not match {rule}")
    return bad


def run_invariants(trace: Dict[str, Any]) -> List[Check]:
    out: List[Check] = []
    l0_8, l0_16, gen4 = _flag_walk(trace)
    out.append(_result(
        "invariant: no L0 at 8.0 GT/s without 8 GT/s equalization complete", not l0_8,
        "ok" if not l0_8 else "; ".join(l0_8[:5]),
    ))
    out.append(_result(
        "invariant: no L0 at 16.0 GT/s without 16 GT/s equalization complete", not l0_16,
        "ok" if not l0_16 else "; ".join(l0_16[:5]),
    ))
    out.append(_result(
        "invariant: 16.0 GT/s never attempted before 8 GT/s equalization is complete", not gen4,
        "ok" if not gen4 else "; ".join(gen4[:5]),
    ))
    waits = _dsp_wait_violations(trace)
    if waits is not None:
        out.append(_result(
            "invariant: switch downstream ports stay in Detect.Quiet until the upstream link is up",
            not waits, "ok" if not waits else "; ".join(waits[:5]),
        ))
    rules = _rule_violations(trace)
    out.append(_result(
        "invariant: every transition matches a row of the transition table", not rules,
        "ok" if not rules else "; ".join(rules[:5]),
    ))
    times = [e["t_ns"] for e in trace["events"]]
    ordered = all(a <= b for a, b in zip(times, times[1:]))
    out.append(_result("invariant: event times never go backwards", ordered, "ok" if ordered else "out of order"))
    return out
