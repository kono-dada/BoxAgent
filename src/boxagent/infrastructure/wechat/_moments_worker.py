"""Private native Moments worker; loaded by LLDB Python 3.9."""
from __future__ import annotations

import struct
import time
from pathlib import Path

from boxagent.infrastructure.darwin.mach_reader import MachReader
from boxagent.infrastructure.wechat._private import object_json, write_json
from boxagent.infrastructure.wechat.executor import Debugger
from boxagent.infrastructure.wechat.executor import Session as NativeSession
from boxagent.infrastructure.wechat.moments import (
    ARENA_SIZE,
    COMPLETE,
    CONTROL,
    EDITOR,
    FUTURE,
    MAGIC,
    PANEL,
    PHASE,
    SUBMIT_STAGE,
    SUCCESS,
    VM,
    WEAK_GUARD,
    Expressions,
    Visibility,
    utf16_units,
)
from boxagent.infrastructure.wechat.probe import Snapshot, allocate, deallocate


class Session(NativeSession):
    def evaluate(self, expression: str) -> int:
        result = super().evaluate(expression)
        if result != 1:
            raise RuntimeError('Native Moments validation failed')
        return result

def u64(reader: MachReader, address: int) -> int:
    return struct.unpack("<Q", reader.read(address, 8))[0]


def verify_payload(reader: MachReader, expressions: Expressions) -> None:
    b, a = expressions.base, expressions.arena
    vm, panel, editor = (u64(reader, a + offset) for offset in (VM, PANEL, EDITOR))
    for pointer, rva in ((vm, 0x9B5D888), (panel, 0x9A0DDC0), (editor, 0x9B96ED0)):
        if u64(reader, pointer) != b + rva:
            raise RuntimeError("Owned instance vtable mismatch")
    core = u64(reader, vm + 0x58)
    raw = reader.read(core + 0x58, 24)
    if raw[23] & 128:
        pointer, length = struct.unpack_from("<QQ", raw)
        if not 0 < length <= 4 * len(utf16_units(expressions.text)):
            raise RuntimeError("Unexpected native text length")
        body = reader.read(pointer, length)
    else:
        if raw[23] > 22:
            raise RuntimeError("Malformed inline text")
        body = raw[:raw[23]]
    privacy = struct.unpack("<I", reader.read(core + 0x1F8, 4))[0]
    if body != expressions.text.encode("utf-8") and body == expressions.text.strip().encode(
        "utf-8"
    ):
        raise RuntimeError(
            "WeChat trimmed leading/trailing whitespace; remove it explicitly from requested "
            "text/file"
        )
    if body != expressions.text.encode("utf-8") or privacy != expressions.visibility.native_mode:
        raise RuntimeError("Prepared payload text/visibility differs from requested content")


def run(debugger: Debugger, context_path: str) -> None:
    context_file = Path(context_path)
    context = object_json(context_file)
    snapshot = Snapshot.parse(context.get("snapshot"))
    text, visibility, publish, nonce = (
        context.get(k) for k in ("text", "visibility", "publish", "nonce")
    )
    if (
        not isinstance(text, str)
        or not isinstance(visibility, str)
        or type(publish) is not bool
        or not isinstance(nonce, str)
    ):
        raise ValueError("Invalid invocation context")
    utf16_units(text)
    selected_visibility = Visibility(visibility)
    header = MAGIC + bytes.fromhex(nonce)
    app_image = Path(snapshot.executable).parents[1] / "Resources/wechat.dylib"
    report_file = context_file.with_name("result.json")
    report: dict[str, object] = {
        "status": "running", "request_id": context["request_id"], "publish_requested": publish,
        "visibility": visibility, "submitted": False, "delivery_confirmed": False,
    }
    session = Session(debugger, snapshot)
    arena = 0
    expressions: Expressions | None = None

    def reader() -> MachReader:
        return MachReader(
            snapshot.pid, Path(snapshot.executable), max_bytes=32 * 1024 * 1024, seconds=60
        )

    def check_owned(memory: MachReader) -> None:
        snapshot.check(memory, app_image)
        if memory.read(arena, len(header)) != header:
            raise RuntimeError("Owned arena identity mismatch")

    write_json(report_file, report)
    try:
        with reader() as memory:
            snapshot.check(memory, app_image)
            initial = bytearray(0x500)
            initial[:len(header)] = header
            initial[SUCCESS] = initial[COMPLETE] = 1
            arena = allocate(memory, ARENA_SIZE, bytes(initial))
        report["arena"] = arena  # Current invocation only; never reused by a later run.
        expressions = Expressions(snapshot.base, arena, text, selected_visibility)
        session.attach()
        with reader() as memory:
            check_owned(memory)
        session.evaluate(expressions.create())
        session.evaluate(expressions.prepare())
        with reader() as memory:
            check_owned(memory)
            verify_payload(memory, expressions)
        report.update(text_exact=True, visibility_verified=True)
        if publish:
            # Persist intent BEFORE the only operation that can submit. Replay of
            # this request ID must return its journal, never execute it again.
            report["status"] = "submission_started"
            write_json(report_file, report)
            with reader() as memory:
                check_owned(memory)
                verify_payload(memory, expressions)
            session.evaluate(expressions.submit())
            report.update(status="submitted_unconfirmed", submitted=True)
        else:
            report["status"] = "dry_run_passed"
    except Exception as error:
        report.update(
            status="outcome_unknown" if report["status"] == "submission_started" else "failed",
            error=str(error),
        )
    finally:
        # Native deleteLater must run on the main thread; detach lets Qt execute it.
        try:
            if arena and expressions:
                with reader() as memory:
                    check_owned(memory)
                    phase = u64(memory, arena + PHASE)
                    report["submit_stage"] = u64(memory, arena + SUBMIT_STAGE)
                if phase >= 3:
                    if session.process is None:
                        session.attach()
                    session.evaluate(expressions.queue_delete())
                    report["cleanup"] = "panel_deletion_queued"
        except Exception as error:
            report["cleanup_error"] = str(error)
        finally:
            try:
                session.detach()
                report["detached"] = True
            except Exception as error:
                report["detach_error"] = str(error)
        write_json(report_file, report)

    # Poll only our fresh guard. Completion records occurrence, never success.
    try:
        deadline = time.monotonic() + 12
        destroyed = False
        while arena and time.monotonic() < deadline:
            with reader() as memory:
                check_owned(memory)
                guard = u64(memory, arena + WEAK_GUARD)
                destroyed = bool(guard) and struct.unpack("<i", memory.read(guard+4, 4))[0] == 0
                if publish:
                    report["next_callback_fired"] = memory.read(arena + SUCCESS, 1) == b"\0"
                    report["completion_callback_fired"] = memory.read(arena + COMPLETE, 1) == b"\0"
            if destroyed:
                break
            time.sleep(0.1)
        report["panel_destroyed"] = destroyed
        if arena and not destroyed:
            report["cleanup_error"] = "Hidden panel destruction was not observed; roots retained"
        # Until the async error/result ABI is independently verified, retain
        # subscription + model roots for publishes. Never free live callbacks.
        if destroyed and not publish and expressions:
            session.attach()
            try:
                with reader() as memory:
                    check_owned(memory)
                    control = u64(memory, arena + CONTROL)
                    if u64(memory, control) != snapshot.base + 0x9B59D88:
                        raise RuntimeError("Owned model control vtable mismatch")
                    if (
                        u64(memory, arena + SUBMIT_STAGE) != 0
                        or u64(memory, arena + FUTURE + 24) != 0
                    ):
                        raise RuntimeError("Dry run unexpectedly created a subscription")
                session.evaluate(expressions.release(submitted=False))
                with reader() as memory:
                    check_owned(memory)
                    deallocate(memory, arena, ARENA_SIZE)
                report["cleanup"] = "model_and_arena_released_guard_retained"
            finally:
                session.detach()
        elif arena:
            report["cleanup"] = "roots_retained_until_process_exit"
    except Exception as error:
        report["cleanup_error"] = str(error)
    finally:
        session.detach()
        write_json(report_file, report)
