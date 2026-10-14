"""Allow ``python -m ltssm``.

Author: Farzana Akhter
"""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
