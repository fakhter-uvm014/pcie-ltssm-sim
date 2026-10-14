"""Console output: run summary, event log, mystery mode, lspci-style views.

Author: Farzana Akhter

Everything here turns a trace dictionary into plain ASCII text. Output is
ASCII only and uses no color unless ``color=True`` (``--color`` on the
command line), so it reads the same on Linux, macOS and Windows terminals.
"""

from __future__ import annotations

import textwrap
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .checks import describe_check, state_events
from .config import LSPCI_SPEED, RATE_GTS, parse_gen
from .enumeration import format_bdf_table
from .states import family_of

WIDTH = 78
MODEL_NOTE = "model output from a behavioral simulator, not a real lspci run"

# Transitions worth calling out in mystery mode (timeouts, failures, errors).
NOTABLE_RULES = {
    "T04", "T06", "T07", "T08", "T11", "T14", "T23", "T28", "T45", "T46", "T55",
    "T62", "T65", "T67", "T68", "T69", "T71",
}


# ----------------------------------------------------------------------------
# small formatting helpers
# ----------------------------------------------------------------------------
def ms(t_ns: Optional[int]) -> str:
    return "-" if t_ns is None else f"{t_ns / 1_000_000:.3f} ms"


def _ansi(text: str, code: str, color: bool) -> str:
    return f"\033[{code}m{text}\033[0m" if color else text


def verdict(passed: bool, color: bool = False) -> str:
    return _ansi("PASS", "32;1", color) if passed else _ansi("FAIL", "31;1", color)


def rule(char: str = "-") -> str:
    return char * WIDTH


def wrap(text: str, indent: str = "  ", width: int = WIDTH) -> List[str]:
    out: List[str] = []
    for para in str(text).split("\n\n"):
        lines = textwrap.wrap(para, width=width, initial_indent=indent, subsequent_indent=indent)
        out.extend(lines or [indent])
        out.append("")
    while out and out[-1] == "":
        out.pop()
    return out


def bullets(items: Iterable[str], indent: str = "  ") -> List[str]:
    out = []
    for item in items:
        out.extend(
            textwrap.wrap(item, width=WIDTH, initial_indent=indent + "- ", subsequent_indent=indent + "  ")
        )
    return out


def table(headers: Sequence[str], rows: Sequence[Sequence[Any]], indent: str = "  ") -> List[str]:
    cells = [[str(c) for c in r] for r in rows]
    widths = [len(h) for h in headers]
    for r in cells:
        for i, c in enumerate(r):
            widths[i] = max(widths[i], len(c))
    fmt = indent + "  ".join("{:<" + str(w) + "}" for w in widths)
    out = [fmt.format(*headers).rstrip(), (indent + "  ".join("-" * w for w in widths))]
    out += [fmt.format(*r).rstrip() for r in cells]
    return out


def _rate(gen: str) -> str:
    return f"{gen} ({RATE_GTS[parse_gen(gen)]})"


# ----------------------------------------------------------------------------
# blocks shared by the normal and the mystery views
# ----------------------------------------------------------------------------
def link_status_block(trace: Dict[str, Any]) -> List[str]:
    rows = []
    for name, link in trace["links"].items():
        rows.append(
            [
                name,
                link["dsp"],
                link["usp"] or "(empty)",
                link["state"],
                _rate(link["speed"]) if link["up"] else "-",
                link["width"],
                "yes" if link["dl_link_active"] else "no",
            ]
        )
    return ["Final link status (model output; compare with lspci LnkSta)"] + table(
        ["Link", "DSP", "USP", "State", "Speed", "Width", "DL_Active"], rows
    )


def port_status_block(trace: Dict[str, Any]) -> List[str]:
    rows = []
    for p in trace["ports"].values():
        rows.append(
            [
                p["name"],
                p["role"],
                p["link"],
                p["state"],
                p["speed"],
                p["width"],
                f"{p['max_speed']} {p['max_width']}",
                p["target_link_speed"],
                "+" if p["eq_8gt_complete"] else "-",
                "+" if p["eq_16gt_complete"] else "-",
                p["rx_errors"],
            ]
        )
    return ["Per-port status"] + table(
        ["Port", "Role", "Link", "State", "Speed", "Width", "LnkCap", "Target", "EQ8", "EQ16", "RxErr"], rows
    )


def counters_block(trace: Dict[str, Any]) -> List[str]:
    rows = []
    for name, link in trace["links"].items():
        c = link["counters"]
        rows.append(
            [
                name,
                ms(c["time_to_l0_ns"]),
                ms(c["time_to_final_speed_ns"]),
                c["recovery_count"],
                c["flap_count"],
                c["detect_cycles"],
                c["link_down_count"],
                f"{c['eq_attempts']['Gen3']}/{c['eq_attempts']['Gen4']}",
                f"{c['eq_failures']['Gen3']}/{c['eq_failures']['Gen4']}",
            ]
        )
    lines = ["Counters (per link)"] + table(
        ["Link", "First L0", "Final speed at", "Recov", "Flaps", "Detect", "Down", "EQ try 8G/16G", "EQ fail 8G/16G"],
        rows,
    )
    causes = []
    for name, link in trace["links"].items():
        rc = link["counters"]["recovery_causes"]
        if rc:
            causes.append(f"{name}: " + ", ".join(f"{k} {v}" for k, v in rc.items()))
    if causes:
        lines.append("  Recovery causes: " + "; ".join(causes))
    return lines


def enumeration_block(trace: Dict[str, Any], with_table: bool = False) -> List[str]:
    enum = trace["enumeration"]
    lines = ["Enumeration (model output, lspci -t style)"]
    lines += ["  " + line for line in enum["lspci_t"].split("\n")]
    lines.append(f"  {enum['device_count']} function(s) found")
    if with_table:
        lines.append("")
        lines += ["  " + row for row in format_bdf_table(enum)]
    for miss in enum["missing"]:
        if "port" in miss:
            lines.append(f"  behind {miss['behind']}: {miss['port']} not found - {miss['reason']}")
        else:
            lines.append(f"  behind {miss['behind']}: {miss['reason']}")
    return lines


def checks_block(trace: Dict[str, Any], color: bool = False) -> List[str]:
    checks = trace["result"]["checks"]
    lines = ["Checks against the expected outcome"]
    invariants = [c for c in checks if c["name"].startswith("invariant:")]
    for c in checks:
        if c in invariants:
            continue
        lines += textwrap.wrap(f"[{verdict(c['passed'], color)}] {c['name']}", width=WIDTH,
                               initial_indent="  ", subsequent_indent="         ")
        if not c["passed"]:
            lines.append(f"         {c['detail']}")
    bad = [c for c in invariants if not c["passed"]]
    if bad:
        for c in bad:
            lines.append(f"  [{verdict(False, color)}] {c['name']}")
            lines.append(f"         {c['detail']}")
    else:
        lines.append(f"  [{verdict(True, color)}] all {len(invariants)} global invariants hold "
                     "(EQ before 8/16 GT/s L0, switch wait rule, table rules, time order)")
    n_ok = sum(1 for c in checks if c["passed"])
    passed = trace["result"]["passed"]
    tail = "matches the expected outcome" if passed else "does NOT match the expected outcome"
    lines.append(f"Result: {verdict(passed, color)} - {tail} ({n_ok}/{len(checks)} checks)")
    return lines


# ----------------------------------------------------------------------------
# full run summary
# ----------------------------------------------------------------------------
def run_summary(trace: Dict[str, Any], trace_path: str = "", color: bool = False) -> str:
    sc = trace["scenario"]
    out: List[str] = [rule("="), f"{sc['id']}  {sc['title']}", rule("=")]
    out += wrap(sc["description"])
    stimulus = sc["faults"] + sc["commands"] + sc.get("settings", [])
    if stimulus:
        out.append("")
        out.append("Stimulus:")
        out += bullets(stimulus)
    out.append("")
    out.append("Expected:")
    out += wrap(sc["expected"]["summary"])
    out.append("")
    stats = trace["stats"]
    out.append(
        f"Run: topology {trace['topology']['name']} | seed {trace['seed']} | "
        f"{ms(trace['duration_ns'])} simulated | {stats['events']} trace events "
        f"({stats['state_events']} state changes)"
    )
    for block in (link_status_block, port_status_block, counters_block):
        out.append("")
        out += block(trace)
    out.append("")
    out += enumeration_block(trace)
    out.append("")
    out += checks_block(trace, color)
    if sc["what_this_means"]:
        out.append("")
        out.append("What this means")
        out += wrap(sc["what_this_means"])
    out.append("")
    if trace_path:
        out.append(f"Trace written to {trace_path}")
    sid = sc["id"]
    out.append("Next steps:")
    out.append(f"  python -m ltssm explain {sid}       learning notes, root causes, how to debug")
    out.append(f"  python -m ltssm run {sid} --trace   full event log with the reason for every step")
    out.append(f"  python -m ltssm lspci {sid}         the result as lspci -vvv would show it")
    out.append("  python -m http.server --directory docs 8000, then open http://localhost:8000/")
    out.append("                                    the viewer (run-all refreshes docs/traces)")
    return "\n".join(out)


# ----------------------------------------------------------------------------
# event log
# ----------------------------------------------------------------------------
def event_log(trace: Dict[str, Any], ports: Optional[Sequence[str]] = None, kinds: Optional[Sequence[str]] = None) -> str:
    kinds = kinds or ("state", "status", "eq", "fault")
    lines = [
        "Event log (t = simulated time; 'why' is the condition that fired; T## is the transition-table row)",
        rule(),
    ]
    for e in trace["events"]:
        if ports and e["port"] not in ports:
            continue
        if e["kind"] not in kinds:
            continue
        t = f"{e['t_ns'] / 1_000_000:11.6f} ms"
        head = f"{t}  {e['port']:<5} [{e['link']}]"
        if e["kind"] == "state":
            lines.append(f"{head}  {e['from']} -> {e['to']}  ({e['speed']} {e['width']}, {e.get('rule', '')})")
            lines += textwrap.wrap(e["reason"], width=WIDTH + 20, initial_indent=" " * 18 + "why: ",
                                   subsequent_indent=" " * 23)
        else:
            label = {"status": "status", "eq": "eq", "fault": "FAULT"}[e["kind"]]
            lines += textwrap.wrap(f"{label}: {e['reason']}", width=WIDTH + 20, initial_indent=head + "  ",
                                   subsequent_indent=" " * (len(head) + 4))
    return "\n".join(lines)


# ----------------------------------------------------------------------------
# mystery mode
# ----------------------------------------------------------------------------
def _family_label(event: Dict[str, Any]) -> str:
    fam = family_of(event["to"])
    if fam == "L0":
        return f"L0/{event['speed']}"
    return fam


def _collapse(tokens: List[tuple]) -> List[str]:
    """Collapse repeated cycles: [A, B, A, B, A, B] -> ['(A > B) x3']."""
    out: List[str] = []
    i = 0
    labels = [lab for _, lab in tokens]
    while i < len(tokens):
        best = None
        for period in (1, 2, 3, 4):
            chunk = labels[i:i + period]
            if len(chunk) < period:
                break
            k = 1
            while labels[i + k * period:i + (k + 1) * period] == chunk:
                k += 1
            if k >= 2 and (best is None or period * k > best[0] * best[1]):
                best = (period, k)
        t, lab = tokens[i]
        if best:
            period, k = best
            inner = " > ".join(labels[i:i + period])
            out.append(f"{t / 1_000_000:.3f} ({inner}) x{k}")
            i += period * k
        else:
            out.append(f"{t / 1_000_000:.3f} {lab}")
            i += 1
    return out


def abbreviated_timeline(trace: Dict[str, Any]) -> List[str]:
    lines = []
    for name, port in trace["ports"].items():
        raw: List[list] = []  # [t_ns, family label, failed receiver detections]
        for e in state_events(trace, name):
            label = _family_label(e)
            if raw and raw[-1][1] == label:
                raw[-1][2] += 1 if e.get("rule") == "T04" else 0
                continue
            raw.append([e["t_ns"], label, 0])
        tokens = [(t, f"{lab}[no receiver x{n}]" if n else lab) for t, lab, n in raw]
        text = " > ".join(_collapse(tokens))
        lines += textwrap.wrap(text, width=WIDTH, initial_indent=f"  {name:<5}[{port['link']}] ",
                               subsequent_indent=" " * 11)
    return lines


def _is_notable(e: Dict[str, Any], trace: Dict[str, Any]) -> bool:
    if e["kind"] == "fault":
        return False
    if e["kind"] == "eq":
        return False  # the phase pass/fail transition already summarizes the trials
    if e["kind"] == "status":
        if e["reason"].startswith("held in") and trace["ports"][e["port"]]["held"]:
            return True  # still held at the end of the run
        keys = ("rate ceiling", "lane reversal", "DL_Down", "software", "switch (")
        return any(k in e["reason"] for k in keys)
    rule_id = e.get("rule")
    if rule_id == "T04":
        # a failed receiver detection only matters if the link never came up
        return not trace["links"][e["link"]]["up"]
    if rule_id in ("T03", "T15"):
        # narrower than the physical link (both ends' widths) is worth a look
        physical = trace["links"][e["link"]]["physical_lanes"]
        return e["width"] != f"x{physical}"
    return rule_id in NOTABLE_RULES


def notable_events(trace: Dict[str, Any], limit: int = 14) -> List[str]:
    picked = [e for e in trace["events"] if _is_notable(e, trace)]
    lines = []
    seen = set()
    shown = 0
    for e in picked:
        key = (e["port"], e.get("rule"), e["reason"][:40])
        if key in seen:
            continue
        seen.add(key)
        if shown >= limit:
            break
        shown += 1
        what = f"{e['from']} -> {e['to']}: " if e["kind"] == "state" else f"{e['kind']}: "
        lines += textwrap.wrap(
            f"{e['t_ns'] / 1_000_000:.3f} ms {e['port']}: {what}{e['reason']}", width=WIDTH,
            initial_indent="  - ", subsequent_indent="    ",
        )
    hidden = len(picked) - shown
    if hidden > 0:
        lines.append(f"  ({hidden} more similar events not shown; run with --reveal or --trace)")
    return lines or ["  (no timeouts, failures or errors were logged)"]


def mystery_view(trace: Dict[str, Any], color: bool = False) -> str:
    sc = trace["scenario"]
    out = [rule("="), f"MYSTERY MODE  {sc['id']}  (symptoms only; the root cause is hidden)", rule("=")]
    out.append("Symptom:")
    out += wrap(sc["symptom"] or "Something is not right with this system.")
    out.append("")
    out.append(f"Topology: {trace['topology']['name']}  ({trace['topology']['description'].rstrip('.')})")
    for block in (link_status_block, port_status_block, counters_block):
        out.append("")
        out += block(trace)
    out.append("")
    out += enumeration_block(trace)
    out.append("")
    out.append("Abbreviated timeline per port (state families; '(A > B) xN' = a repeated cycle)")
    out += abbreviated_timeline(trace)
    out.append("")
    out.append("Notable events (timeouts, failures, errors, software actions)")
    out += notable_events(trace)
    out.append("")
    out.append("Hypothesis? Questions to ask yourself:")
    hints = list(sc["mystery_hints"]) + [
        "Compare LnkCap with LnkSta for every port: speed, width, or both?",
        "Which link is different from the healthy baseline (python -m ltssm run S01)?",
    ]
    out += bullets(hints)
    passed = trace["result"]["passed"]
    out.append("")
    out.append(f"Result vs expected outcome: {verdict(passed, color)} (check details hidden)")
    out.append(f"Reveal the answer: python -m ltssm run {sc['id']} --reveal")
    return "\n".join(out)


def learning_notes(scenario: Dict[str, Any], include_cause: bool = True) -> List[str]:
    out: List[str] = []
    if include_cause:
        out.append("Injected fault / stimulus:")
        items = scenario["faults"] + scenario["commands"] + scenario.get("settings", [])
        out += bullets(items or ["none (healthy run)"])
        out.append("")
    out.append("What to look for:")
    out += wrap(scenario["what_to_look_for"])
    out.append("")
    out.append("What this means:")
    out += wrap(scenario["what_this_means"])
    out.append("")
    out.append("Likely root causes on real hardware:")
    out += bullets(scenario["likely_root_causes"])
    out.append("")
    out.append("How to debug it on real hardware:")
    out += bullets(scenario["how_to_debug"])
    return out


def reveal_view(trace: Dict[str, Any], trace_path: str = "", color: bool = False) -> str:
    sc = trace["scenario"]
    out = [run_summary(trace, trace_path, color), "", rule("="), f"REVEAL  {sc['id']}: {sc['title']}", rule("=")]
    if sc["mystery_cause"]:
        out.append("Root cause:")
        out += wrap(sc["mystery_cause"])
        out.append("")
    out += learning_notes(sc)
    return "\n".join(out)


# ----------------------------------------------------------------------------
# explain / list / run-all
# ----------------------------------------------------------------------------
def explain_view(scn) -> str:
    notes = scn.notes()
    out = [rule("="), f"{scn.id}  {scn.title}", rule("=")]
    out += wrap(notes["description"])
    out.append("")
    topo = scn.topology if isinstance(scn.topology, str) else scn.topology.get("name")
    out.append(f"Topology: {topo}   seed: {scn.seed}   simulated time: {scn.duration_ms} ms")
    if scn.config:
        out.append("Config overrides: " + ", ".join(f"{k}={v}" for k, v in scn.config.items()))
    out.append("")
    out.append("Expected outcome:")
    out += wrap(notes["expected"]["summary"])
    out.append("")
    out.append("Machine-checked expectations:")
    for c in notes["expected"]["checks"]:
        out += textwrap.wrap(describe_check(c), width=WIDTH, initial_indent="  - ", subsequent_indent="    ")
    out.append("  - plus the global invariants checked on every run (see docs/guide/interpreting-results.md)")
    out.append("")
    out += learning_notes(notes)
    out.append("")
    out.append(f"Try it: python -m ltssm run {scn.id}      (add --trace for the event log)")
    if scn.quiz:
        out.append(f"        python -m ltssm run {scn.id} --hidden   (symptoms only, guess the cause)")
    return "\n".join(out)


def list_view(scenarios) -> str:
    rows = []
    for s in scenarios:
        topo = s.topology if isinstance(s.topology, str) else s.topology.get("name")
        summary = " ".join(str(s.expected.get("summary", "")).split())
        if len(summary) > 58:
            summary = summary[:55].rstrip() + "..."
        rows.append([s.id, s.title[:44], topo, summary])
    lines = ["Scenarios (python -m ltssm explain <ID> for the full notes)"]
    lines += table(["ID", "Title", "Topology", "Expected outcome"], rows, indent="")
    return "\n".join(lines)


def run_all_view(results: List[Dict[str, Any]], color: bool = False) -> str:
    rows = []
    for r in results:
        t = r["trace"]
        checks = t["result"]["checks"]
        n_ok = sum(1 for c in checks if c["passed"])
        rows.append(
            [t["scenario"]["id"], t["scenario"]["title"][:46], f"{n_ok}/{len(checks)}",
             verdict(t["result"]["passed"], color)]
        )
    lines = table(["ID", "Title", "Checks", "Result"], rows, indent="")
    failed = [r for r in results if not r["trace"]["result"]["passed"]]
    lines.append("")
    if failed:
        lines.append(f"{len(failed)} of {len(results)} scenarios do NOT match their expected outcome:")
        for r in failed:
            for c in r["trace"]["result"]["checks"]:
                if not c["passed"]:
                    lines.append(f"  {r['trace']['scenario']['id']}: {c['name']} ({c['detail']})")
    else:
        lines.append(f"All {len(results)} scenarios match their expected outcome.")
    return "\n".join(lines)


# ----------------------------------------------------------------------------
# lspci -vvv style excerpt
# ----------------------------------------------------------------------------
def _pm(flag: bool) -> str:
    return "+" if flag else "-"


def _speed_list(max_gen: int) -> str:
    return f"2.5-{LSPCI_SPEED[max_gen]}" if max_gen > 1 else "2.5GT/s"


def lspci_view(trace: Dict[str, Any]) -> str:
    enum = {f["port"]: f for f in trace["enumeration"]["functions"]}
    topo_ports = {p["name"]: p for p in trace["topology"]["ports"]}
    out = [f"lspci -vvv style excerpt per port ({MODEL_NOTE})", rule()]
    kinds = {
        "root_port": "PCI bridge",
        "switch_upstream": "PCI bridge",
        "switch_downstream": "PCI bridge",
        "endpoint": "Endpoint",
    }
    aspm = trace["config"].get("aspm_l1")
    for name, p in trace["ports"].items():
        tp = topo_ports[name]
        fn = enum.get(name)
        bdf = fn["bdf"] if fn else "--:--.-"
        out.append(f"{bdf} {kinds[tp['kind']]}: {p['label']}   [port {name}, link {p['link']}]")
        if fn is None:
            out.append("        note: not visible to software (the link above it is not up);")
            out.append("              the values below are the model's internal state")
        cap_gen = parse_gen(p["max_speed"])
        cur_gen = parse_gen(p["speed"])
        cap_w = int(p["max_width"][1:])
        cur_w = int(p["width"][1:])
        # pciutils 3.8+ marks "(downgraded)" only on upstream-facing ports
        # (switch upstream ports, endpoints) and prints no marker when equal.
        upstream_facing = tp["kind"] in ("switch_upstream", "endpoint")
        sp_note = " (downgraded)" if upstream_facing and cur_gen < cap_gen else ""
        w_note = " (downgraded)" if upstream_facing and cur_w < cap_w else ""
        out.append(f"        LnkCap:  Speed {LSPCI_SPEED[cap_gen]}, Width {p['max_width']}, ASPM L1")
        out.append(f"        LnkCtl:  ASPM {'L1 Enabled' if aspm else 'Disabled'}; RCB 64 bytes, LnkDisable- CommClk+")
        out.append(f"        LnkSta:  Speed {LSPCI_SPEED[cur_gen]}{sp_note}, Width {p['width']}{w_note}")
        out.append(
            f"                 TrErr- Train{_pm(p['link_training'])} SlotClk+ DLActive{_pm(p['dl_link_active'])} "
            "BWMgmt- ABWMgmt-"
        )
        out.append(f"        LnkCap2: Supported Link Speeds: {_speed_list(cap_gen)}")
        out.append(f"        LnkCtl2: Target Link Speed: {LSPCI_SPEED[parse_gen(p['target_link_speed'])]}")
        if cap_gen >= 3:
            e = p["eq"]["Gen3"]
            out.append(
                f"        LnkSta2: EqualizationComplete{_pm(e['complete'])} EqualizationPhase1{_pm(e['phase1'])}"
            )
            out.append(
                f"                 EqualizationPhase2{_pm(e['phase2'])} EqualizationPhase3{_pm(e['phase3'])} "
                f"LinkEqualizationRequest{_pm(e['link_eq_request'])}"
            )
        if cap_gen >= 4:
            e = p["eq"]["Gen4"]
            out.append(
                f"        Phy16Sta: EquComplete{_pm(e['complete'])} EquPhase1{_pm(e['phase1'])} "
                f"EquPhase2{_pm(e['phase2'])} EquPhase3{_pm(e['phase3'])} LinkEquRequest{_pm(e['link_eq_request'])}"
            )
        out.append(f"        CESta:   RxErr{_pm(p['rx_errors'] > 0)}   (model receiver error count: {p['rx_errors']})")
        if p["lane_reversed"]:
            out.append("        (model) lane reversal applied on this port")
        out.append("")
    out.append("How to read it: compare LnkSta with LnkCap yourself, on BOTH ends of the link.")
    out.append("As in pciutils 3.8+, '(downgraded)' is printed only on upstream-facing ports")
    out.append("(switch upstream ports, endpoints) and nothing is printed when the values match.")
    out.append("(pciutils 3.6-3.7 printed (ok)/(downgraded) on every port.) Lower than this")
    out.append("port's LnkCap is expected when the partner is slower or narrower; it is a")
    out.append("problem only when the link is below what BOTH ends support.")
    return "\n".join(out)


def enumerate_view(trace: Dict[str, Any]) -> str:
    out = [f"Bus tree after training: {trace['scenario']['title']}", rule()]
    out += enumeration_block(trace, with_table=True)
    out.append("")
    out.append("Reading it: each bridge shows [secondary-subordinate] bus numbers. Buses are")
    out.append("assigned depth-first; a link that is not up hides everything below it.")
    return "\n".join(out)
