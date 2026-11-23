"""Locate live client objects and the peer string object of a conversation."""

from __future__ import annotations

import ctypes as c
import hashlib
import logging
import os
import plistlib
import struct
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol, cast

from boxagent.domain.wechat.contracts import OperationRejected
from boxagent.infrastructure.darwin.mach_reader import MachReader, TaskReadError
from boxagent.infrastructure.wechat.addresses import (
    ARM64_SHA256,
    BUILD,
    BUNDLE_ID,
    VERSION,
    BuildProfile,
)

logger = logging.getLogger(__name__)



class PeerUnavailable(OperationRejected):
    """The peer is not materialized in the client's conversation model."""


class ReplicaNotRunning(OperationRejected):
    """The configured replica path has no unique running process."""


@dataclass(frozen=True)
class LiveObjects:
    """Addresses of the objects needed for a native send."""

    vm: int
    editor: int
    view: int
    cell: int
    peer_object: int


class ObjectProbe(Protocol):
    def locate(self, base: int, peer: str, profile: BuildProfile) -> LiveObjects: ...


@dataclass
class ReplicaProbe:
    """Scan the replica's writable memory for known vtable pointers."""

    pid: int
    executable: Path
    max_seconds: int = 50

    def locate(self, base: int, peer: str, profile: BuildProfile) -> LiveObjects:
        patterns = {
            "vm": struct.pack("<Q", base + profile.vm_vptr),
            "editor": struct.pack("<Q", base + profile.editor_vptr),
            "view": struct.pack("<Q", base + profile.view_vptr),
        }
        with MachReader(
            self.pid, self.executable, max_bytes=2 * 1024 * 1024 * 1024, seconds=self.max_seconds
        ) as reader:
            hits = self._scan(reader, patterns)
            missing = [name for name in patterns if not hits[name]]
            if missing:
                raise PeerUnavailable(f"live instances missing: {', '.join(missing)}")

            def qword(address: int) -> int:
                return int(struct.unpack("<Q", reader.read(address, 8))[0])

            wanted = peer.encode()
            selected: list[tuple[int, int]] = []
            for vm in hits["vm"]:
                try:
                    data = qword(vm+0xF8)
                    begin, end = qword(data), qword(data+8)
                    if end < begin or (end-begin) % 16 or end-begin > 16384*16:
                        continue
                    for index in range((end-begin)//16):
                        cell = qword(begin+index*16)
                        if qword(cell) != base+profile.cell_vptr:
                            continue
                        raw = reader.read(cell+0x158, 24)
                        if native_string(reader, raw) == wanted:
                            selected.append((vm, cell))
                except TaskReadError:
                    logger.debug("Discarded an unreadable model candidate")
            if len(selected) != 1:
                raise PeerUnavailable("Peer is unmaterialized or model identity is ambiguous")
            # Qt5 QObjectData has a back-pointer and a parent pointer. Validate
            # the back-pointer at every hop; never combine unrelated first hits.
            pairs: list[tuple[int, int]] = []
            for editor in hits["editor"]:
                ancestors = qobject_ancestors(reader, editor)
                pairs.extend((editor, view) for view in hits["view"] if view in ancestors)
            if len(pairs) != 1:
                raise PeerUnavailable("Composer ownership is ambiguous or unavailable")
            vm, cell = selected[0]
            editor, view = pairs[0]
            return LiveObjects(vm, editor, view, cell, cell+0x158)
        raise PeerUnavailable(f"peer {peer!r} is not materialized in the conversation model")

    @staticmethod
    def _scan(reader: MachReader, patterns: dict[str, bytes]) -> dict[str, list[int]]:
        hits: dict[str, list[int]] = {name: [] for name in patterns}
        for region in reader.regions(max_regions=8192):
            if not (region.protection & 2):
                continue
            offset = 0
            while offset < region.size:
                size = min(2 * 1024 * 1024, region.size - offset)
                try:
                    data = reader.read(region.address + offset, size)
                except TaskReadError:
                    logger.debug("Skipped an unreadable region during bounded WeChat scan")
                    break
                for name, pattern in patterns.items():
                    start = 0
                    while True:
                        index = data.find(pattern, start)
                        if index < 0:
                            break
                        if (region.address + offset + index) % 8 == 0:
                            hits[name].append(region.address + offset + index)
                        start = index + 8
                offset += size
        return hits


def native_string(reader: MachReader, raw: bytes) -> bytes:
    if raw[23] & 128:
        pointer, length = struct.unpack_from("<QQ", raw)
        if not 0 < length <= 4096:
            raise TaskReadError("Invalid native string length")
        return reader.read(pointer, length)
    if raw[23] > 22:
        raise TaskReadError("Invalid native short string")
    return raw[:raw[23]]


def qobject_ancestors(reader: MachReader, obj: int) -> set[int]:
    result: set[int] = set()
    try:
        for _ in range(64):
            if not obj or obj in result:
                break
            result.add(obj)
            data = struct.unpack("<Q", reader.read(obj+8, 8))[0]
            back, parent = struct.unpack("<QQ", reader.read(data+8, 16))
            if back != obj:
                return set()
            obj = parent
    except TaskReadError:
        logger.debug("Discarded invalid QObject parent chain")
        return set()
    return result


def replica_process(app: Path) -> tuple[int, Path]:
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    if info.get("CFBundleIdentifier") != "com.tencent.xinWeChatBoxAgent":
        raise TaskReadError("Only the installed BoxAgent replica is allowed")
    entitlements = subprocess.run(
        ["codesign", "-d", "--entitlements", ":-", str(app)],
        capture_output=True, check=True, timeout=10,
    ).stdout
    if plistlib.loads(entitlements).get("com.apple.security.get-task-allow") is not True:
        raise TaskReadError("Replica lacks get-task-allow")
    executable = (app / "Contents/MacOS/WeChat").resolve(strict=True)
    matches = matching_pids(executable)
    if len(matches) != 1:
        raise ReplicaNotRunning(
            "The configured WeChat replica must have exactly one running process"
        )
    return matches[0], executable


def matching_pids(executable: Path) -> tuple[int, ...]:
    process_list = subprocess.run(
        ["ps", "-axo", "pid=,comm="], text=True, capture_output=True, check=True, timeout=10,
    ).stdout
    matches: list[int] = []
    for line in process_list.splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) == 2 and parts[1] == str(executable):
            matches.append(int(parts[0]))
    return tuple(matches)


def another_replica_running() -> bool:
    """Catch an older installation sharing this replica's bundle/data identity."""
    process_list = subprocess.run(['ps', '-axo', 'comm='], text=True,
                                  capture_output=True, check=True, timeout=10).stdout
    for line in process_list.splitlines():
        executable = Path(line.strip())
        if not executable.is_absolute() or not str(executable).endswith('/Contents/MacOS/WeChat'):
            continue
        try:
            info = plistlib.loads((executable.parents[1]/'Info.plist').read_bytes())
            if info.get('CFBundleIdentifier') == BUNDLE_ID:
                return True
        except (OSError, ValueError):
            continue
    return False


def macho_uuid(header: bytes) -> str:
    if len(header) < 32 or struct.unpack_from("<II", header) != (0xFEEDFACF, 0x100000C):
        raise ValueError("Expected ARM64 Mach-O")
    count, size = struct.unpack_from("<II", header, 16)
    if count > 4096 or size > 65536 or 32 + size > len(header):
        raise ValueError("Invalid Mach-O commands")
    position = 32
    uuids: list[str] = []
    text_addresses: list[int] = []
    for _ in range(count):
        command, length = struct.unpack_from("<II", header, position)
        if length < 8 or position + length > 32 + size:
            raise ValueError("Invalid load command")
        if command == 0x1B and length == 24:
            uuids.append(header[position + 8 : position + 24].hex())
        if (
            command == 0x19
            and length >= 72
            and header[position + 8 : position + 24].rstrip(b"\0") == b"__TEXT"
        ):
            text_addresses.append(struct.unpack_from("<Q", header, position + 24)[0])
        position += length
    if position != 32 + size or len(uuids) != 1 or text_addresses != [0]:
        raise ValueError("Unexpected UUID or preferred image address")
    return uuids[0]


def disk_image(path: Path) -> str:
    with path.open("rb") as stream:
        head = stream.read(8)
        if len(head) != 8:
            raise ValueError("Truncated image")
        magic, count = struct.unpack(">II", head)
        if magic == 0xCAFEBABE and count <= 32:
            entries = stream.read(20 * count)
            slices = [struct.unpack_from(">IIIII", entries, i * 20) for i in range(count)]
            arm = [(x[2], x[3]) for x in slices if x[0] == 0x100000C]
            if len(arm) != 1:
                raise ValueError("Expected one ARM64 slice")
            offset, size = arm[0]
        elif magic == 0xCFFAEDFE:
            offset, size = 0, path.stat().st_size
        else:
            raise ValueError("Unsupported Mach-O container")
        stream.seek(offset)
        header = stream.read(min(size, 65568))
        stream.seek(offset)
        digest = hashlib.sha256()
        remaining = size
        while remaining:
            data = stream.read(min(remaining, 1024 * 1024))
            if not data:
                raise ValueError("Truncated ARM64 slice")
            digest.update(data)
            remaining -= len(data)
        if digest.hexdigest() != ARM64_SHA256:
            raise ValueError("Unsupported wechat.dylib fingerprint; native calls refused")
        return macho_uuid(header)


def image_base(reader: MachReader, image: Path) -> int:
    # TASK_DYLD_INFO, then dyld_all_image_infos and dyld_image_info[].
    class DyldInfo(c.Structure):
        _fields_ = [("address", c.c_uint64), ("size", c.c_uint64), ("format", c.c_int)]

    info = DyldInfo()
    count = c.c_uint32(c.sizeof(info) // 4)
    status = reader.lib.task_info(reader.port, 17, c.byref(info), c.byref(count))
    if status != 0 or info.format != 1:  # TASK_DYLD_ALL_IMAGE_INFO_64
        raise TaskReadError("64-bit dyld info unavailable")
    _, n, array = struct.unpack("<IIQ", reader.read(info.address, 16))
    if not 0 < n <= 8192:
        raise TaskReadError("Invalid image count")
    rows = reader.read(array, n * 24)
    found: list[int] = []
    for index in range(n):
        load, path_address, _ = struct.unpack_from("<QQQ", rows, index * 24)
        try:
            name = reader.read(path_address, min(1024, 4096 - (path_address & 4095))).split(
                b"\0", 1
            )[0]
        except TaskReadError:
            continue  # Unrelated dyld entries can have inaccessible path storage.
        if name == os.fsencode(image):
            found.append(load)
    if len(found) != 1:
        raise TaskReadError("Unique live wechat.dylib not found")
    return found[0]


@dataclass(frozen=True)
class Snapshot:
    pid: int
    executable: str
    start_seconds: int
    start_microseconds: int
    audit: str
    base: int
    uuid: str

    def check(self, reader: MachReader, image: Path) -> None:
        reader.unchanged()
        identity = reader.identity()
        if (identity.pid, str(identity.executable), identity.start, identity.audit_token.hex()) != (
            self.pid, self.executable, (self.start_seconds, self.start_microseconds), self.audit,
        ):
            raise TaskReadError("Process instance changed since discovery")
        if image_base(reader, image) != self.base:
            raise TaskReadError("Image base changed since discovery")
        if macho_uuid(reader.read(self.base, 65568)) != self.uuid:
            raise TaskReadError("Live image UUID changed")

    def json(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def parse(cls, value: object) -> Snapshot:
        if not isinstance(value, dict):
            raise ValueError("Missing snapshot")
        data = cast("dict[str, object]", value)
        numbers = [data.get(k) for k in ("pid", "start_seconds", "start_microseconds", "base")]
        strings = [data.get(k) for k in ("executable", "audit", "uuid")]
        if not all(type(x) is int for x in numbers) or not all(isinstance(x, str) for x in strings):
            raise ValueError("Invalid snapshot")
        pid, seconds, micros, base = cast("list[int]", numbers)
        executable, audit, uuid = cast("list[str]", strings)
        return cls(pid, executable, seconds, micros, audit, base, uuid)


def discover(app: Path) -> Snapshot:
    # The host is Python 3.12. Do not import its other experiments inside
    # Apple's LLDB Python 3.9 (some use evaluated Python 3.10 union types).

    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    if (info.get("CFBundleShortVersionString"), str(info.get("CFBundleVersion"))) != (
        VERSION,
        BUILD,
    ):
        raise ValueError(f"Only WeChat {VERSION} build {BUILD} is supported")
    image = app / "Contents/Resources/wechat.dylib"
    uuid = disk_image(image)
    pid, executable = replica_process(app)
    with MachReader(pid, executable, max_bytes=32*1024*1024, seconds=30) as reader:
        base = image_base(reader, image)
        if macho_uuid(reader.read(base, 65568)) != uuid:
            raise ValueError("Disk/live image UUID mismatch")
        identity = reader.identity()
        return Snapshot(
            pid, str(executable), *identity.start, identity.audit_token.hex(), base, uuid
        )


def allocate(reader: MachReader, size: int, header: bytes) -> int:
    reader.unchanged()
    allocate_fn = reader.lib.mach_vm_allocate
    allocate_fn.argtypes = [c.c_uint32, c.POINTER(c.c_uint64), c.c_uint64, c.c_int]
    allocate_fn.restype = c.c_int
    address = c.c_uint64()
    if allocate_fn(reader.port, c.byref(address), size, 1) != 0:
        raise TaskReadError("Owned target allocation failed")
    write_fn = reader.lib.mach_vm_write
    write_fn.argtypes = [c.c_uint32, c.c_uint64, c.c_uint64, c.c_uint32]
    write_fn.restype = c.c_int
    buffer = c.create_string_buffer(header)
    if write_fn(reader.port, address.value, c.addressof(buffer), len(header)) != 0:
        deallocate(reader, address.value, size)
        raise TaskReadError("Owned arena initialization failed")
    return address.value


def deallocate(reader: MachReader, address: int, size: int) -> None:
    reader.unchanged()
    fn = reader.lib.mach_vm_deallocate
    fn.argtypes = [c.c_uint32, c.c_uint64, c.c_uint64]
    fn.restype = c.c_int
    if fn(reader.port, address, size) != 0:
        raise TaskReadError("Owned arena deallocation failed")
