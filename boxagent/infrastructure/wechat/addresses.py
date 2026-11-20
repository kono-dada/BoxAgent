"""Fixed code offsets inside ``wechat.dylib`` per client build.

Every value here is an RVA: the runtime address is ``base + rva`` where ``base``
is the load address of ``wechat.dylib`` for the running process. Loading
addresses change on every restart and must never be cached across processes.
"""

from __future__ import annotations

from dataclasses import dataclass

VERSION = "4.1.15"
BUILD = "270115"
BUNDLE_ID = "com.tencent.xinWeChatBoxAgent"
ARM64_SHA256 = "6d3feb56043cb63d2ff3f77aa32115abbfe7a2f28b3ab1ab59886ee625703e4b"


class UnsupportedBuild(RuntimeError):
    """Raised when the running client build has no verified profile."""


@dataclass(frozen=True)
class BuildProfile:
    """Verified offsets for one build of the macOS client."""

    build: str
    vm_vptr: int
    editor_vptr: int
    view_vptr: int
    navigate: int
    metacall: int
    send: int
    qarray_dealloc: int
    shared_null: int
    send_index: int = 8
    send_action: int = 3
    operator_new: int = 0x6FC04A8
    cell_vptr: int = 0x99A1238


PROFILES: dict[str, BuildProfile] = {
    "4.1.15.270115": BuildProfile(
        build="4.1.15.270115",
        vm_vptr=0x99A1170,
        editor_vptr=0x999A3D0,
        view_vptr=0x999A960,
        navigate=0xA57524,
        metacall=0x69A0654,
        send=0x12FBF4,
        qarray_dealloc=0x6D227A8,
        shared_null=0xA05B978,
    ),
}


def profile_for(build: str) -> BuildProfile:
    """Return the verified profile for ``build`` or raise ``UnsupportedBuild``."""
    try:
        return PROFILES[build]
    except KeyError:
        supported = ", ".join(sorted(PROFILES))
        raise UnsupportedBuild(f"unsupported WeChat build {build!r}; known: {supported}") from None
