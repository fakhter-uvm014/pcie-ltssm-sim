"""Repository checks: scenario files, viewer files and wording rules.

Author: Farzana Akhter
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from ltssm.scenario import list_scenarios

ROOT = Path(__file__).resolve().parent.parent
TEXT_SUFFIXES = {".py", ".md", ".yaml", ".yml", ".toml", ".html", ".js", ".css", ".json", ".txt", ".cfg"}
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "build", "dist", "report"}


def _text_files():
    for path in ROOT.rglob("*"):
        if any(part in SKIP_DIRS or part.endswith(".egg-info") for part in path.relative_to(ROOT).parts):
            continue
        if path.is_file() and (path.suffix in TEXT_SUFFIXES or path.name in ("LICENSE", ".gitignore")):
            if path.parent.name == "traces" and path.parent.parent == ROOT:
                continue  # local run output, not committed
            if path.name == "report.html":
                continue  # pytest-html output, git-ignored
            yield path


REQUIRED_NOTES = ("what_to_look_for", "what_this_means", "likely_root_causes", "how_to_debug", "symptom")


@pytest.mark.parametrize("scn", list_scenarios(), ids=lambda s: s.id)
def test_scenario_files_have_learning_notes(scn):
    for key in REQUIRED_NOTES:
        assert getattr(scn, key), f"{scn.id}: {key} is empty"
    assert scn.expected.get("summary") and scn.expected.get("checks")
    assert Path(scn.source).name.startswith(scn.id + "_")
    text = Path(scn.source).read_text(encoding="utf-8")
    assert all(ord(ch) < 128 for ch in text), "scenario text is printed to the console: keep it ASCII"


def test_viewer_uses_relative_paths_only():
    for name in ("index.html", "app.js", "style.css"):
        text = (ROOT / "docs" / name).read_text(encoding="utf-8")
        assert not re.search(r"""(src|href)=["']/(?!/)""", text), f"absolute path in docs/{name}"
        assert "fetch('/" not in text and 'fetch("/' not in text


def test_trace_index_lists_existing_files():
    index = json.loads((ROOT / "docs" / "traces" / "index.json").read_text(encoding="utf-8"))
    ids = [s.id for s in list_scenarios()]
    assert [item["id"] for item in index["scenarios"]] == ids
    for item in index["scenarios"]:
        trace = json.loads((ROOT / "docs" / "traces" / item["file"]).read_text(encoding="utf-8"))
        assert trace["scenario"]["id"] == item["id"]
        for key in ("title", "description", "expected", "what_to_look_for", "likely_root_causes", "how_to_debug"):
            assert key in trace["scenario"]
        assert set(trace["result"]) == {"passed", "checks"}


def test_guides_exist():
    for name in ("how-it-works.md", "what-to-expect.md", "debug-guide.md", "interpreting-results.md",
                 "scenario-catalog.md", "real-hardware.md", "glossary.md", "transition-table.md"):
        assert (ROOT / "docs" / "guide" / name).is_file(), name


def test_wording_rules():
    """No dates, no forbidden words (the Polling.Compliance substate name is allowed)."""
    # Words are split so this file does not trip its own check.
    banned = ["compl" + "iant", "certi" + "fied", "TO" + "DO", "clau" + "de", "anthro" + "pic", "generated" + " by"]
    forbidden = re.compile(r"\b(" + "|".join(banned) + r")\b", re.IGNORECASE)
    year = re.compile(r"(?<![0-9.#a-fA-F])(19|20)[0-9][0-9](?![0-9])")  # hex colors like #1d2024 are fine
    compl = re.compile(r"(?<!polling\.)(?<!polling_)" + "compl" + "iance", re.IGNORECASE)
    for path in _text_files():
        if path.suffix == ".json":
            continue  # numeric trace data
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(ROOT).as_posix()
        assert not year.search(text), f"{rel}: looks like a year: {year.search(text).group(0)}"
        assert not forbidden.search(text), f"{rel}: forbidden word {forbidden.search(text).group(0)!r}"
        assert not compl.search(text), f"{rel}: word used outside the Polling.Compliance substate name"


def test_scenario_catalog_is_in_sync():
    import importlib.util

    spec = importlib.util.spec_from_file_location("gen_catalog", ROOT / "scripts" / "gen_scenario_catalog.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    doc = (ROOT / "docs" / "guide" / "scenario-catalog.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert doc == mod.build(), "regenerate with: python scripts/gen_scenario_catalog.py"
