"""Resolve code and resource locations in a checkout or an app bundle."""

import os
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PACKAGE_ROOT.parent
RESOURCE_ROOT = Path(
    os.environ.get(
        "BOXAGENT_RESOURCE_ROOT",
        SOURCE_ROOT.parent if SOURCE_ROOT.name == "src" else SOURCE_ROOT,
    )
).resolve()
