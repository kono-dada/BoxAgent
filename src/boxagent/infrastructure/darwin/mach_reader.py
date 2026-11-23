"""Bounded Darwin task reads for an explicitly selected, owned app replica."""

from __future__ import annotations

import ctypes as c
import os
import struct
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType


class TaskReadError(RuntimeError):
    """A process identity, permission, ABI, or read check failed."""


@dataclass(frozen=True)
class TaskIdentity:
    pid: int
    executable: Path
    uid: int
    start: tuple[int, int]
    audit_token: bytes = field(repr=False)


@dataclass(frozen=True)
class MemoryRegion:
    address: int
    size: int
    protection: int
    user_tag: int


class MachReader:
    """Expose reads only; the acquired Mach right is not a read-only sandbox."""

    def __init__(self, pid: int, executable: Path, *, max_bytes: int, seconds: float) -> None:
        if sys.platform != "darwin" or pid <= 1:
            raise TaskReadError("A Darwin process PID greater than one is required")
        if not 1 <= max_bytes <= 2 * 1024 * 1024 * 1024 or not 0 < seconds <= 60:
            raise TaskReadError("Invalid scan budget")
        self.pid = pid
        self.executable = executable.resolve(strict=True)
        self.max_bytes = max_bytes
        self.bytes_read = 0
        self.deadline = time.monotonic() + seconds
        self.port = 0
        self.lib = c.CDLL("/usr/lib/libSystem.B.dylib")
        self.self_task = c.c_uint32.in_dll(self.lib, "mach_task_self_").value
        self.lib.task_for_pid.argtypes = [c.c_uint32, c.c_int, c.POINTER(c.c_uint32)]
        self.lib.task_for_pid.restype = c.c_int
        self.lib.pid_for_task.argtypes = [c.c_uint32, c.POINTER(c.c_int)]
        self.lib.pid_for_task.restype = c.c_int
        self.lib.task_info.argtypes = [c.c_uint32, c.c_int, c.c_void_p, c.POINTER(c.c_uint32)]
        self.lib.task_info.restype = c.c_int
        self.lib.proc_pidinfo.argtypes = [c.c_int, c.c_int, c.c_uint64, c.c_void_p, c.c_int]
        self.lib.proc_pidinfo.restype = c.c_int
        self.lib.proc_pidpath.argtypes = [c.c_int, c.c_void_p, c.c_uint32]
        self.lib.proc_pidpath.restype = c.c_int
        self.lib.mach_vm_region_recurse.argtypes = [
            c.c_uint32, c.POINTER(c.c_uint64), c.POINTER(c.c_uint64),
            c.POINTER(c.c_uint32), c.c_void_p, c.POINTER(c.c_uint32),
        ]
        self.lib.mach_vm_region_recurse.restype = c.c_int
        self.lib.mach_vm_read_overwrite.argtypes = [
            c.c_uint32, c.c_uint64, c.c_uint64, c.c_uint64, c.POINTER(c.c_uint64),
        ]
        self.lib.mach_vm_read_overwrite.restype = c.c_int
        self.lib.mach_port_deallocate.argtypes = [c.c_uint32, c.c_uint32]
        self.lib.mach_port_deallocate.restype = c.c_int
        self.initial_identity = self.identity(require_port=False)
        handle = c.c_uint32()
        status = int(self.lib.task_for_pid(self.self_task, pid, c.byref(handle)))
        self.port = handle.value
        try:
            if status != 0 or not self.port:
                raise TaskReadError(f"task_for_pid failed with status {status}")
            self.unchanged()
        except BaseException:
            self.close()
            raise

    def __enter__(self) -> MachReader:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        if self.port:
            handle, self.port = self.port, 0
            status = int(self.lib.mach_port_deallocate(self.self_task, handle))
            if status != 0:
                raise TaskReadError(f"Mach right release failed with status {status}")

    def identity(self, *, require_port: bool = True) -> TaskIdentity:
        path = c.create_string_buffer(4096)
        if int(self.lib.proc_pidpath(self.pid, path, 4096)) <= 0:
            raise TaskReadError("Process path unavailable")
        executable = Path(os.fsdecode(path.value))
        if executable != self.executable:
            raise TaskReadError("PID is not bound to the selected executable")
        bsd = c.create_string_buffer(136)
        if int(self.lib.proc_pidinfo(self.pid, 3, 0, bsd, 136)) != 136:
            raise TaskReadError("BSD process identity unavailable")
        pid, _, uid = struct.unpack_from("<III", bsd.raw, 12)
        if pid != self.pid or uid != os.getuid():
            raise TaskReadError("Process PID or owner mismatch")
        start = struct.unpack_from("<QQ", bsd.raw, 120)
        token = b""
        if require_port:
            if not self.port:
                raise TaskReadError("Task right is closed")
            task_pid = c.c_int()
            if (
                int(self.lib.pid_for_task(self.port, c.byref(task_pid))) != 0
                or task_pid.value != self.pid
            ):
                raise TaskReadError("Task PID mismatch")
            audit = (c.c_uint32 * 8)()
            count = c.c_uint32(8)
            if (
                int(self.lib.task_info(self.port, 15, c.byref(audit), c.byref(count))) != 0
                or count.value != 8
            ):
                raise TaskReadError("Task audit token unavailable")
            token = bytes(audit)
        return TaskIdentity(self.pid, executable, uid, (start[0], start[1]), token)

    def unchanged(self) -> None:
        current = self.identity()
        initial = self.initial_identity
        if (current.pid, current.executable, current.uid, current.start) != (
            initial.pid, initial.executable, initial.uid, initial.start,
        ):
            raise TaskReadError("Process instance changed")
        if initial.audit_token and current.audit_token != initial.audit_token:
            raise TaskReadError("Task identity changed")
        if not initial.audit_token:
            self.initial_identity = current

    def regions(self, *, max_regions: int = 4096) -> Iterator[MemoryRegion]:
        address = c.c_uint64(0)
        depth = c.c_uint32(0)
        for _ in range(max_regions):
            if time.monotonic() >= self.deadline:
                raise TaskReadError("Region enumeration deadline exceeded")
            size = c.c_uint64()
            # Apple SDK vm_region_submap_info_64 is packed to 4, size 76,
            # count 19; protection/tag/is_submap offsets are 0/20/48.
            info = (c.c_int32 * 19)()
            count = c.c_uint32(19)
            status = int(self.lib.mach_vm_region_recurse(
                self.port, c.byref(address), c.byref(size), c.byref(depth),
                c.byref(info), c.byref(count),
            ))
            if status == 1:  # KERN_INVALID_ADDRESS: end of map.
                return
            if status != 0 or count.value != 19 or not size.value:
                raise TaskReadError(f"Region enumeration failed with status {status}")
            if info[12]:
                depth.value += 1
                if depth.value > 64:
                    raise TaskReadError("Submap nesting exceeds bound")
                continue
            end = address.value + size.value
            if end > 1 << 64:
                raise TaskReadError("Region address overflow")
            yield MemoryRegion(address.value, size.value, info[0], info[5])
            address.value = end
        raise TaskReadError("Region count budget exhausted")

    def read(self, address: int, size: int) -> bytes:
        if time.monotonic() >= self.deadline:
            raise TaskReadError("Memory read deadline exceeded")
        if not self.port or address < 0x10000 or not 0 < size <= 2 * 1024 * 1024:
            raise TaskReadError("Invalid memory read bounds")
        if address + size > 1 << 64 or self.bytes_read + size > self.max_bytes:
            raise TaskReadError("Memory byte budget exhausted")
        self.bytes_read += size
        buffer = c.create_string_buffer(size)
        received = c.c_uint64()
        status = int(self.lib.mach_vm_read_overwrite(
            self.port, address, size, c.addressof(buffer), c.byref(received),
        ))
        if status != 0 or received.value != size:
            raise TaskReadError(f"Incomplete Mach read with status {status}")
        return buffer.raw
