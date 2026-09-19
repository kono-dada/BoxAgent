# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""对照标准管道和本地 socketpair，只读取计算器，不修改权限或身份。"""

import argparse
import ctypes
import json
import os
from pathlib import Path
import selectors
import signal
import socket
import stat
import subprocess
import sys
import time


class CleanSpawn(subprocess.Popen):
    # 仅用于对照：沿用 CPython 文件描述符映射，显式初始化子进程信号状态。
    def _posix_spawn(self, args, executable, env, restore_signals,
                     p2cread, p2cwrite, c2pread, c2pwrite, errread, errwrite):
        actions = [(os.POSIX_SPAWN_CLOSE, fd) for fd in (p2cwrite, c2pread, errread) if fd >= 0]
        actions += [(os.POSIX_SPAWN_DUP2, fd, target) for fd, target in
                    ((p2cread, 0), (c2pwrite, 1), (errwrite, 2)) if fd >= 0]
        defaults = set(signal.valid_signals()) - {signal.SIGKILL, signal.SIGSTOP}
        self.pid = os.posix_spawn(executable, args, env, file_actions=actions,
                                  setsigmask=(), setsigdef=defaults)
        self._child_created = True
        self._close_pipe_fds(p2cread, p2cwrite, c2pread, c2pwrite, errread, errwrite)


class DarwinSpawn(subprocess.Popen):
    # 对照 libuv 的公开 posix_spawn 设置；仅关闭多余 FD、重置信号，不设置身份属性。
    def _posix_spawn(self, args, executable, env, restore_signals,
                     p2cread, p2cwrite, c2pread, c2pwrite, errread, errwrite):
        lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        attr, actions = ctypes.c_void_p(), ctypes.c_void_p()
        ptr = ctypes.byref
        def check(code):
            if code:
                raise OSError(code, os.strerror(code))
        check(lib.posix_spawnattr_init(ptr(attr)))
        check(lib.posix_spawn_file_actions_init(ptr(actions)))
        try:
            check(lib.posix_spawnattr_setflags(ptr(attr), ctypes.c_short(0x4000 | 0x0004 | 0x0008)))
            full, empty = ctypes.c_uint32(), ctypes.c_uint32()
            check(lib.sigfillset(ptr(full)))
            check(lib.sigemptyset(ptr(empty)))
            check(lib.posix_spawnattr_setsigdefault(ptr(attr), ptr(full)))
            check(lib.posix_spawnattr_setsigmask(ptr(attr), ptr(empty)))
            for fd, target in ((p2cread, 0), (c2pwrite, 1), (errwrite, 2)):
                if fd >= 0:
                    check(lib.posix_spawn_file_actions_adddup2(ptr(actions), fd, target))
                    check(lib.posix_spawn_file_actions_addclose(ptr(actions), fd))
            argv = (ctypes.c_char_p * (len(args) + 1))(*(os.fsencode(x) for x in args), None)
            entries = [os.fsencode(f"{key}={value}") for key, value in env.items()]
            envp = (ctypes.c_char_p * (len(entries) + 1))(*entries, None)
            pid = ctypes.c_int()
            check(lib.posix_spawn(ptr(pid), os.fsencode(executable), ptr(actions), ptr(attr), argv, envp))
            self.pid, self._child_created = pid.value, True
            self._close_pipe_fds(p2cread, p2cwrite, c2pread, c2pwrite, errread, errwrite)
        finally:
            lib.posix_spawn_file_actions_destroy(ptr(actions))
            lib.posix_spawnattr_destroy(ptr(attr))


def run(transport, settle=0):
    executable = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / (
        "computer-use/Codex Computer Use.app/Contents/SharedSupport/"
        "SkyComputerUseClient.app/Contents/MacOS/SkyComputerUseClient"
    )
    env = {key: os.environ[key] for key in
           ("HOME", "PATH", "TMPDIR", "LANG", "CODEX_HOME") if key in os.environ}
    endpoints = []
    if transport in ("socket", "darwin-socket"):
        # 使用本进程真实创建的连接，不传入或伪造任何身份凭据。
        parent_in, child_in = socket.socketpair()
        parent_out, child_out = socket.socketpair()
        endpoints = [parent_in, child_in, parent_out, child_out]
        launcher = DarwinSpawn if transport == "darwin-socket" else subprocess.Popen
        process = launcher([str(executable), "mcp"], stdin=child_in,
                           stdout=child_out, stderr=subprocess.PIPE, env=env,
                           close_fds=transport != "darwin-socket")
        child_in.close()
        child_out.close()
        input_fd, output_fd = parent_in.fileno(), parent_out.fileno()
    else:
        launcher = {"spawn": CleanSpawn, "darwin": DarwinSpawn}.get(transport, subprocess.Popen)
        process = launcher([str(executable), "mcp"], stdin=subprocess.PIPE,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                           close_fds=transport not in ("spawn", "darwin"))
        input_fd, output_fd = process.stdin.fileno(), process.stdout.fileno()
    started = time.monotonic()
    records = []

    def log(stage, **data):
        item = {"transport": transport, "elapsedMs": round((time.monotonic() - started) * 1000),
                "stage": stage, **data}
        records.append(item)
        print(json.dumps(item, ensure_ascii=False), flush=True)

    def send(message):
        data = (json.dumps({"jsonrpc": "2.0", **message}, separators=(",", ":")) + "\n").encode()
        while data:
            data = data[os.write(input_fd, data):]

    selector = selectors.DefaultSelector()
    selector.register(output_fd, selectors.EVENT_READ)
    selector.register(process.stderr, selectors.EVENT_READ)
    buffer = b""
    log("spawn", pid=process.pid, pythonExecutable=sys.executable, pythonVersion=sys.version,
        stdinIsSocket=stat.S_ISSOCK(os.fstat(input_fd).st_mode),
        stdinIsPipe=stat.S_ISFIFO(os.fstat(input_fd).st_mode))
    try:
        send({"id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "boxagent-python-transport-test", "version": "0.1.0"},
        }})
        complete, passed = False, False
        while not complete and time.monotonic() - started < 8:
            for key, _ in selector.select(timeout=0.2):
                chunk = os.read(key.fd, 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                if key.fd != output_fd:
                    log("stderr", text=chunk.decode(errors="replace"))
                    continue
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    message = json.loads(line)
                    if "method" in message and "id" in message:
                        accepted = (message["method"] == "elicitation/create"
                                    and message.get("params", {}).get("message") == "Allow ChatGPT to use Calculator?")
                        log("callback", method=message["method"], accepted=accepted)
                        send({"id": message["id"], "result": {"action": "accept", "content": {}}
                              if accepted else {"action": "decline"}})
                    elif message.get("id") == 1:
                        log("initialize", result=message)
                        send({"method": "notifications/initialized"})
                        send({"id": 2, "method": "tools/list", "params": {}})
                    elif message.get("id") == 2:
                        log("tools", count=len(message.get("result", {}).get("tools", [])))
                        if settle:
                            time.sleep(settle)
                        send({"id": 3, "method": "tools/call", "params": {
                            "name": "get_app_state", "arguments": {"app": "com.apple.calculator"},
                        }})
                    elif message.get("id") == 3:
                        result = message.get("result", {})
                        log("app_state", error=message.get("error"), isError=result.get("isError", False),
                            text=[x.get("text", "")[:300] for x in result.get("content", []) if x.get("type") == "text"],
                            imageCount=sum(x.get("type") == "image" for x in result.get("content", [])))
                        passed = not message.get("error") and not result.get("isError") and bool(result.get("content"))
                        complete = True
        if not complete:
            log("timeout")
    finally:
        selector.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for endpoint in endpoints:
            endpoint.close()
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream:
                stream.close()
        output = Path(__file__).resolve().parents[1] / ".runtime/private/artifacts/computer-use/python-diagnosis"
        output.mkdir(parents=True, exist_ok=True)
        (output / f"{transport}.jsonl").write_text(
            "\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n", encoding="utf-8")
    return passed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("transport", choices=["pipe", "socket", "spawn", "darwin", "darwin-socket"])
    parser.add_argument("--settle", type=float, default=0)
    args = parser.parse_args()
    sys.exit(0 if run(args.transport, args.settle) else 1)
