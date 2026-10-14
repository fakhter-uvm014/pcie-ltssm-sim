"""Trace events and JSON export.

Author: Farzana Akhter

Every state change of every port becomes one trace event::

    {"t_ns": 13725676, "kind": "state", "port": "RP", "link": "A",
     "from": "Recovery.RcvrLock", "to": "Recovery.RcvrCfg",
     "reason": "received 8 consecutive TS1/TS2 on all 16 lanes at Gen4",
     "speed": "Gen4", "width": "x16", "rule": "T44"}

Event kinds:

* ``state``  - an LTSSM substate transition (drives the timeline swimlanes).
  ``rule`` is the id of the transition-table row that fired.
* ``status`` - a register-like status change that is not a substate change:
  DL_Link_Active, equalization-complete bits, data-rate switch, a switch port
  being held or released, an L0 receiver error.
* ``eq``     - one equalization preset trial (preset, figure of merit, verdict).
* ``fault``  - a fault-injection action (hidden in mystery mode).

``detail`` is optional extra data. ``detail.cause`` names the injected fault
behind an event; mystery mode strips it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


class Trace:
    """Collects events in the order they happen."""

    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []

    def add(
        self,
        t_ns: int,
        kind: str,
        port: str,
        link: str,
        frm: str,
        to: str,
        reason: str,
        speed: str,
        width: str,
        rule: Optional[str] = None,
        detail: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not reason:
            raise ValueError("every trace event needs a human-readable reason")
        event: Dict[str, Any] = {
            "t_ns": int(t_ns),
            "kind": kind,
            "port": port,
            "link": link,
            "from": frm,
            "to": to,
            "reason": reason,
            "speed": speed,
            "width": width,
        }
        if rule is not None:
            event["rule"] = rule
        if detail:
            event["detail"] = detail
        self.events.append(event)
        return event

    def state_events(self, port: Optional[str] = None) -> List[Dict[str, Any]]:
        return [
            e for e in self.events if e["kind"] == "state" and (port is None or e["port"] == port)
        ]


def write_json(path: Path, data: Dict[str, Any]) -> None:
    """Write JSON with indent=2, UTF-8 and LF line endings on every OS."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, sort_keys=False, ensure_ascii=False) + "\n"
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def read_json(path: Path) -> Dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as fh:
        return json.load(fh)
