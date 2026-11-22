"""Start the standalone BoxAgent Engine process."""

import argparse
import asyncio
import signal
from pathlib import Path

from boxagent.bootstrap.logs import configure_logging, install_asyncio_handler, shutdown_logging


async def run(args):
    from boxagent.bootstrap.engine import create_engine_server
    from boxagent.bootstrap.settings import load_settings

    settings = load_settings(log_dir=args.log_dir)
    configure_logging(settings.log_dir, process="engine")
    install_asyncio_handler()
    server = create_engine_server(
        args.socket, app_settings=settings,
        task_provider=args.task_provider, task_model=args.task_model,
        auto_approve=not args.require_approval,
        context_interval=args.context_interval, context_size=args.context_size)
    loop = asyncio.get_running_loop()
    for name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(name, server.request_stop)
        except NotImplementedError:
            signal.signal(name, lambda *_args: loop.call_soon_threadsafe(server.request_stop))
    try:
        await server.run()
    finally:
        shutdown_logging()


def main():
    parser = argparse.ArgumentParser(description="BoxAgent local Engine")
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--log-dir", type=Path)
    parser.add_argument("--task-provider", choices=("codex", "deepseek"), default="codex")
    parser.add_argument("--task-model")
    parser.add_argument("--require-approval", action="store_true")
    parser.add_argument("--context-interval", type=float, default=0)
    parser.add_argument("--context-size", type=int, default=960)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
