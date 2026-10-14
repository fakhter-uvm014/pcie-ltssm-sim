"""Command-line interface: ``python -m ltssm <command>`` (or ``ltssm <command>``).

Author: Farzana Akhter

Commands
--------
list                          scenarios with their expected outcome
explain <id>                  learning notes for one scenario (no simulation)
run <id|path> [options]       run one scenario; exit code 0 if it matches the expected outcome
    --trace                   also print the event log (--port RP,USP to filter)
    --hidden                  mystery mode: symptoms only
    --reveal                  run, then show the injected fault and the learning notes
    --seed N                  override the scenario seed
    --json-only               print a machine-readable result summary instead of text
run-all                       run every scenario, write traces, publish them to docs/traces/
lspci <id>                    lspci -vvv style excerpt for every port after the run
enumerate <id|topology>       lspci -t style bus tree after training
quiz [--seed N] [--answer N]  a random mystery scenario; guess the cause from a list
table [--markdown]            print the transition table

Add ``--color`` to any command for colored PASS/FAIL.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__
from .paths import docs_dir, repo_root, traces_dir
from .report import (
    enumerate_view,
    event_log,
    explain_view,
    learning_notes,
    list_view,
    lspci_view,
    mystery_view,
    reveal_view,
    run_all_view,
    run_summary,
    wrap,
)
from .scenario import (
    RunResult,
    find_scenario,
    list_scenarios,
    run_scenario,
    topology_scenario,
)
from .topology import find_topology_file
from .trace import write_json
from .transitions import table_as_markdown, table_as_text


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------
def _print(text: str) -> None:
    """Print ASCII safely on any console encoding."""
    data = text + "\n"
    try:
        sys.stdout.write(data)
    except UnicodeEncodeError:  # pragma: no cover - defensive, output is ASCII
        sys.stdout.write(data.encode("ascii", "replace").decode("ascii"))


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(repo_root())).replace("\\", "/")
    except ValueError:
        return str(path)


def write_trace(result: RunResult) -> Path:
    path = traces_dir() / f"{_safe_id(result.trace['scenario']['id'])}.json"
    write_json(path, result.trace)
    return path


def _safe_id(text: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in text)


def json_summary(result: RunResult, trace_path: Path) -> Dict[str, Any]:
    t = result.trace
    return {
        "id": t["scenario"]["id"],
        "passed": t["result"]["passed"],
        "trace_file": _relative(trace_path),
        "links": {
            name: {k: link[k] for k in ("state", "speed", "width", "up", "counters")}
            for name, link in t["links"].items()
        },
        "device_count": t["enumeration"]["device_count"],
        "checks": t["result"]["checks"],
    }


def run_all_scenarios(publish: bool = True) -> List[Dict[str, Any]]:
    """Run every scenario, write traces/, and copy them to docs/traces/ with an index."""
    results = []
    manifest = []
    for scn in list_scenarios():
        res = run_scenario(scn)
        path = write_trace(res)
        results.append({"trace": res.trace, "path": path})
        manifest.append(
            {
                "id": scn.id,
                "title": scn.title,
                "file": f"{_safe_id(scn.id)}.json",
                "topology": res.trace["topology"]["name"],
                "description": res.trace["scenario"]["description"],
                "expected": res.trace["scenario"]["expected"]["summary"],
                "passed": res.passed,
            }
        )
    if publish:
        out_dir = docs_dir() / "traces"
        for item in results:
            write_json(out_dir / item["path"].name, item["trace"])
        write_json(out_dir / "index.json", {"format": "pcie-ltssm-sim trace index", "scenarios": manifest})
    return results


# ----------------------------------------------------------------------------
# commands
# ----------------------------------------------------------------------------
def cmd_list(args: argparse.Namespace) -> int:
    _print(list_view(list_scenarios()))
    return 0


def cmd_explain(args: argparse.Namespace) -> int:
    _print(explain_view(find_scenario(args.scenario)))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    scn = find_scenario(args.scenario)
    result = run_scenario(scn, seed=args.seed)
    path = write_trace(result)
    if args.json_only:
        _print(json.dumps(json_summary(result, path), indent=2))
        return 0 if result.passed else 1
    if args.hidden:
        _print(mystery_view(result.trace, color=args.color))
    elif args.reveal:
        _print(reveal_view(result.trace, _relative(path), color=args.color))
    else:
        _print(run_summary(result.trace, _relative(path), color=args.color))
    if args.trace:
        ports = [p.strip() for p in args.port.split(",")] if args.port else None
        kinds = ("state",) if args.hidden else None
        _print("")
        if args.hidden:
            _print("(mystery mode: only state changes are shown in the event log)")
        _print(event_log(_hide(result.trace) if args.hidden else result.trace, ports, kinds))
    return 0 if result.passed else 1


def _hide(trace: Dict[str, Any]) -> Dict[str, Any]:
    """Copy of the trace without fault events and fault causes (mystery mode)."""
    events = []
    for e in trace["events"]:
        if e["kind"] == "fault":
            continue
        e = dict(e)
        if "detail" in e:
            e["detail"] = {k: v for k, v in e["detail"].items() if k != "cause"}
        events.append(e)
    out = dict(trace)
    out["events"] = events
    return out


def cmd_run_all(args: argparse.Namespace) -> int:
    results = run_all_scenarios(publish=not args.no_publish)
    _print(run_all_view(results, color=args.color))
    if not args.no_publish:
        _print(f"Traces written to traces/ and docs/traces/ ({len(results)} scenarios + index.json).")
    return 0 if all(r["trace"]["result"]["passed"] for r in results) else 1


def cmd_lspci(args: argparse.Namespace) -> int:
    result = run_scenario(find_scenario(args.scenario))
    write_trace(result)
    _print(f"{result.trace['scenario']['id']}  {result.trace['scenario']['title']}")
    _print(lspci_view(result.trace))
    return 0


def _scenario_or_topology(ref: str):
    try:
        return find_scenario(ref)
    except (FileNotFoundError, KeyError):
        pass  # not a scenario: try a topology name or file
    path = find_topology_file(ref)
    return topology_scenario(str(path) if Path(ref).is_file() else path.stem)


def cmd_enumerate(args: argparse.Namespace) -> int:
    scn = _scenario_or_topology(args.target)
    result = run_scenario(scn)
    _print(enumerate_view(result.trace))
    return 0


def cmd_table(args: argparse.Namespace) -> int:
    _print(table_as_markdown() if args.markdown else table_as_text())
    return 0


def build_quiz(seed: int):
    rng = random.Random(seed)
    pool = [s for s in list_scenarios() if s.quiz and s.mystery_cause]
    scn = rng.choice(pool)
    others = [s.mystery_cause for s in pool if s.id != scn.id]
    options = [scn.mystery_cause] + rng.sample(others, min(4, len(others)))
    rng.shuffle(options)
    return scn, options, options.index(scn.mystery_cause) + 1


def cmd_quiz(args: argparse.Namespace) -> int:
    seed = args.seed if args.seed is not None else random.SystemRandom().randrange(1000)
    scn, options, correct = build_quiz(seed)
    result = run_scenario(scn)
    write_trace(result)
    _print(f"Quiz seed {seed} (python -m ltssm quiz --seed {seed} repeats this question)")
    _print(mystery_view(result.trace, color=args.color))
    _print("")
    _print("Which root cause best explains these symptoms?")
    for i, text in enumerate(options, 1):
        lines = wrap(text, indent="     ")
        _print(f"  {i}. {lines[0].strip()}")
        for line in lines[1:]:
            _print(line)
    answer: Optional[int] = args.answer
    if answer is None:
        if not sys.stdin.isatty():
            _print("")
            _print(f"Answer with: python -m ltssm quiz --seed {seed} --answer N")
            return 0
        try:
            raw = input(f"Your answer (1-{len(options)}): ").strip()
            answer = int(raw)
        except (EOFError, ValueError):
            _print("No valid answer given.")
            _print(f"Answer with: python -m ltssm quiz --seed {seed} --answer N")
            return 0
    _print("")
    if answer == correct:
        _print("\n".join(wrap(f"Correct: {correct}. {options[correct - 1]}", indent="")))
    else:
        _print("\n".join(wrap(f"Not quite. You chose {answer}; the answer is {correct}. "
                              f"{options[correct - 1]}", indent="")))
    _print("")
    _print(f"Scenario {scn.id}: {scn.title}")
    _print("\n".join(learning_notes(result.trace["scenario"])))
    return 0


# ----------------------------------------------------------------------------
# argument parsing
# ----------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ltssm",
        description="PCIe LTSSM behavioral simulator (learning model; not spec-accurate, not cycle-accurate).",
    )
    parser.add_argument("--version", action="version", version=f"pcie-ltssm-sim {__version__}")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--color", action="store_true", help="color PASS/FAIL (ANSI)")
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")

    p = sub.add_parser("list", parents=[common], help="list scenarios")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("explain", parents=[common], help="print the learning notes of a scenario")
    p.add_argument("scenario", help="scenario ID (S04) or path")
    p.set_defaults(func=cmd_explain)

    p = sub.add_parser("run", parents=[common], help="run one scenario")
    p.add_argument("scenario", help="scenario ID (S04) or path to a YAML file")
    p.add_argument("--trace", action="store_true", help="print the event log")
    p.add_argument("--port", default="", help="with --trace: comma-separated ports to show")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--hidden", action="store_true", help="mystery mode: symptoms only")
    mode.add_argument("--reveal", action="store_true", help="show the injected fault and learning notes")
    p.add_argument("--seed", type=int, default=None, help="override the scenario seed")
    p.add_argument("--json-only", action="store_true", help="print a JSON result summary only")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("run-all", parents=[common], help="run every scenario and publish traces")
    p.add_argument("--no-publish", action="store_true", help="do not copy traces into docs/traces/")
    p.set_defaults(func=cmd_run_all)

    p = sub.add_parser("lspci", parents=[common], help="lspci -vvv style view after a run")
    p.add_argument("scenario")
    p.set_defaults(func=cmd_lspci)

    p = sub.add_parser("enumerate", parents=[common], help="bus tree after training")
    p.add_argument("target", help="scenario ID, topology name (basic) or topology file")
    p.set_defaults(func=cmd_enumerate)

    p = sub.add_parser("quiz", parents=[common], help="guess the root cause of a hidden scenario")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--answer", type=int, default=None, help="answer non-interactively")
    p.set_defaults(func=cmd_quiz)

    p = sub.add_parser("table", parents=[common], help="print the transition table")
    p.add_argument("--markdown", action="store_true")
    p.set_defaults(func=cmd_table)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except FileNotFoundError as exc:
        _print(f"error: {exc}")
        return 2
    except (KeyError, ValueError) as exc:
        _print(f"error: {exc}")
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
