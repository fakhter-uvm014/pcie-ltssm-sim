"""Load scenario files, apply faults, run the simulation, build the trace.

Author: Farzana Akhter

A scenario YAML file contains:

``id, title, description``      what the scenario is
``topology``                     a topology name, or ``{name: ..., ports: {PORT: {field: value}}}``
``seed, duration_ms, config``    RNG seed, simulated time, Config overrides
``faults``                       list of faults (see FAULT_TYPES below)
``commands``                     software actions at given times (Hot Reset, retrain, ...)
``expected``                     ``summary`` text and machine-checked ``checks``
``symptom``                      neutral description for mystery mode
``what_to_look_for``             what the output/timeline shows
``what_this_means``              short paragraph printed after a run
``likely_root_causes``           what this symptom usually means on real hardware
``how_to_debug``                 steps a student would take on real hardware
``mystery_hints``                questions printed in mystery mode
``mystery_cause``                one-line root cause used by the quiz
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .checks import run_checks, run_invariants
from .config import MS, Config, format_lanes, parse_gen, parse_lanes
from .engine import Engine
from .enumeration import enumerate_topology
from .link import LaneErrorFault, TxQuirk
from .paths import scenarios_dir
from .topology import Topology, apply_port_override, load_topology_spec, remove_device_on_link
from .trace import Trace

TRACE_FORMAT = "pcie-ltssm-sim trace"
TRACE_FORMAT_VERSION = 1

FAULT_TYPES = (
    "capability",
    "target_link_speed",
    "empty_slot",
    "dead_lanes",
    "silent_lanes",
    "lane_reversal",
    "lane_errors",
    "eq_fail",
    "partner_never_sends_ts2",
    "partner_silent",
    "periodic_l0_errors",
    "connection_lost",
)

COMMANDS = (
    "hot_reset", "disable", "enable", "loopback", "loopback_exit", "retrain",
    "set_target_speed", "l23_ready", "power_restore",
)


@dataclass
class Scenario:
    id: str
    title: str
    description: str
    topology: Union[str, Dict[str, Any]]
    seed: int = 1
    duration_ms: float = 60.0
    config: Dict[str, Any] = field(default_factory=dict)
    faults: List[Dict[str, Any]] = field(default_factory=list)
    commands: List[Dict[str, Any]] = field(default_factory=list)
    expected: Dict[str, Any] = field(default_factory=dict)
    symptom: str = ""
    what_to_look_for: str = ""
    what_this_means: str = ""
    likely_root_causes: List[str] = field(default_factory=list)
    how_to_debug: List[str] = field(default_factory=list)
    mystery_hints: List[str] = field(default_factory=list)
    mystery_cause: str = ""
    quiz: bool = True
    source: str = ""

    @property
    def slug(self) -> str:
        return Path(self.source).stem if self.source else self.id

    def notes(self) -> Dict[str, Any]:
        """The learning notes embedded in the trace (and shown in the viewer)."""
        return {
            "id": self.id,
            "title": self.title,
            "description": _clean(self.description),
            "symptom": _clean(self.symptom),
            "topology": self.topology if isinstance(self.topology, str) else self.topology.get("name"),
            "faults": describe_faults(self.faults),
            "fault_specs": self.faults,
            "commands": describe_commands(self.commands),
            "settings": [f"config {k} = {str(v).lower() if isinstance(v, bool) else v}" for k, v in self.config.items()],
            "expected": {
                "summary": _clean(self.expected.get("summary", "")),
                "checks": self.expected.get("checks", []),
            },
            "what_to_look_for": _clean(self.what_to_look_for),
            "what_this_means": _clean(self.what_this_means),
            "likely_root_causes": [_clean(x) for x in self.likely_root_causes],
            "how_to_debug": [_clean(x) for x in self.how_to_debug],
            "mystery_hints": [_clean(x) for x in self.mystery_hints],
            "mystery_cause": _clean(self.mystery_cause),
        }


def _clean(text: str) -> str:
    """Join folded YAML text into tidy paragraphs (keeps blank-line breaks)."""
    paras = [" ".join(p.split()) for p in str(text or "").strip().split("\n\n")]
    return "\n\n".join(p for p in paras if p)


# ----------------------------------------------------------------------------
# loading
# ----------------------------------------------------------------------------
_KNOWN_KEYS = set(Scenario.__dataclass_fields__) - {"source"}


def load_scenario(path: Union[str, Path]) -> Scenario:
    import yaml

    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    unknown = set(data) - _KNOWN_KEYS
    if unknown:
        raise KeyError(f"{path.name}: unknown scenario fields {sorted(unknown)}")
    for key in ("id", "title", "description", "topology", "expected"):
        if key not in data:
            raise KeyError(f"{path.name}: missing required field {key!r}")
    for fault in data.get("faults", []) or []:
        if fault.get("type") not in FAULT_TYPES:
            raise ValueError(f"{path.name}: unknown fault type {fault.get('type')!r}")
    for cmd in data.get("commands", []) or []:
        if cmd.get("command") not in COMMANDS:
            raise ValueError(f"{path.name}: unknown command {cmd.get('command')!r}")
    data["faults"] = data.get("faults") or []
    data["commands"] = data.get("commands") or []
    data["config"] = data.get("config") or {}
    return Scenario(source=str(path), **data)


def scenario_files() -> List[Path]:
    return sorted(scenarios_dir().glob("S*.yaml"))


def list_scenarios() -> List[Scenario]:
    return [load_scenario(p) for p in scenario_files()]


def find_scenario(ref: str) -> Scenario:
    """Accept an ID ("S04", "s4"), a file name or a path."""
    p = Path(ref)
    if p.is_file():
        return load_scenario(p)
    key = ref.strip().upper()
    if key.startswith("S") and key[1:].isdigit():
        key = f"S{int(key[1:]):02d}"
    for path in scenario_files():
        if path.stem.upper().startswith(key + "_") or path.stem.upper() == key:
            return load_scenario(path)
    raise FileNotFoundError(f"no scenario {ref!r}; try 'python -m ltssm list'")


# ----------------------------------------------------------------------------
# faults and commands
# ----------------------------------------------------------------------------
def _ms(value: Any) -> int:
    return int(round(float(value) * MS))


def describe_fault(f: Dict[str, Any]) -> str:
    t = f["type"]
    if t == "capability":
        caps = []
        if "max_speed" in f:
            caps.append(f"supports only up to {f['max_speed']}")
        if "max_width" in f:
            caps.append(f"is only {f['max_width']} wide")
        return f"{f['port']} " + " and ".join(caps)
    if t == "target_link_speed":
        return f"software set Target Link Speed (LnkCtl2) of {f['port']} to {f['speed']}"
    if t == "empty_slot":
        return f"nothing plugged into the slot on link {f['link']}"
    if t == "dead_lanes":
        if f["lanes"] == "all":
            return f"every lane of link {f['link']} is open (no receiver termination, no signal)"
        return f"lanes {format_lanes(parse_lanes(f['lanes']))} of link {f['link']} are open (no receiver termination, no signal)"
    if t == "silent_lanes":
        return f"lanes {f['lanes']} of link {f['link']} terminate but never carry signal"
    if t == "lane_reversal":
        return f"link {f['link']} is wired with lanes in reverse order"
    if t == "lane_errors":
        rates = f.get("rates")
        at = "at " + "/".join(str(r) for r in rates) + " only" if rates else "at every rate"
        lanes = format_lanes(parse_lanes(f["lanes"]))
        what = []
        if f.get("corrupt_rate"):
            what.append(f"corrupts {float(f['corrupt_rate']) * 100:g}%")
        if f.get("drop_rate"):
            what.append(f"drops {float(f['drop_rate']) * 100:g}%")
        return f"lane {lanes} of link {f['link']} {' and '.join(what) or 'is noisy'} of ordered sets {at}"
    if t == "eq_fail":
        return f"equalization at {f['rate']} on link {f['link']} cannot pass phase {f['phase']}"
    if t == "partner_never_sends_ts2":
        return f"{f['port']}'s transmitter sends TS1 where it should send TS2"
    if t == "partner_silent":
        until = f" until {f['until_ms']} ms" if "until_ms" in f else ""
        return f"{f['port']}'s transmitter stays silent{until} (receiver termination present)"
    if t == "periodic_l0_errors":
        return (
            f"{f['count']} receiver errors injected on link {f['link']} in L0, every {f['period_ms']} ms "
            f"from {f['start_ms']} ms"
        )
    if t == "connection_lost":
        return f"every lane of link {f['link']} opens at {f['at_ms']} ms (card removed / cable unplugged)"
    return str(f)


def describe_faults(faults: List[Dict[str, Any]]) -> List[str]:
    return [describe_fault(f) for f in faults]


def describe_commands(commands: List[Dict[str, Any]]) -> List[str]:
    from .port import COMMAND_TEXT

    out = []
    for c in commands:
        if c["command"] == "set_target_speed":
            text = f"Target Link Speed set to {c['speed']} and Retrain Link set"
        else:
            text = COMMAND_TEXT.get(c["command"], c["command"])
        out.append(f"at {c['at_ms']} ms on {c['port']}, software: {text}")
    return out


def _apply_spec_faults(spec: Dict[str, Any], faults: List[Dict[str, Any]]) -> None:
    """Faults that change the topology before ports are built."""
    for f in faults:
        t = f["type"]
        if t == "capability":
            values = {k: v for k, v in f.items() if k in ("max_speed", "max_width")}
            apply_port_override(spec, f["port"], values)
        elif t == "target_link_speed":
            apply_port_override(spec, f["port"], {"target_link_speed": f["speed"]})
        elif t == "empty_slot":
            remove_device_on_link(spec, f["link"])


def _apply_runtime_faults(topo: Topology, faults: List[Dict[str, Any]]) -> None:
    for f in faults:
        t = f["type"]
        link = topo.links.get(f["link"]) if "link" in f else None
        if "link" in f and link is None:
            raise KeyError(f"fault {t}: no link {f['link']!r}")
        if t == "dead_lanes":
            lanes = range(link.lanes) if f["lanes"] == "all" else parse_lanes(f["lanes"])
            link.dead_lanes.update(lanes)
        elif t == "silent_lanes":
            link.silent_lanes.update(parse_lanes(f["lanes"]))
        elif t == "lane_reversal":
            link.reversed = True
        elif t == "lane_errors":
            rates = [parse_gen(r) for r in f["rates"]] if f.get("rates") else None
            link.lane_errors.append(
                LaneErrorFault(
                    lanes=parse_lanes(f["lanes"]),
                    corrupt_rate=float(f.get("corrupt_rate", 0.0)),
                    drop_rate=float(f.get("drop_rate", 0.0)),
                    rates=rates,
                    direction=f.get("direction", "both"),
                )
            )
        elif t == "eq_fail":
            from .equalization import EQ_FAIL_PENALTY

            link.eq_fail[(parse_gen(f["rate"]), int(f["phase"]))] = int(f.get("penalty", EQ_FAIL_PENALTY))
        elif t in ("partner_never_sends_ts2", "partner_silent"):
            port = topo.ports[f["port"]]
            quirk = port.link.tx_quirks.setdefault(port.name, TxQuirk())
            if t == "partner_never_sends_ts2":
                quirk.ts2_as_ts1 = True
            else:
                quirk.silent_until_ns = _ms(f["until_ms"]) if "until_ms" in f else 10**18
        elif t == "periodic_l0_errors":
            link.schedule_periodic_errors(_ms(f["start_ms"]), _ms(f["period_ms"]), int(f["count"]))
        elif t == "connection_lost":
            link.schedule_connection_loss(_ms(f["at_ms"]))


def _schedule_commands(topo: Topology, engine: Engine, commands: List[Dict[str, Any]]) -> None:
    for c in commands:
        port = topo.ports[c["port"]]
        kwargs = {}
        if c["command"] == "set_target_speed":
            kwargs["speed"] = parse_gen(c["speed"])
        engine.schedule_at(_ms(c["at_ms"]), port.direct, c["command"], c.get("note", ""), **kwargs)


# ----------------------------------------------------------------------------
# running
# ----------------------------------------------------------------------------
@dataclass
class RunResult:
    scenario: Scenario
    trace: Dict[str, Any]

    @property
    def passed(self) -> bool:
        return bool(self.trace["result"]["passed"])


def run_scenario(scenario: Scenario, seed: Optional[int] = None, config_overrides: Optional[Dict[str, Any]] = None) -> RunResult:
    """Run one scenario and return the full trace dictionary."""
    seed = scenario.seed if seed is None else int(seed)
    overrides = dict(scenario.config)
    overrides.update(config_overrides or {})
    if "duration_ms" not in overrides and "duration_ns" not in overrides:
        overrides["duration_ms"] = scenario.duration_ms
    config = Config().with_overrides(overrides)

    spec = load_topology_spec(scenario.topology)
    _apply_spec_faults(spec, scenario.faults)

    engine = Engine()
    trace = Trace()
    topo = Topology(spec, config, engine, trace, seed)
    _apply_runtime_faults(topo, scenario.faults)
    _schedule_commands(topo, engine, scenario.commands)
    topo.start()
    engine.run(config.duration_ns)

    data = build_trace(scenario, topo, config, seed, trace, engine)
    checks = run_checks(data, scenario.expected.get("checks", []))
    checks += run_invariants(data)
    data["result"] = {"passed": all(c["passed"] for c in checks), "checks": checks}
    return RunResult(scenario, data)


def build_trace(scenario: Scenario, topo: Topology, config: Config, seed: int, trace: Trace, engine: Engine) -> Dict[str, Any]:
    events = trace.events
    return {
        "format": TRACE_FORMAT,
        "format_version": TRACE_FORMAT_VERSION,
        "scenario": scenario.notes(),
        "topology": topo.describe(),
        "seed": seed,
        "duration_ns": config.duration_ns,
        "config": config.to_dict(),
        "stats": {
            "events": len(events),
            "state_events": sum(1 for e in events if e["kind"] == "state"),
            "engine_events": engine.events_processed,
        },
        "events": events,
        "links": {name: link.final_summary() for name, link in topo.links.items()},
        "ports": {name: port.final_summary() for name, port in topo.ports.items()},
        "enumeration": enumerate_topology(topo),
        "result": {"passed": False, "checks": []},
    }


def topology_scenario(name: str) -> Scenario:
    """A fault-free scenario for ``python -m ltssm enumerate <topology>``."""
    return Scenario(
        id=f"topology:{name}",
        title=f"Clean bring-up of topology {name}",
        description="No faults; used to print the bus tree.",
        topology=name,
        duration_ms=45.0,
        expected={"summary": "all populated links train", "checks": []},
        quiz=False,
    )

