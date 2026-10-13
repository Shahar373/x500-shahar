"""PyInstaller entry script (keeps package-relative imports working)."""

import sys

from x500fs.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
