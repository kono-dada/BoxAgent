#!/usr/bin/env python3
"""Command-line entry point for the vendored BoxAgent Jev-Mem worker."""

import sys
from pathlib import Path

if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    source = root / "src"
    sys.path.insert(0, str(source if source.is_dir() else root))

    from boxagent.infrastructure.memory.jev_mem.worker import main

    main()
