"""Entry point of the single-file FETCH executable (see scripts/build_exe.py)."""

import multiprocessing
import sys

from fetch_app.__main__ import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
