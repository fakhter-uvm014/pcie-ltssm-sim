"""Command-line tests, run as a subprocess the way a user runs them.

Author: Farzana Akhter

Uses ``sys.executable -m ltssm`` from the repository root so it works the
same on Linux, macOS and Windows.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def cli(*args: str, check_ascii: bool = True) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-m", "ltssm", *args],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", env=env, timeout=120,
        stdin=subprocess.DEVNULL,  # never inherit the console (Windows would make the CLI prompt)
    )
    if check_ascii:
        assert all(ord(ch) < 128 for ch in proc.stdout), "console output must be ASCII"
    assert "\x1b[" not in proc.stdout or "--color" in args, "no ANSI codes without --color"
    return proc


def test_list_shows_every_scenario():
    proc = cli("list")
    assert proc.returncode == 0
    for n in range(1, 17):
        assert f"S{n:02d}" in proc.stdout


def test_explain_prints_learning_notes():
    proc = cli("explain", "S07")
    assert proc.returncode == 0
    for section in ("Expected outcome", "What to look for", "Likely root causes", "How to debug"):
        assert section in proc.stdout


def test_run_passes_and_writes_trace():
    proc = cli("run", "S04")
    assert proc.returncode == 0, proc.stdout
    assert "Result: PASS" in proc.stdout
    assert "What this means" in proc.stdout
    assert (ROOT / "traces" / "S04.json").is_file()


def test_run_accepts_a_path_and_lowercase_id():
    path = next((ROOT / "scenarios").glob("S12_*.yaml"))
    assert cli("run", str(path)).returncode == 0
    assert cli("run", "s12").returncode == 0


def test_run_with_trace_prints_the_event_log():
    proc = cli("run", "S04", "--trace", "--port", "EP1")
    assert proc.returncode == 0
    assert "Event log" in proc.stdout
    assert "T62" in proc.stdout  # Phase 2 failure row
    assert "why:" in proc.stdout


def test_hidden_mode_does_not_reveal_the_fault():
    proc = cli("run", "S06", "--hidden")
    assert proc.returncode == 0
    out = proc.stdout
    assert "MYSTERY MODE" in out and "Hypothesis?" in out
    for secret in ("dead_lanes", "lanes 8-15 of link A are open", "Lane failure", "FAULT"):
        assert secret not in out


def test_hidden_trace_hides_fault_events():
    proc = cli("run", "S14", "--hidden", "--trace")
    assert proc.returncode == 0
    assert "FAULT" not in proc.stdout and "connection_lost" not in proc.stdout


def test_reveal_shows_fault_and_notes():
    proc = cli("run", "S06", "--reveal")
    assert proc.returncode == 0
    assert "REVEAL" in proc.stdout
    assert "lanes 8-15 of link A are open" in proc.stdout
    assert "How to debug it on real hardware" in proc.stdout


def test_json_only_is_machine_readable():
    proc = cli("run", "S02", "--json-only")
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert data["passed"] is True and data["links"]["C"]["speed"] == "Gen2"


def test_seed_option_changes_random_faults():
    a = json.loads(cli("run", "S15", "--json-only", "--seed", "7").stdout)
    b = json.loads(cli("run", "S15", "--json-only", "--seed", "8").stdout)
    assert a["links"]["B"]["counters"] != b["links"]["B"]["counters"] or a["checks"] != b["checks"]


def test_lspci_view():
    proc = cli("lspci", "S06")
    assert proc.returncode == 0
    out = proc.stdout
    # pciutils 3.8+ style: no marker on the root port, "(downgraded)" on the switch upstream port
    assert "LnkSta:  Speed 16GT/s, Width x8\n" in out
    assert "LnkSta:  Speed 16GT/s, Width x8 (downgraded)\n" in out
    assert not any("(ok)" in line for line in out.splitlines() if "LnkSta:" in line)
    assert "pciutils 3.6-3.7 printed (ok)/(downgraded) on every port" in out
    assert "not a real lspci run" in proc.stdout


def test_enumerate_scenario_and_topology():
    proc = cli("enumerate", "S09")
    assert proc.returncode == 0 and "1 function(s) found" in proc.stdout
    proc = cli("enumerate", "basic")
    assert proc.returncode == 0 and "02:02.0" in proc.stdout and "7 function(s) found" in proc.stdout
    proc = cli("enumerate", str(ROOT / "topologies" / "point_to_point.yaml"))
    assert proc.returncode == 0 and "2 function(s) found" in proc.stdout


def test_quiz_non_interactive_answer():
    proc = cli("quiz", "--seed", "3", "--answer", "1")
    assert proc.returncode == 0
    assert "Which root cause best explains these symptoms?" in proc.stdout
    assert ("Correct" in proc.stdout) or ("the answer is" in proc.stdout)


def test_quiz_without_answer_explains_how_to_answer():
    proc = cli("quiz", "--seed", "5")
    assert proc.returncode == 0
    assert "--answer N" in proc.stdout


def test_table_command():
    proc = cli("table")
    assert proc.returncode == 0 and "T01" in proc.stdout and "Detect.Quiet" in proc.stdout


def test_mismatch_gives_exit_code_1(tmp_path):
    src = next((ROOT / "scenarios").glob("S01_*.yaml")).read_text(encoding="utf-8")
    wrong = src.replace("id: S01", "id: S99").replace(
        "{check: link, link: A, state: L0, speed: Gen4, width: x16}",
        "{check: link, link: A, state: L0, speed: Gen2, width: x16}",
    )
    path = tmp_path / "S99_wrong_expectation.yaml"
    path.write_text(wrong, encoding="utf-8")
    proc = cli("run", str(path))
    assert proc.returncode == 1
    assert "Result: FAIL" in proc.stdout
    (ROOT / "traces" / "S99.json").unlink(missing_ok=True)


def test_unknown_scenario_is_a_clean_error():
    proc = cli("run", "S77")
    assert proc.returncode == 2 and "error:" in proc.stdout


def test_color_flag_adds_ansi_codes():
    proc = cli("run", "S12", "--color")
    assert "\x1b[32;1mPASS" in proc.stdout


def test_run_all_passes_and_publishes_index():
    proc = cli("run-all")
    assert proc.returncode == 0, proc.stdout
    assert "All 16 scenarios match their expected outcome." in proc.stdout
    index = json.loads((ROOT / "docs" / "traces" / "index.json").read_text(encoding="utf-8"))
    assert len(index["scenarios"]) == 16
    for item in index["scenarios"]:
        assert (ROOT / "docs" / "traces" / item["file"]).is_file()
