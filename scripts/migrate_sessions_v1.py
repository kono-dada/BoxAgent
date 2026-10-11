"""Copy Product Sessions into unified streams, or select the legacy snapshot.

Stop the engine before migration. Rollback retains both directory trees;
legacy data does not contain activity created after switching to unified data.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from boxagent.bootstrap.session_migration import migrate_sessions, rollback_sessions  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--rollback", action="store_true")
    args = parser.parse_args()
    if args.rollback:
        rollback_sessions(args.data_dir)
        print("已导出并切换到包含全部正式历史的 legacy 投影；原始目录与统一事件流均保留。")
    else:
        print(json.dumps(asyncio.run(migrate_sessions(args.data_dir)), ensure_ascii=False))


if __name__ == "__main__":
    main()
