"""Stdio RPC child owned by Electron; EOF flushes Engine and resource shutdown."""
from __future__ import annotations

import argparse
import json
import signal
import sys
from pathlib import Path

from boxagent.bootstrap.electron import create_electron_services
from boxagent.bootstrap.logs import configure_logging, migrate_legacy_logs, shutdown_logging
from boxagent.bootstrap.settings import load_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="BoxAgent Electron local services")
    parser.add_argument("--pet", type=Path)
    parser.add_argument("--log-dir", type=Path)
    parser.add_argument("--task-provider", default="codex", choices=("codex", "deepseek"))
    parser.add_argument("--task-model")
    parser.add_argument("--require-approval", action="store_true")
    parser.add_argument("--context-interval", type=float, default=0)
    parser.add_argument("--context-size", type=int, default=960)
    args = parser.parse_args()
    settings = load_settings(log_dir=args.log_dir)
    configure_logging(settings.log_dir, process="desktop-service")
    migrate_legacy_logs(settings.data_dir, settings.log_dir)
    service = create_electron_services(settings, pet_directory=args.pet,
        task_provider=args.task_provider, task_model=args.task_model,
        auto_approve=not args.require_approval, context_interval=args.context_interval,
        context_size=args.context_size)

    def stop(_signum: int, _frame: object) -> None:
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        for line in sys.stdin:
            request: dict[str, object] = {}
            try:
                value = json.loads(line)
                if not isinstance(value, dict) or not isinstance(value.get("params", {}), dict):
                    raise ValueError("桌面服务请求格式无效")
                request = value
                result = service.dispatch(str(value.get("method", "")), value.get("params", {}))
                response = {"id": request.get("id"), "ok": True, "result": result}
            except Exception as error:
                response = {"id": request.get("id"), "ok": False, "error": str(error)}
            print(json.dumps(response, ensure_ascii=False), flush=True)
    finally:
        try:
            service.close()
        finally:
            shutdown_logging()


if __name__ == "__main__":
    main()
