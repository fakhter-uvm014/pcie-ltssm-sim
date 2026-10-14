"""pytest setup shared by every test module.

Author: Farzana Akhter

Makes the repository importable without installation, so ``pytest`` works
both from a plain clone and after ``pip install -e .``.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
