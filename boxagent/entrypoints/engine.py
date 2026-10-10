"""Start the standalone BoxAgent Engine process."""

import argparse
import asyncio
import signal
from pathlib import Path


async def run(args):
    from boxagent.bootstrap.engine import create_application
    from boxagent.bootstrap.settings import load_settings
    from boxagent.interfaces.engine.server import EngineServer

    settings = load_settings(log_dir=args.log_dir)
    server = EngineServer(
        args.socket,
        lambda publish: create_application(
            publish,
            task_provider=args.task_provider,
            task_model=args.task_model,
            auto_approve=not args.require_approval,
            app_settings=settings,
            context_interval=args.context_interval,
            context_size=args.context_size,
        ),
        start_context=args.context_interval > 0,
    )
    loop = asyncio.get_running_loop()
    for name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(name, server.request_stop)
    await server.run()


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
