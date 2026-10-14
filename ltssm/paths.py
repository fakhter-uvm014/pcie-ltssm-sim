"""Find the repository folders (scenarios/, topologies/, traces/, docs/).

Author: Farzana Akhter

The simulator is meant to be run from a clone of the repository, either with
``python -m ltssm`` from the repository root or through the ``ltssm`` command
after ``pip install -e .``. The root is found in this order:

1. the ``LTSSM_HOME`` environment variable, if set;
2. the current working directory, if it contains ``scenarios/``;
3. the folder that contains the ``ltssm`` package (editable installs).
"""

from __future__ import annotations

import os
from pathlib import Path


def repo_root() -> Path:
    candidates = []
    env = os.environ.get("LTSSM_HOME")
    if env:
        candidates.append(Path(env))
    candidates.append(Path.cwd())
    candidates.append(Path(__file__).resolve().parent.parent)
    for c in candidates:
        if (c / "scenarios").is_dir() and (c / "topologies").is_dir():
            return c.resolve()
    raise FileNotFoundError(
        "cannot find the scenarios/ and topologies/ folders. Run from the repository root, "
        "or set LTSSM_HOME to the repository folder."
    )


def scenarios_dir() -> Path:
    return repo_root() / "scenarios"


def topologies_dir() -> Path:
    return repo_root() / "topologies"


def traces_dir() -> Path:
    return repo_root() / "traces"


def docs_dir() -> Path:
    return repo_root() / "docs"
