"""Versioned ABI and native expressions for the independent Moments experiment.

Only RVAs and layouts belong here. PIDs, image bases and object addresses are
resolved or allocated for each invocation; nothing is loaded from old probes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Visibility(str, Enum):
    PRIVATE = "private"
    PUBLIC = "public"

    @property
    def native_mode(self) -> int:
        return 2 if self is Visibility.PRIVATE else 1


VERSION = "4.1.15"
BUILD = "270115"
BUNDLE_ID = "com.tencent.xinWeChatBoxAgent"
ARM64_SHA256 = "6d3feb56043cb63d2ff3f77aa32115abbfe7a2f28b3ab1ab59886ee625703e4b"
MAX_UTF16_UNITS = 4096  # Our expression budget, not a claimed WeChat limit.
ARENA_SIZE = 65536
MAGIC = b"BoxSNS01"

# Offsets within a newly owned Mach allocation, never absolute addresses.
SUCCESS = 0x1D0
COMPLETE = 0x3D0
PHASE = 0x400
SUBMIT_STAGE = 0x408
VM = 0x420
CONTROL = 0x428
PANEL = 0x430
EDITOR = 0x438
WEAK_GUARD = 0x440
FUTURE = 0x800
SUBSCRIPTION = 0x1000
SOURCE = 0x1800


@dataclass(frozen=True)
class Expressions:
    base: int
    arena: int
    text: str
    visibility: Visibility

    def address(self, rva: int) -> str:
        return f"{self.base + rva}ULL"

    def slot(self, offset: int) -> str:
        return f"{self.arena + offset}ULL"

    def load(self, offset: int) -> str:
        return f"*(unsigned long*){self.slot(offset)}"

    @property
    def qstring_type(self) -> str:
        return (
            "struct BoxSnsQString {void* d; ~BoxSnsQString(){int boxrefs=*(int*)d;"
            "if(boxrefs==0||(boxrefs!=-1&&__atomic_fetch_sub((int*)d,1,4)==1))"
            f"((void(*)(void*,unsigned long,unsigned long)){self.address(0x6D227A8)})(d,2,8);"
            "}}; "
        )

    def create(self) -> str:
        return (
            "({ struct BoxSnsPair {void*p;void*c;~BoxSnsPair(){}};"
            "BoxSnsPair boxempty={0,0};int boxkind=1;"
            f"BoxSnsPair boxvm=((BoxSnsPair(*)(void*,const BoxSnsPair*,const int*)){self.address(0x19AF500)})(0,&boxempty,&boxkind);"
            f"{self.load(VM)}=(unsigned long)boxvm.p;{self.load(CONTROL)}=(unsigned long)boxvm.c;{self.load(PHASE)}=1;"
            "*(unsigned int*)((char*)boxvm.p+0x54)=0;"
            f"void* boxpanel=((void*(*)(unsigned long)){self.address(0x6FC04A8)})(0x238);"
            f"{self.load(PANEL)}=(unsigned long)boxpanel;{self.load(PHASE)}=2;"
            f"((void*(*)(void*,unsigned int,unsigned int,void*)){self.address(0x19A2410)})(boxpanel,1,0,0);"
            f"{self.load(PHASE)}=3;"
            f"((void(*)(void*,const BoxSnsPair*)){self.address(0x19A54F4)})(boxpanel,&boxvm);"
            f"{self.load(PHASE)}=4;"
            "unsigned long boxinput=*(unsigned long*)((char*)boxpanel+0x1b0);"
            "unsigned long boxreply=*(unsigned long*)(boxinput+0x190);"
            f"{self.load(EDITOR)}=*(unsigned long*)(boxreply+0x1e0);"
            "bool boxvisible=true;void* boxargs[]={&boxvisible};"
            f"((void(*)(void*,int,int,void**)){self.address(0x6889B54)})(boxpanel,1,35,boxargs);"
            f"(!boxvisible&&*(unsigned long*)boxpanel=={self.address(0x9A0DDC0)}"
            f"&&*(unsigned long*)boxvm.p=={self.address(0x9B5D888)})?1L:-1L; }})"
        )

    def prepare(self) -> str:
        units = utf16_units(self.text)
        numbers = ",".join(str(x) for x in (*units, 0))
        n = len(units)
        return (
            "({ " + self.qstring_type +
            f"unsigned long boxvm={self.load(VM)},boxeditor={self.load(EDITOR)};"
            "unsigned long boxprivacy[13]={0};void* boxpv=(void*)(*(unsigned long*)(boxvm+0x20)+0x78);"
            f"((void(*)(void*,const void*)){self.address(0x19C6FFC)})(boxprivacy,boxpv);"
            f"*(unsigned int*)boxprivacy={self.visibility.native_mode};"
            f"((void(*)(void*,const void*)){self.address(0x19DE150)})((void*)boxvm,boxprivacy);"
            f"((void(*)(void*)){self.address(0x1F68A4)})(boxprivacy);"
            f"unsigned short boxwant[]={{{numbers}}};"
            f"void* boxdata=((void*(*)(unsigned long)){self.address(0x6FC04A8)})({24 + 2 * (n + 1)});"
            f"*(int*)boxdata=1;*(int*)((char*)boxdata+4)={n};"
            f"*(unsigned int*)((char*)boxdata+8)={n+1};*(unsigned int*)((char*)boxdata+12)=0;"
            "*(long*)((char*)boxdata+16)=24;"
            f"for(int boxi=0;boxi<={n};++boxi)((unsigned short*)((char*)boxdata+24))[boxi]=boxwant[boxi];"
            "BoxSnsQString boxdesired={boxdata};void* boxset[]={0,&boxdesired};"
            f"((void(*)(void*,int,int,void**)){self.address(0x69A0654)})((void*)boxeditor,0,16,boxset);"
            f"BoxSnsQString boxout={{(void*){self.address(0xA05B978)}}};void* boxget[]={{&boxout}};"
            f"((void(*)(void*,int,int,void**)){self.address(0x69A0654)})((void*)boxeditor,1,9,boxget);"
            "int boxn=*((int*)boxout.d+1);long boxoff=*(long*)((char*)boxout.d+16);"
            f"bool boxsame=boxn=={n}&&boxoff>=24&&boxoff<=1048576;"
            f"if(boxsame)for(int boxi=0;boxi<{n};++boxi)if(((unsigned short*)((char*)boxout.d+boxoff))[boxi]!=boxwant[boxi])boxsame=false;"
            f"((void(*)(void*)){self.address(0x19DECF8)})((void*)boxvm);"
            "unsigned long boxcore=*(unsigned long*)(boxvm+0x58);"
            f"bool boxprivate=*(unsigned int*)(boxcore+0x1f8)=={self.visibility.native_mode};"
            "(boxsame&&boxprivate)?1L:-2L; })"
        )

    def submit(self) -> str:
        # This is the tested native on-next/on-completed recorder, not injected
        # asynchronous JIT code. It records occurrence, not the returned bool.
        return (
            "({ static_assert(__cplusplus>=201703L,\"C++17 required\");"
            "struct BoxSnsResult {unsigned long words[128];~BoxSnsResult(){}"
            "static void* operator new(unsigned long,void* boxwhere){return boxwhere;}};"
            "struct BoxSnsFunction {unsigned long words[4];};"
            f"BoxSnsFunction boxnext={{{{{self.address(0x9B6B9F8)},{self.arena}ULL,0,0}}}};"
            f"BoxSnsFunction boxdone={{{{{self.address(0x9B6B9F8)},{self.arena+0x200}ULL,0,0}}}};"
            "boxnext.words[3]=(unsigned long)&boxnext;boxdone.words[3]=(unsigned long)&boxdone;"
            f"BoxSnsResult* boxsource=new((void*){self.slot(SOURCE)}) BoxSnsResult("
            f"((BoxSnsResult(*)(const char*,const char*,unsigned int)){self.address(0x3A8D8)})"
            f"((const char*){self.address(0xA13CC10)},(const char*){self.address(0xA13CC21)},1));"
            f"BoxSnsResult* boxfuture=new((void*){self.slot(FUTURE)}) BoxSnsResult("
            f"((BoxSnsResult(*)(void*)){self.address(0x19DD910)})((void*){self.load(VM)}));"
            f"{self.load(SUBMIT_STAGE)}=1;"
            f"BoxSnsResult* boxsub=new((void*){self.slot(SUBSCRIPTION)}) BoxSnsResult("
            f"((BoxSnsResult(*)(void*,void*,void*,void*)){self.address(0xEB9DF0)})"
            "(boxfuture,&boxnext,&boxdone,boxsource));"
            f"{self.load(SUBMIT_STAGE)}=2;1L; }})"
        )

    def queue_delete(self) -> str:
        return (
            f"({{void* boxpanel=(void*){self.load(PANEL)};"
            f"void* boxguard=((void*(*)(void*)){self.address(0x6D49E54)})(boxpanel);"
            f"{self.load(WEAK_GUARD)}=(unsigned long)boxguard;void* boxargs[]={{0}};"
            f"((void(*)(void*,int,int,void**)){self.address(0x6E5D5BC)})(boxpanel,0,3,boxargs);1L; }})"
        )

    def release(self, submitted: bool) -> str:
        # Release libc++ shared control blocks, NEVER QObject vptr slots.
        prefix = (
            "({ struct BoxSnsShared {static void release(void* boxp){if(!boxp)return;"
            "long boxold=__atomic_fetch_sub((long*)((char*)boxp+8),1,4);"
            "if(boxold==0){void** boxvt=*(void***)boxp;((void(*)(void*))boxvt[2])(boxp);"
            f"((void(*)(void*)){self.address(0x6FC0094)})(boxp);}}}}}};"
        )
        subscription = ""
        if submitted:
            subscription = (
                f"unsigned long* boxsub=(unsigned long*){self.slot(SUBSCRIPTION)};"
                "void* boxc1=(void*)boxsub[2];void* boxc2=(void*)boxsub[4];boxsub[2]=0;boxsub[4]=0;"
                "BoxSnsShared::release(boxc1);BoxSnsShared::release(boxc2);"
                f"void* boxfuture=(void*){self.slot(FUTURE)};void* boxfn=*((void**)boxfuture+3);"
                "if(boxfn){void** boxvt=*(void***)boxfn;"
                "((void(*)(void*))boxvt[boxfn==boxfuture?4:5])(boxfn);*((void**)boxfuture+3)=0;}"
            )
        return (
            prefix + subscription +
            f"void* boxcontrol=(void*){self.load(CONTROL)};{self.load(CONTROL)}=0;"
            "BoxSnsShared::release(boxcontrol);1L; })"
        )


def utf16_units(text: str) -> tuple[int, ...]:
    if not text.strip() or "\x00" in text:
        raise ValueError("Text must be nonempty and must not contain NUL")
    raw = text.encode("utf-16-le", errors="strict")
    if len(raw) // 2 > MAX_UTF16_UNITS:
        raise ValueError(f"Text exceeds the experiment's {MAX_UTF16_UNITS} UTF-16 unit budget")
    return tuple(int.from_bytes(raw[i:i+2], "little") for i in range(0, len(raw), 2))

import hashlib
import fcntl
import json
import os
from pathlib import Path
import signal
import secrets
import subprocess
import uuid
from boxagent.domain.wechat.models import PublishOutcome
from boxagent.infrastructure.wechat.probe import discover
from boxagent.infrastructure.wechat.executor import native_lock
from boxagent.infrastructure.wechat._private import object_json, private_dir, write_json

ROOT = Path(__file__).resolve().parents[3]

def request_key(request_id: str) -> str:
    if not request_id.strip() or len(request_id) > 256:
        raise ValueError("Request ID must be nonempty and at most 256 characters")
    return hashlib.sha256(request_id.encode("utf-8")).hexdigest()


def fingerprint(text: str, visibility: str, publish: bool, app: Path) -> str:
    return hashlib.sha256(json.dumps(
        [text, visibility, publish, str(app)], ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def previous_request(directory: Path, expected: str) -> dict[str, object] | None:
    context_file = directory / "context.json"
    if not directory.exists():
        return None
    if not context_file.exists():
        raise ValueError("Request journal incomplete; inspect it before using a new ID")
    context = object_json(context_file)
    if context.get("fingerprint") != expected:
        raise ValueError("Request ID already used with different content, visibility, action or app")
    result = directory / "result.json"
    report = object_json(result) if result.exists() else {
        "status": "outcome_unknown", "request_id": context["request_id"],
        "delivery_confirmed": False,
    }
    return {**report, "replayed": True, "executed_again": False}


def execute(text: str, visibility: Visibility, publish: bool, request_id: str, app: Path, state: Path, lease: int) -> dict[str, object]:
    utf16_units(text)
    app = app.expanduser().resolve()
    state = private_dir(state)
    requests = private_dir(state / "requests")
    directory = requests / request_key(request_id)
    expected = fingerprint(text, visibility.value, publish, app)
    lock_fd = os.open(state / "executor.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("Another Moments executor is running; wait before retrying") from error
        replay = previous_request(directory, expected)
        if replay is not None:
            return replay
        snapshot = discover(app)
        private_dir(directory)
        context_file = directory / "context.json"
        write_json(context_file, {
            "request_id": request_id, "fingerprint": expected, "snapshot": snapshot.json(),
            "text": text, "visibility": visibility.value, "publish": publish,
            "nonce": secrets.token_hex(16),
        })
        # repr is Python-source quoting, never shell interpolation. User text is
        # only in private JSON; native expressions contain numeric UTF-16 units.
        command_file = directory / "commands.lldb"
        command = (
            "settings set target.load-cwd-lldbinit false\n"
            "script import sys; sys.path.insert(0, " + repr(str(ROOT)) + "); "
            "from boxagent.infrastructure.wechat._moments_worker import run; "
            "run(lldb.debugger, " + repr(str(context_file)) + ")\n"
        )
        fd = os.open(command_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(command)
        log_path = directory / "executor.log"
        log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(log_fd, "w") as log:
            process = subprocess.Popen(
                ["/usr/bin/lldb", "--no-lldbinit", "--batch", "-s", str(command_file)],
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                pass_fds=(lock_fd,lease), start_new_session=True,
            )
            try:
                process.wait(timeout=150)
            except (subprocess.TimeoutExpired, KeyboardInterrupt):
                # Ask the executor to unwind/detach; never kill WeChat/debugserver.
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    return {"status": "outcome_unknown", "request_id": request_id,
                            "error": "Executor did not exit; its process retains the lock. Inspect executor.log.",
                            "journal": str(directory), "delivery_confirmed": False}
        result_file = directory / "result.json"
        report: dict[str, object] = object_json(result_file) if result_file.exists() else {
            "status": "failed", "request_id": request_id,
            "error": "Worker did not produce a result; inspect executor.log",
        }
        if report.get("status") in {"running", "submission_started"}:
            report["status"] = "outcome_unknown"
        report["journal"] = str(directory)
        return report
    finally:
        # Do not LOCK_UN: the executor inherits this open description, keeping
        # the lock if this parent exits before LLDB does.
        os.close(lock_fd)



class NativeMomentsPublisher:
    """Explicit single submissions; async roots retain the experimental lifetime limit."""
    def __init__(self, app: Path, state_dir: Path) -> None:
        self.app = app
        self.state_dir = state_dir

    def publish(self, text: str, visibility: str, *, request_id: str,
                dry_run: bool = True) -> PublishOutcome:
        utf16_units(text)
        selected = Visibility(visibility)
        with native_lock(self.state_dir.parent) as lease:
            report = execute(text, selected, not dry_run, request_id, self.app, self.state_dir, lease)
        status = str(report.get('status', 'outcome_unknown'))
        detail = str(report.get('error') or report.get('cleanup_error') or report.get('cleanup') or '')
        return PublishOutcome(request_id,text,visibility,status,False,detail)
