# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "pyaudio>=0.2.14",
#   "websockets>=15,<17",
#   "pyobjc-framework-Cocoa>=11,<13",
#   "pyobjc-framework-Quartz>=11,<13",
#   "pillow>=11,<13",
# ]
# ///
"""uv 直接启动桌宠；不生成 .app，不修改已有 MLX 环境。"""

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if __name__ == "__main__":
    if "--check-install" in sys.argv:
        from scripts.check_install import main
        sys.exit(main())
    elif "--check" in sys.argv:
        import unittest
        suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        sys.exit(0 if result.wasSuccessful() else 1)
    elif "--check-context" in sys.argv:
        from scripts.check_window_summary import main
        asyncio.run(main())
    elif any(flag in sys.argv for flag in ("--check-ui", "--check-vrm-ui", "--check-pet-size-ui", "--smoke-desktop")):
        from scripts.check_electron_ui import main
        sys.exit(main())
    elif "--smoke" in sys.argv:
        sys.argv.remove("--smoke")
        from scripts.smoke_pet import main
        asyncio.run(main())
    else:
        from boxagent.__main__ import main
        main()
