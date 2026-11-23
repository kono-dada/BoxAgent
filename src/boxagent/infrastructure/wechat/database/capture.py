"""Capture login KDF input from one validated replica; never logs key bytes."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import select
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from boxagent.infrastructure.wechat.database._authentication import (
    KeyCandidate,
    authenticate_first_page,
)
from boxagent.infrastructure.wechat.database.keys import read_first_page
from boxagent.infrastructure.wechat.probe import (
    another_replica_running,
    matching_pids,
    replica_process,
)
from boxagent.infrastructure.wechat.replica import check_client


class KeyCaptureError(RuntimeError):
    pass


def parse_capture(output: str) -> bytes | None:
    values: list[int] = []
    for line in output.splitlines():
        match = re.fullmatch(r'\s*0x[0-9a-f]+:\s+((?:0x[0-9a-f]{2}\s*)+)', line)
        if match:
            values.extend(int(value, 16) for value in re.findall(r'0x([0-9a-f]{2})', match[1]))
    return bytes(values[:32]) if len(values) >= 48 else None


def derive_material(passphrase: bytes, root: Path) -> dict[str, str]:
    if len(passphrase) != 32:
        raise KeyCaptureError('wechat_keys_invalid')
    result: dict[str, str] = {}
    root = root.expanduser().resolve(strict=True)
    for file in sorted(root.rglob('*.db')):
        if file.is_symlink() or not file.resolve().is_relative_to(root):
            raise KeyCaptureError('wechat_database_alias_rejected')
        page = read_first_page(file)
        if page is None:
            continue
        key = hashlib.pbkdf2_hmac('sha512', passphrase, page[:16], 256000, 32)
        if authenticate_first_page(page, KeyCandidate(key, page[:16])) != 'derived-le':
            raise KeyCaptureError('wechat_keys_invalid')
        result[file.relative_to(root).as_posix()] = key.hex()
    required = {"contact/contact.db", "session/session.db"}
    if not required <= result.keys() or not any(
        name.startswith("message/message_") for name in result
    ):
        raise KeyCaptureError("wechat_databases_missing")
    return result


def _capture_pid(pid: int, lldb: Path, *, cancelled: Callable[[], bool], deadline: float,
                 executable: Path | None = None) -> bytes | None:
    # A private FIFO remains writable while LLDB is stopped, allowing bounded detach.
    with tempfile.TemporaryDirectory(prefix='boxagent-wechat-kdf-') as directory:
        fifo = Path(directory)/'commands'
        os.mkfifo(fifo, 0o600)
        descriptor = os.open(fifo, os.O_RDWR)
        process: subprocess.Popen[bytes] | None = None
        def send(value: str) -> None:
            with contextlib.suppress(OSError):
                os.write(descriptor, value.encode())
        try:
            process = subprocess.Popen(
                [str(lldb), "--no-lldbinit"],
                stdin=descriptor,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env={**os.environ, "TERM": "dumb"},
            )
            assert process.stdout is not None
            attach = (
                "target create "
                f"{json.dumps(str(executable), ensure_ascii=False)}"
                "\nprocess launch --stop-at-entry\n"
                if executable is not None
                else f"process attach -p {pid}\n"
            )
            send(
                "settings set target.preload-symbols false\n"
                + attach
                + "breakpoint set -n CCKeyDerivationPBKDF -c '$x2 == 32 && $x6 == "
                    "256000'\nbreakpoint command add 1\nmemory read --size 1 --count 32 --format "
                    "x $x1\nmemory read --size 1 --count 16 --format x $x3\nprocess "
                    "detach\nDONE\nprocess continue\n"
            )
            output = ""
            stopped_at: float | None = None
            while process.poll() is None and time.monotonic() < deadline and not cancelled():
                ready, _, _ = select.select([process.stdout], [], [], .2)
                if ready:
                    chunk = os.read(process.stdout.fileno(), 65536)
                    if not chunk:
                        break
                    output = (output+chunk.decode('utf-8', errors='replace'))[-262144:]
                    if found := parse_capture(output):
                        return found
                    # Inspect only new output. An initial attach stop must not
                    # remain a permanent timeout after the target resumes.
                    for line in chunk.decode('utf-8', errors='replace').splitlines():
                        if 'stop reason =' in line:
                            stopped_at = time.monotonic()
                        elif 'resuming' in line or 'detached' in line:
                            stopped_at = None
                    if "error: attach failed" in output or "Not allowed to attach" in output:
                        raise KeyCaptureError("wechat_debug_permission_missing")
                    if executable is not None and (
                        "error: process exited with status" in output
                        or "error: Cannot launch" in output
                        or "error: process launch failed" in output
                    ):
                        raise KeyCaptureError("wechat_client_launch_failed")
                    if "exited with status" in output:
                        raise KeyCaptureError("wechat_client_exited")
                    if "detached" in output:
                        return None
                if stopped_at is not None and time.monotonic()-stopped_at > 5:
                    raise KeyCaptureError('wechat_capture_stopped')
            return None
        finally:
            # Never pkill system debugservers or touch another debugger session.
            send('process detach\nquit\n')
            if process is not None:
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=1)
            os.close(descriptor)


def capture_passphrase(app: Path, *, lldb: Path, cancelled: Callable[[], bool],
                       timeout: float = 300, launch: bool = False) -> bytes:
    check_client(app)
    if not lldb.is_file() or not os.access(lldb, os.X_OK):
        raise KeyCaptureError('wechat_debugger_missing')
    if str(lldb) == '/usr/bin/lldb' and subprocess.run(
            ['/usr/bin/xcode-select', '-p'], capture_output=True, timeout=10).returncode != 0:
        raise KeyCaptureError('wechat_debugger_missing')
    deadline = time.monotonic()+timeout
    if launch:
        if (
            matching_pids((app / "Contents/MacOS/WeChat").resolve(strict=True))
            or another_replica_running()
        ):
            raise KeyCaptureError("wechat_close_replica_first")
        found = _capture_pid(
            0,
            lldb,
            cancelled=cancelled,
            deadline=deadline,
            executable=(app / "Contents/MacOS/WeChat").resolve(strict=True),
        )
        if found is not None:
            return found
        raise KeyCaptureError('cancelled' if cancelled() else 'wechat_capture_timeout')
    while time.monotonic() < deadline and not cancelled():
        try:
            pid, _ = replica_process(app)
        except (OSError, RuntimeError, subprocess.SubprocessError):
            time.sleep(.2)
            continue
        found = _capture_pid(pid, lldb, cancelled=cancelled, deadline=deadline)
        if found is not None:
            return found
        time.sleep(.2)
    raise KeyCaptureError('cancelled' if cancelled() else 'wechat_capture_timeout')


def capture_keys(
    app: Path, root: Path, *, lldb: Path, cancelled: Callable[[], bool], timeout: float = 300
) -> dict[str, str]:
    return derive_material(
        capture_passphrase(app, lldb=lldb, cancelled=cancelled, timeout=timeout), root
    )
