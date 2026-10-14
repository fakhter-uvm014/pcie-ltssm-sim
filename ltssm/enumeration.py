"""Configuration-space walk and bus numbering after link training.

Author: Farzana Akhter

This mimics what firmware or the operating system does at boot, in a very
reduced form:

* Root ports and switch ports are bridges (Type 1 header). Endpoints have a
  Type 0 header.
* The walk is depth-first. Every bridge found gets a *secondary* bus number
  (the bus right below it); after its subtree is scanned, its *subordinate*
  bus number is set to the highest bus number in that subtree. Its
  *primary* bus number is the bus it sits on.
* Reading the Vendor ID of a function that is not there returns 0xFFFF.
  That is what happens behind a link that is not up: the read cannot reach
  the device. The model treats a link as reachable when its Data Link Layer
  is active (DL_Link_Active), which is what follows L0.
* A switch has an internal bus between its upstream port and its downstream
  ports; each downstream port is a device number on that internal bus.

The output can be printed as an ``lspci -t``-style tree. It is model
output, not a real lspci run.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .topology import Topology

NO_DEVICE = 0xFFFF


class _Walker:
    def __init__(self, topo: Topology) -> None:
        self.topo = topo
        self.spec = topo.spec
        self.next_bus = 1
        self.functions: List[Dict[str, Any]] = []
        self.missing: List[Dict[str, str]] = []

    # ---------------------------------------------------------------- helpers
    def _func(self, bus: int, dev: int, port_name: str, kind: str, header: str, parent: Optional[str]) -> Dict[str, Any]:
        p = self.spec["ports"][port_name]
        device = next(d for d in self.spec["devices"] if d["id"] == p["device"])
        fn = {
            "bdf": f"{bus:02x}:{dev:02x}.0",
            "bus": bus,
            "dev": dev,
            "fn": 0,
            "port": port_name,
            "kind": kind,
            "header": header,
            "name": p["label"] if kind != "endpoint" else device["label"],
            "vendor_id": f"0x{p['vendor_id']:04x}",
            "device_id": f"0x{p['device_id']:04x}",
            "parent": parent,
            "primary": None,
            "secondary": None,
            "subordinate": None,
            "children": [],
        }
        self.functions.append(fn)
        return fn

    def _alloc_bus(self) -> int:
        bus = self.next_bus
        self.next_bus += 1
        return bus

    def _link_reachable(self, dsp_name: str) -> bool:
        link = self.topo.ports[dsp_name].link
        if link is None or link.usp is None:
            return False
        return link.dsp.dl_link_active and link.usp.dl_link_active

    # ------------------------------------------------------------------ walk
    def walk(self) -> List[Dict[str, Any]]:
        roots = []
        for dev in self.spec["devices"]:
            if dev["kind"] != "root_complex":
                continue
            for port_name in dev["ports"]:
                number = self.spec["ports"][port_name]["port_number"]
                rp = self._func(0, number, port_name, "root_port", "Type 1", None)
                self._bridge(rp, bus_on=0, dsp_name=port_name)
                roots.append(rp)
        return roots

    def _bridge(self, fn: Dict[str, Any], bus_on: int, dsp_name: str) -> None:
        """Assign numbers to a downstream-facing bridge and scan below it."""
        fn["primary"] = bus_on
        fn["secondary"] = self._alloc_bus()
        self._scan_below(fn, dsp_name, fn["secondary"])
        fn["subordinate"] = self.next_bus - 1

    def _scan_below(self, bridge: Dict[str, Any], dsp_name: str, bus: int) -> None:
        link = self.topo.ports[dsp_name].link
        if link is None or link.usp is None:
            self.missing.append({"behind": dsp_name, "reason": "empty slot (Vendor ID read returns 0xFFFF)"})
            return
        if not self._link_reachable(dsp_name):
            self.missing.append(
                {
                    "behind": dsp_name,
                    "port": link.usp.name,
                    "reason": f"link {link.name} is not up (DL_Link_Active=0), Vendor ID read returns 0xFFFF",
                }
            )
            return
        usp_name = link.usp.name
        usp_spec = self.spec["ports"][usp_name]
        if usp_spec["kind"] == "endpoint":
            child = self._func(bus, 0, usp_name, "endpoint", "Type 0", bridge["bdf"])
            bridge["children"].append(child["bdf"])
            return
        # switch: upstream port bridge, internal bus, downstream port bridges
        up = self._func(bus, 0, usp_name, "switch_upstream", "Type 1", bridge["bdf"])
        bridge["children"].append(up["bdf"])
        up["primary"] = bus
        up["secondary"] = internal = self._alloc_bus()
        device = next(d for d in self.spec["devices"] if d["id"] == usp_spec["device"])
        downs = [n for n in device["ports"] if self.spec["ports"][n]["role"] == "DSP"]
        for index, dsp in enumerate(downs):
            dfn = self._func(internal, index, dsp, "switch_downstream", "Type 1", up["bdf"])
            up["children"].append(dfn["bdf"])
            self._bridge(dfn, bus_on=internal, dsp_name=dsp)
        up["subordinate"] = self.next_bus - 1


def enumerate_topology(topo: Topology) -> Dict[str, Any]:
    """Walk the topology and return the bus tree (JSON-friendly)."""
    walker = _Walker(topo)
    walker.walk()
    funcs = walker.functions
    result = {
        "device_count": len(funcs),
        "functions": [
            {k: v for k, v in f.items()} for f in sorted(funcs, key=lambda f: (f["bus"], f["dev"]))
        ],
        "missing": walker.missing,
    }
    result["lspci_t"] = format_lspci_tree(result)
    return result


# ----------------------------------------------------------------------------
# lspci -t style rendering
# ----------------------------------------------------------------------------
def _by_bdf(tree: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {f["bdf"]: f for f in tree["functions"]}


def _render_function(fn: Dict[str, Any], index: Dict[str, Dict[str, Any]]) -> List[str]:
    head = f"{fn['dev']:02x}.{fn['fn']}"
    if fn["secondary"] is None:
        return [head]
    sec, sub = fn["secondary"], fn["subordinate"]
    rng = f"[{sec:02x}]" if sec == sub else f"[{sec:02x}-{sub:02x}]"
    head = f"{head}-{rng}--"
    kids = [index[b] for b in fn["children"]]
    if not kids:
        return [head]
    lines = _render_bus(kids, index)
    return [head + lines[0]] + [" " * len(head) + line for line in lines[1:]]


def _render_bus(kids: List[Dict[str, Any]], index: Dict[str, Dict[str, Any]]) -> List[str]:
    if len(kids) == 1:
        lines = _render_function(kids[0], index)
        return ["--" + lines[0]] + ["  " + line for line in lines[1:]]
    out: List[str] = []
    for i, kid in enumerate(kids):
        lines = _render_function(kid, index)
        last = i == len(kids) - 1
        out.append(("\\-" if last else "+-") + lines[0])
        out.extend(("  " if last else "| ") + line for line in lines[1:])
    return out


def format_lspci_tree(tree: Dict[str, Any]) -> str:
    index = _by_bdf(tree)
    roots = [f for f in tree["functions"] if f["parent"] is None]
    if not roots:
        return "-[0000:00]-"
    prefix = "-[0000:00]-"
    lines = _render_bus(roots, index)
    first = prefix + lines[0]
    return "\n".join([first] + [" " * len(prefix) + line for line in lines[1:]])


def format_bdf_table(tree: Dict[str, Any]) -> List[str]:
    rows = [f"{'BDF':8}  {'Header':6}  {'Vendor:Device':13}  {'Pri/Sec/Sub':11}  {'Port':5}  Name"]
    for f in tree["functions"]:
        if f["secondary"] is None:
            buses = "-"
        else:
            buses = f"{f['primary']:02x}/{f['secondary']:02x}/{f['subordinate']:02x}"
        vd = f"{f['vendor_id'][2:]}:{f['device_id'][2:]}"
        rows.append(f"{f['bdf']:8}  {f['header']:6}  {vd:13}  {buses:11}  {f['port']:5}  {f['name']}")
    return rows
