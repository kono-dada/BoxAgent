#!/usr/bin/env python3
"""Command-line entry point for the vendored BoxAgent Jev-Mem worker."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from boxagent.infrastructure.memory.jev_mem.worker import main


if __name__ == "__main__":
    main()
