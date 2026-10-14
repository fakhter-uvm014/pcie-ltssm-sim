"""Replace the GitHub username placeholder in links.

Author: Farzana Akhter

The README badges, the pyproject URLs and the viewer links point at
``https://github.com/fakhter-uvm/pcie-ltssm-sim``. If you publish the
repository under another account, run (from the repository root):

    python scripts/set_github_user.py NEWNAME

Files rewritten: README.md, CONTRIBUTING.md, pyproject.toml and every
.html, .md and .js file under docs/. The script prints each file it changes.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PLACEHOLDER = "fakhter-uvm"
ROOT = Path(__file__).resolve().parent.parent


def target_files() -> list:
    files = [ROOT / "README.md", ROOT / "CONTRIBUTING.md", ROOT / "pyproject.toml"]
    docs = ROOT / "docs"
    for pattern in ("*.html", "*.md", "*.js"):
        files.extend(sorted(docs.rglob(pattern)))
    return [f for f in files if f.is_file()]


def main(argv: list) -> int:
    if len(argv) != 1 or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", argv[0]):
        print("usage: python scripts/set_github_user.py NEWNAME   (a valid GitHub username)")
        return 2
    new = argv[0]
    changed = 0
    for path in target_files():
        text = path.read_text(encoding="utf-8")
        if PLACEHOLDER not in text:
            continue
        with path.open("w", encoding="utf-8", newline="\n") as fh:
            fh.write(text.replace(PLACEHOLDER, new))
        changed += 1
        print(f"updated {path.relative_to(ROOT).as_posix()}")
    print(f"{changed} file(s) changed; '{PLACEHOLDER}' -> '{new}'")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
