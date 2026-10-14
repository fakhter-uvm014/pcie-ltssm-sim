"""Load topology files and build ports, links and switch logic.

Author: Farzana Akhter

A topology file (YAML or JSON under ``topologies/``) lists *devices* and
*links*. Each device owns one or more ports:

* ``root_complex``: one or more root ports (role DSP).
* ``switch``: one upstream port (role USP) and N downstream ports (role DSP).
* ``endpoint``: one upstream-facing port (role USP).

Each link connects one DSP to one USP. ``usp: null`` is an empty slot.

Vendor and Device IDs in the shipped topologies are placeholders for the
model; they do not identify real products.

Switch behavior modeled here (``SwitchControl``):

* ``dsp_start: wait_for_upstream`` (default): the model shows the
  downstream ports as held in Detect.Quiet until the upstream link (link A)
  reaches L0. A real switch keeps such ports in reset, which is why their
  receiver termination is high-impedance and partners see "no receiver".
* ``dsp_start: together``: every port starts at power-on.
* When the upstream port enters Hot Reset or link A goes down, every
  downstream port in L0 is directed into Hot Reset, and downstream ports are
  held again until link A is back in L0.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .config import Config, parse_gen, parse_width
from .engine import Engine
from .link import Link
from .paths import topologies_dir
from .port import Port
from .states import Substate as S
from .trace import Trace


def _load_file(path: Path) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    import yaml  # optional dependency, only needed for YAML files

    return yaml.safe_load(text)


def find_topology_file(name_or_path: str) -> Path:
    """Accept "basic", "basic.yaml" or a path."""
    candidate = Path(name_or_path)
    if candidate.is_file():
        return candidate
    base = topologies_dir()
    for ext in ("", ".yaml", ".yml", ".json"):
        p = base / f"{name_or_path}{ext}"
        if p.is_file():
            return p
    raise FileNotFoundError(f"topology {name_or_path!r} not found in {base}")


def list_topologies() -> List[str]:
    return sorted(p.stem for p in topologies_dir().glob("*.yaml"))


def normalize(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Turn the file layout into a flat, validated description."""
    spec: Dict[str, Any] = {
        "name": raw.get("name", "topology"),
        "description": raw.get("description", ""),
        "devices": [],
        "ports": {},
        "links": [],
    }
    for dev in raw.get("devices", []):
        kind = dev["kind"]
        dev_out = {
            "id": dev["id"],
            "kind": kind,
            "label": dev.get("label", dev["id"]),
            "vendor_id": int(dev.get("vendor_id", 0x1234)),
            "device_id": int(dev.get("device_id", 0x0001)),
            "ports": [],
        }
        if kind == "root_complex":
            entries = [(p, "DSP", "root_port") for p in dev["ports"]]
        elif kind == "switch":
            entries = [(dev["upstream"], "USP", "switch_upstream")]
            entries += [(p, "DSP", "switch_downstream") for p in dev["downstream"]]
        elif kind == "endpoint":
            entries = [(dev["port"], "USP", "endpoint")]
        else:
            raise ValueError(f"unknown device kind {kind!r}")
        for index, (p, role, pkind) in enumerate(entries):
            name = p["name"]
            if name in spec["ports"]:
                raise ValueError(f"duplicate port name {name!r}")
            number = p.get("device_number", p.get("port_number", index if pkind != "root_port" else index + 1))
            spec["ports"][name] = {
                "name": name,
                "role": role,
                "kind": pkind,
                "device": dev["id"],
                "label": p.get("label", dev_out["label"] if pkind == "endpoint" else name),
                "max_speed": parse_gen(p.get("max_speed", "Gen4")),
                "max_width": parse_width(p.get("max_width", 16)),
                "target_link_speed": parse_gen(p["target_link_speed"]) if "target_link_speed" in p else None,
                "port_number": int(number),
                "vendor_id": int(p.get("vendor_id", dev_out["vendor_id"])),
                "device_id": int(p.get("device_id", dev_out["device_id"] + index)),
            }
            dev_out["ports"].append(name)
        spec["devices"].append(dev_out)
    for link in raw.get("links", []):
        dsp, usp = link["dsp"], link.get("usp")
        for p in (dsp, usp):
            if p is not None and p not in spec["ports"]:
                raise ValueError(f"link {link['name']} refers to unknown port {p!r}")
        if spec["ports"][dsp]["role"] != "DSP":
            raise ValueError(f"link {link['name']}: {dsp} is not a downstream-facing port")
        if usp is not None and spec["ports"][usp]["role"] != "USP":
            raise ValueError(f"link {link['name']}: {usp} is not an upstream-facing port")
        spec["links"].append({"name": link["name"], "dsp": dsp, "usp": usp, "note": link.get("note", "")})
    return spec


def load_topology_spec(source: Union[str, Dict[str, Any]]) -> Dict[str, Any]:
    """``source`` is a name, a path, or a mapping ``{name: ..., ports: {...}}``
    whose ``ports`` entries override port capabilities."""
    overrides: Dict[str, Any] = {}
    if isinstance(source, dict):
        overrides = source.get("ports", {}) or {}
        source = source["name"]
    spec = normalize(_load_file(find_topology_file(source)))
    spec = copy.deepcopy(spec)
    for name, values in overrides.items():
        apply_port_override(spec, name, values)
    return spec


def apply_port_override(spec: Dict[str, Any], name: str, values: Dict[str, Any]) -> None:
    if name not in spec["ports"]:
        raise KeyError(f"no port named {name!r} in topology {spec['name']!r}")
    port = spec["ports"][name]
    for key, value in values.items():
        if key in ("max_speed", "target_link_speed"):
            port[key] = parse_gen(value)
        elif key == "max_width":
            port[key] = parse_width(value)
        elif key in ("label",):
            port[key] = value
        else:
            raise KeyError(f"cannot override port field {key!r}")


def remove_device_on_link(spec: Dict[str, Any], link_name: str) -> None:
    """Make a link an empty slot (used by the ``empty_slot`` fault)."""
    for link in spec["links"]:
        if link["name"] == link_name and link["usp"] is not None:
            usp = link["usp"]
            link["usp"] = None
            dev_id = spec["ports"][usp]["device"]
            spec["devices"] = [d for d in spec["devices"] if d["id"] != dev_id]
            del spec["ports"][usp]


class SwitchControl:
    """Holds/releases switch downstream ports and propagates Hot Reset."""

    def __init__(self, usp: Port, dsps: List[Port], config: Config, engine: Engine) -> None:
        self.usp = usp
        self.dsps = dsps
        self.engine = engine
        self.wait = config.dsp_start == "wait_for_upstream"
        self.usp_up = False
        usp.listeners.append(self._usp_changed)
        for dsp in dsps:
            dsp.hold_check = self._hold_reason

    def _hold_reason(self, port: Port) -> Optional[str]:
        if self.wait and not self.usp_up:
            return f"switch upstream link {self.usp.link_name} not in L0 yet (dsp_start=wait_for_upstream)"
        return None

    def _usp_changed(self, port: Port, old: S, new: S, event: Optional[str]) -> None:
        if new is S.L0 and not self.usp_up:
            self.usp_up = True
            for dsp in self.dsps:
                self.engine.schedule(0, dsp.release, f"switch upstream link {port.link_name} reached L0")
        elif new in (S.HOT_RESET, S.DETECT_QUIET, S.DISABLED) and self.usp_up:
            self.usp_up = False
            why = "upstream Hot Reset" if new is S.HOT_RESET else f"upstream link {port.link_name} down"
            for dsp in self.dsps:
                self.engine.schedule(0, self._reset_dsp, dsp, why)

    def _reset_dsp(self, dsp: Port, why: str) -> None:
        if dsp.substate in (S.L0, S.L1_IDLE, S.L1_ENTRY):
            dsp.direct("hot_reset", by=f"switch (propagating {why})")
        elif dsp.substate is S.DETECT_QUIET and self.wait:
            dsp.hold(f"switch reset ({why}); waiting for link {self.usp.link_name} to reach L0 again")


class Topology:
    """Live simulation objects built from a normalized spec."""

    def __init__(self, spec: Dict[str, Any], config: Config, engine: Engine, trace: Trace, seed: int) -> None:
        self.spec = spec
        self.name = spec["name"]
        self.config = config
        self.engine = engine
        self.trace = trace
        self.ports: Dict[str, Port] = {}
        self.links: Dict[str, Link] = {}
        self.switches: List[SwitchControl] = []
        for name, p in spec["ports"].items():
            self.ports[name] = Port(
                name=name, role=p["role"], device=p["device"], label=p["label"],
                max_speed=p["max_speed"], max_width=p["max_width"], config=config, engine=engine,
                trace=trace, port_number=p["port_number"], target_link_speed=p["target_link_speed"],
            )
        for link in spec["links"]:
            usp = self.ports[link["usp"]] if link["usp"] else None
            self.links[link["name"]] = Link(link["name"], self.ports[link["dsp"]], usp, config, engine, trace, seed)
        for dev in spec["devices"]:
            if dev["kind"] == "switch":
                names = dev["ports"]
                ups = [self.ports[n] for n in names if spec["ports"][n]["role"] == "USP"]
                downs = [self.ports[n] for n in names if spec["ports"][n]["role"] == "DSP"]
                self.switches.append(SwitchControl(ups[0], downs, config, engine))

    def start(self) -> None:
        for port in self.ports.values():
            if port.link is None:
                raise ValueError(f"port {port.name} is not connected to any link")
            port.start()

    def link_of(self, port_name: str) -> Link:
        return self.ports[port_name].link  # type: ignore[return-value]

    def describe(self) -> Dict[str, Any]:
        """Topology section of the trace (what the viewer draws)."""
        return {
            "name": self.spec["name"],
            "description": self.spec["description"],
            "devices": [
                {
                    "id": d["id"],
                    "kind": d["kind"],
                    "label": d["label"],
                    "ports": list(d["ports"]),
                }
                for d in self.spec["devices"]
            ],
            "ports": [
                {
                    "name": p["name"],
                    "role": p["role"],
                    "kind": p["kind"],
                    "device": p["device"],
                    "label": p["label"],
                    "max_speed": f"Gen{p['max_speed']}",
                    "max_width": f"x{p['max_width']}",
                    "link": self.ports[p["name"]].link_name,
                }
                for p in self.spec["ports"].values()
            ],
            "links": [
                {"name": l["name"], "dsp": l["dsp"], "usp": l["usp"], "lanes": self.links[l["name"]].lanes}
                for l in self.spec["links"]
            ],
        }
