"""Write docs/guide/scenario-catalog.md from the scenario files.

Author: Farzana Akhter

Usage (from the repository root):

    python scripts/gen_scenario_catalog.py

The catalog is built from scenarios/*.yaml so it cannot drift from them;
tests/test_repo.py checks that the committed file is up to date.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ltssm.scenario import list_scenarios  # noqa: E402

CATALOG = ROOT / "docs" / "guide" / "scenario-catalog.md"

HEADER = [
    "# Scenario catalog",
    "",
    "Every scenario with its stimulus, expected outcome and learning point. Run",
    "`python -m ltssm explain <ID>` for the full notes (what to look for, likely",
    "root causes on real hardware, how to debug), `python -m ltssm run <ID>` to",
    "check it, and `python -m ltssm run <ID> --hidden` to practice diagnosing it.",
    "",
    "S01 to S12 are the core set; S13 to S16 add a speed-policy comparison, a",
    "link that goes down, a marginal lane and a software speed cap.",
    "",
    "This file is produced by `python scripts/gen_scenario_catalog.py` from the",
    "scenario files.",
    "",
    "| ID | Title | Topology | Stimulus | Expected outcome |",
    "|---|---|---|---|---|",
]

ORDER = [
    "",
    "## Suggested learning order",
    "",
    "1. **S01**, the healthy baseline: learn what normal looks like and keep it open for comparison.",
    "2. **S13**: the same hardware with a different speed policy. Paths can differ while results match.",
    "3. **S02** and **S16**: slower than expected, but nothing failed.",
    "4. **S06** and **S12**: width and lane numbering.",
    "5. **S04** and **S05**: equalization failures and fallback.",
    "6. **S03**, **S09** and **S08**: links that never come up.",
    "7. **S07**, **S15** and **S11**: recoveries and what causes them.",
    "8. **S10** and **S14**: links going down, on purpose and by accident.",
    "9. Mystery mode: `python -m ltssm quiz` until you get them right without the hints.",
    "",
]


def build() -> str:
    rows, details = [], []
    for s in list_scenarios():
        n = s.notes()
        stim = "; ".join(n["faults"] + n["commands"] + n["settings"]) or "none (healthy)"
        rows.append(f"| {s.id} | {s.title} | {n['topology']} | {stim} | {n['expected']['summary']} |")
        tries = f"**Try:** `python -m ltssm run {s.id} --trace`"
        if s.quiz:
            tries += f" or `python -m ltssm run {s.id} --hidden`"
        details += [
            f"## {s.id}: {s.title}", "", n["description"], "",
            f"**Stimulus:** {stim}", "",
            f"**Expected:** {n['expected']['summary']}", "",
            f"**Learning point:** {n['what_this_means']}", "",
            tries, "",
        ]
    return "\n".join(HEADER + rows + ORDER + details).rstrip() + "\n"


def main() -> int:
    with CATALOG.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(build())
    print(f"wrote {CATALOG.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
