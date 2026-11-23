"""Private Host subprocess protocol. Secrets use pipes, never argv or logs."""
from __future__ import annotations

import json
import signal
import sys
import threading
from pathlib import Path
from typing import cast

from boxagent.bootstrap.wechat_onboarding import build_wechat_onboarding


def string(value: dict[str, object], name: str, default: str = '') -> str:
    result = value.get(name, default)
    if not isinstance(result, str) or '\0' in result or '\n' in result:
        raise ValueError('invalid_configuration')
    return result


def main() -> None:
    cancelled = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: cancelled.set())
    signal.signal(signal.SIGINT, lambda *_: cancelled.set())
    try:
        raw: object = json.loads(sys.stdin.buffer.read(1024*1024+1))
        if not isinstance(raw, dict):
            raise ValueError('invalid_configuration')
        request = cast(dict[str, object], raw)
        action = string(request, 'action')
        app = Path(string(request, 'app'))
        setup = build_wechat_onboarding()
        if action == 'prepare':
            result = setup.prepare(Path(string(request, 'source')), app)
        elif action == 'client':
            setup.check_client(app)
            result = {"client": {"state": "ready", "code": ""}}
        elif action == "capture":
            root = string(request, "root")
            result = setup.capture(
                app,
                Path(root) if root else None,
                lldb=Path(string(request, "lldb", "/usr/bin/lldb")),
                cancelled=cancelled.is_set,
            )
        elif action in ("verify", "native"):
            value = request.get("material")
            if not isinstance(value, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in value.items()
            ):
                raise ValueError("wechat_keys_invalid")
            result = setup.verify(
                app,
                Path(string(request, "root")),
                cast(dict[str, str], value),
                lldb=Path(string(request, "lldb", "/usr/bin/lldb")),
                native=action == "native",
            )
        else:
            raise ValueError('invalid_setup_method')
        print(json.dumps({'ok': True, 'result': result}))
    except Exception as error:
        # Do not expose debugger output, keys, local filenames or chat content.
        code = str(error)
        if not code.startswith(("wechat_", "cancelled", "invalid_")) or len(code) > 100:
            code = (
                "wechat_permission_denied"
                if isinstance(error, PermissionError)
                else "wechat_setup_failed"
            )
        print(json.dumps({"ok": False, "code": code}))


if __name__ == '__main__':
    main()
