"""Run every scenario, write traces/, and publish them to docs/traces/.

Author: Farzana Akhter

Usage (from the repository root):

    python scripts/run_all.py

This is the same as ``python -m ltssm run-all``. It writes one JSON trace per
scenario to traces/, copies them to docs/traces/ and writes
docs/traces/index.json so the viewer can list them. The exit code is 0 when
every scenario matches its expected outcome, 1 otherwise.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ltssm.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["run-all"] + sys.argv[1:]))
