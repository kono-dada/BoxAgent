"""Qt-main-thread LLDB executor with named results and unconditional detach."""
from __future__ import annotations
from contextlib import contextmanager
import fcntl
import importlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from typing import Iterator, Protocol, cast
import uuid

from boxagent.infrastructure.darwin.mach_reader import MachReader
from boxagent.infrastructure.wechat.probe import Snapshot, discover
from boxagent.infrastructure.wechat._private import object_json, private_dir, write_json

class ExecutionError(RuntimeError):
    """Native evaluation failed. A side-effecting call may have an unknown outcome."""

class ExpressionExecutor(Protocol):
    def base_address(self) -> int: ...
    def evaluate(self, expressions: dict[str, str]) -> dict[str, int | None]: ...

class Error(Protocol):
    def Fail(self) -> bool: ...
    def GetCString(self) -> str: ...


class Value(Protocol):
    def GetError(self) -> Error: ...
    def GetValueAsSigned(self) -> int: ...


class Options(Protocol):
    def SetLanguage(self, language: int) -> None: ...
    def SetStopOthers(self, value: bool) -> None: ...
    def SetTimeoutInMicroSeconds(self, value: int) -> None: ...
    def SetUnwindOnError(self, value: bool) -> None: ...
    def SetIgnoreBreakpoints(self, value: bool) -> None: ...


class Frame(Protocol):
    def EvaluateExpression(self, expression: str, options: Options) -> Value: ...


class Thread(Protocol):
    def GetQueueName(self) -> str: ...
    def GetFrameAtIndex(self, index: int) -> Frame: ...


class Process(Protocol):
    def GetProcessID(self) -> int: ...
    def GetNumThreads(self) -> int: ...
    def GetThreadAtIndex(self, index: int) -> Thread: ...
    def SetSelectedThread(self, thread: Thread) -> bool: ...
    def Detach(self) -> Error: ...


class Listener(Protocol):
    pass


class Target(Protocol):
    def AttachToProcessWithID(self, listener: Listener, pid: int, error: Error) -> Process: ...


class Debugger(Protocol):
    def CreateTarget(self, executable: str) -> Target: ...
    def GetListener(self) -> Listener: ...


class Lldb(Protocol):
    eLanguageTypeC_plus_plus_17: int
    def SBError(self) -> Error: ...
    def SBExpressionOptions(self) -> Options: ...


class Session:
    def __init__(self, debugger: Debugger, snapshot: Snapshot) -> None:
        self.debugger = debugger
        self.snapshot = snapshot
        self.module = cast(Lldb, importlib.import_module("lldb"))
        self.process: Process | None = None
        self.frame: Frame | None = None

    def attach(self) -> None:
        target = self.debugger.CreateTarget(self.snapshot.executable)
        error = self.module.SBError()
        process = target.AttachToProcessWithID(self.debugger.GetListener(), self.snapshot.pid, error)
        self.process = process
        if error.Fail() or process.GetProcessID() != self.snapshot.pid:
            raise RuntimeError("Attach failed: " + str(error.GetCString()))
        threads = [process.GetThreadAtIndex(i) for i in range(process.GetNumThreads())]
        main = next((t for t in threads if t.GetQueueName() == "com.apple.main-thread"), None)
        if main is None or not process.SetSelectedThread(main):
            raise RuntimeError("Qt main thread unavailable")
        self.frame = main.GetFrameAtIndex(0)

    def evaluate(self, expression: str) -> int:
        if self.frame is None:
            raise RuntimeError("No attached main thread")
        options = self.module.SBExpressionOptions()
        options.SetLanguage(self.module.eLanguageTypeC_plus_plus_17)
        options.SetStopOthers(False)
        options.SetTimeoutInMicroSeconds(15_000_000)
        options.SetUnwindOnError(True)
        options.SetIgnoreBreakpoints(True)
        value = self.frame.EvaluateExpression(expression, options)
        if value.GetError().Fail():
            raise RuntimeError("Native expression failed: " + str(value.GetError().GetCString()))
        return value.GetValueAsSigned()

    def detach(self) -> None:
        if self.process is not None:
            process, self.process = self.process, None
            self.frame = None
            error = process.Detach()
            if error.Fail():
                raise RuntimeError("Detach failed: " + str(error.GetCString()))


@contextmanager
def native_lock(directory: Path) -> Iterator[int]:
    directory = private_dir(directory)
    fd = os.open(directory / "client.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield fd
    finally:
        os.close(fd)


def execute_file(debugger: Debugger, filename: str) -> None:
    """Called in Apple's LLDB Python 3.9; use no evaluated 3.10-only types."""
    path = Path(filename)
    job = object_json(path)
    snapshot = Snapshot.parse(job.get("snapshot"))
    expressions = job.get("expressions")
    if not isinstance(expressions, dict) or not all(isinstance(k, str) and isinstance(v, str)
                                                   for k, v in expressions.items()):
        raise ValueError("Invalid native expression request")
    report: dict[str, object] = {"values": {}, "detached": False}
    session = Session(debugger, snapshot)
    image = Path(snapshot.executable).parents[1] / "Resources/wechat.dylib"
    try:
        session.attach()
        with MachReader(snapshot.pid, Path(snapshot.executable), max_bytes=32*1024*1024, seconds=30) as reader:
            snapshot.check(reader, image)
        values: dict[str, int] = {}
        for name, expression in expressions.items():
            values[name] = session.evaluate(expression)
        report["values"] = values
    except Exception as error:
        report["error"] = str(error)
    finally:
        try:
            session.detach()
            report["detached"] = True
        finally:
            write_json(path.with_name("result.json"), report)


class LLDBExecutor:
    def __init__(self, pid: int, *, app: Path | None = None, output: Path | None = None,
                 timeout: float = 60.0, lldb: str = "/usr/bin/lldb") -> None:
        self.pid = pid
        self.app = app or Path.home() / "Applications/WeChat-4.1.15-BoxAgent.app"
        self.output = output or Path(__file__).resolve().parents[3] / ".runtime/private/wechat/native"
        self.timeout = timeout
        self.lldb = lldb
        self._snapshot: Snapshot | None = None

    def base_address(self) -> int:
        snapshot = discover(self.app)
        if snapshot.pid != self.pid:
            raise ExecutionError("Configured replica process was replaced; rebuild the transport")
        self._snapshot = snapshot
        return snapshot.base

    def evaluate(self, expressions: dict[str, str]) -> dict[str, int | None]:
        if self._snapshot is None:
            self.base_address()
        if self._snapshot is None:
            raise ExecutionError("Replica identity unavailable")
        with native_lock(self.output) as lease:
            directory = private_dir(self.output / "jobs" / uuid.uuid4().hex)
            context = directory / "context.json"
            write_json(context, {"snapshot": self._snapshot.json(), "expressions": expressions})
            root = Path(__file__).resolve().parents[3]
            command = ("script import sys; sys.path.insert(0," + repr(str(root)) + "); "
                       "from boxagent.infrastructure.wechat.executor import execute_file; "
                       "execute_file(lldb.debugger," + repr(str(context)) + ")")
            fd = os.open(directory / "executor.log", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w") as log:
                process = subprocess.Popen([self.lldb, "--no-lldbinit", "--batch", "-o", command],
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                    pass_fds=(lease,), start_new_session=True)
                try:
                    process.wait(timeout=self.timeout)
                except (subprocess.TimeoutExpired, KeyboardInterrupt):
                    process.send_signal(signal.SIGINT)
                    try:
                        process.wait(timeout=20)
                    except subprocess.TimeoutExpired as error:
                        raise ExecutionError("Native executor did not exit; its inherited lock remains held") from error
                    raise ExecutionError("Native call interrupted; inspect its result before retrying")
            result = directory / "result.json"
            if not result.exists():
                raise ExecutionError("Native worker produced no result; see private executor.log")
            report = object_json(result)
            if report.get("error") or report.get("detached") is not True:
                raise ExecutionError(str(report.get("error") or "Native worker did not detach"))
            values = report.get("values")
            if not isinstance(values, dict) or set(values) != set(expressions):
                raise ExecutionError("Native result names do not match request")
            if not all(type(v) is int for v in values.values()):
                raise ExecutionError("Native result has an invalid type")
            return cast("dict[str, int | None]", values)
