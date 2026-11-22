"""Local CLI for the composed WeChat module. No Agent or key capture."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, is_dataclass
import json
import logging
import sqlite3
import subprocess
from pathlib import Path
from typing import Sequence
import uuid

from boxagent.application.wechat.module import WeChatModule
from boxagent.application.wechat.service import PeerResolutionError
from boxagent.bootstrap.wechat import DEFAULT_KEYS, REPLICA_APP, ROOT, WeChatConfig, build_local_wechat_module


def jsonable(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        result = asdict(value)
        if hasattr(value, "status"):
            result["status"] = getattr(value, "status")
        return jsonable(result)
    if isinstance(value, (tuple, list)):
        return [jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: str(item) if key == "server_id" and isinstance(item, int) else jsonable(item)
                for key, item in value.items()}
    return value


async def listen(module: WeChatModule, duration: float) -> dict[str, object]:
    if not 0 <= duration <= 86400:
        raise ValueError("Invalid duration")
    try:
        await module.start()
        print(json.dumps({"type": "ready", "monitoring": True, "agent_connected": False}), flush=True)
        await asyncio.sleep(duration)
        return {"status": "stopped", "error": module.monitor.last_error}
    finally:
        await module.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, default=REPLICA_APP)
    parser.add_argument("--db-root", type=Path)
    parser.add_argument("--keys", type=Path, default=DEFAULT_KEYS)
    parser.add_argument("--self-wxid")
    parser.add_argument("--data-dir", type=Path, default=ROOT / ".runtime/pet/wechat")
    commands = parser.add_subparsers(dest="command", required=True)
    contacts = commands.add_parser("contacts")
    contacts.add_argument("query", nargs="?", default="")
    for name in ("sessions", "unread"):
        commands.add_parser(name).add_argument("-n", type=int, default=20)
    contacts.add_argument("-n", type=int, default=100)
    history = commands.add_parser("history")
    history.add_argument("recipient")
    history.add_argument("-n", type=int, default=50)
    search = commands.add_parser("search")
    search.add_argument("query")
    search.add_argument("--in", dest="recipient")
    search.add_argument("-n", type=int, default=50)
    for name in ("members", "draft", "clear"):
        commands.add_parser(name).add_argument("recipient")
    send = commands.add_parser("send")
    send.add_argument("text")
    send.add_argument("recipient")
    send.add_argument("--request-id")
    send.add_argument("--dry-run", action="store_true")
    send.add_argument("--mention")
    monitor = commands.add_parser("listen")
    monitor.add_argument("--peer")
    monitor.add_argument("--interval", type=float, default=0.5)
    monitor.add_argument("--duration", type=float, default=60)
    moments = commands.add_parser("moments")
    moments.add_argument("text")
    moments.add_argument("--visibility", choices=("private", "public"), default="private")
    moments.add_argument("--request-id")
    moments.add_argument("--publish", action="store_true")
    commands.add_parser("doctor")
    args = parser.parse_args(argv)
    if args.command == "send" and not args.dry_run and not args.request_id:
        parser.error("Actual sending requires --request-id; reuse it to prevent duplicate submission")
    if args.command == "moments" and args.publish and not args.request_id:
        parser.error("Publishing requires --request-id")
    logging.basicConfig(level=logging.WARNING)
    try:
        config = WeChatConfig(app=args.app, db_root=args.db_root, key_report=args.keys,
            data_dir=args.data_dir, self_wxid=args.self_wxid,
            watched_peer=args.peer if args.command == "listen" else None,
            interval=args.interval if args.command == "listen" else 0.5)
        module = build_local_wechat_module(config)
        result: object
        if args.command == "contacts":
            result = module.contacts(args.query, limit=args.n)
        elif args.command in {"sessions", "unread"}:
            result = module.sessions(unread_only=args.command == "unread", limit=args.n)
        elif args.command == "history":
            result = module.history(args.recipient, limit=args.n)
        elif args.command == "search":
            result = module.search(args.query, recipient=args.recipient, limit=args.n)
        elif args.command == "members":
            result = module.members(args.recipient)
        elif args.command == "draft":
            peer = module.service.resolve(args.recipient).peer
            result = module.service.transport().draft_for(peer)
        elif args.command == "clear":
            module.clear_draft(args.recipient)
            result = {"status": "cleared"}
        elif args.command == "send":
            result = module.send(args.recipient, args.text, request_id=args.request_id or "dry-"+uuid.uuid4().hex,
                                 dry_run=args.dry_run, mention=args.mention)
        elif args.command == "moments":
            result = module.publish(args.text, args.visibility, request_id=args.request_id or "dry-"+uuid.uuid4().hex,
                                    dry_run=not args.publish)
        elif args.command == "listen":
            result = asyncio.run(listen(module, args.duration))
        else:
            result = module.health()
        payload = jsonable(result)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        if isinstance(payload, dict):
            if payload.get("status") in {"outcome_unknown", "submitted_unconfirmed"}:
                return 3
            if payload.get("status") in {"rejected", "failed"}:
                return 1
            if payload.get("error"):
                return 1
        return 0
    except PeerResolutionError as error:
        print(json.dumps({"status": "needs_input", "error": str(error),
                          "candidates": jsonable(error.candidates)}, ensure_ascii=False))
        return 2
    except (OSError, ValueError, RuntimeError, sqlite3.Error, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
