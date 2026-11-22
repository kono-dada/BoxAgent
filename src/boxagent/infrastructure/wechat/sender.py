"""Guarded native composer operations using the verified 4.1.15 static metacalls."""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict
from pathlib import Path

from boxagent.domain.wechat.contracts import OperationRejected
from boxagent.domain.wechat.models import DraftSnapshot, GroupMember, SendOutcome
from boxagent.infrastructure.wechat._private import private_dir
from boxagent.infrastructure.wechat.addresses import BuildProfile
from boxagent.infrastructure.wechat.executor import ExpressionExecutor
from boxagent.infrastructure.wechat.probe import LiveObjects, ObjectProbe


class DraftNotEmpty(OperationRejected):
    """An existing user draft must be preserved."""

class WriteRejected(OperationRejected):
    """Exact native readback differed from the requested text."""

class SendRejected(OperationRejected):
    """The native operation refused its preconditions."""

def qstring_blob(text: str) -> bytes:
    raw = text.encode('utf-16-le', errors='strict')
    return (b'\xff'*4 + (len(raw)//2).to_bytes(4,'little') + bytes(8)
            + (24).to_bytes(8,'little') + raw + b'\0\0')

def code_units(text: str) -> tuple[int, ...]:
    if '\0' in text:
        raise ValueError('NUL is not supported')
    raw = text.encode('utf-16-le', errors='strict')
    if len(raw) > 8192:
        raise ValueError('Text exceeds the verified expression budget')
    return tuple(int.from_bytes(raw[i:i+2],'little') for i in range(0,len(raw),2))

class NativeWeChatSender:
    def __init__(
        self, probe: ObjectProbe, executor: ExpressionExecutor, profile: BuildProfile
    ) -> None:
        self._probe = probe
        self._executor = executor
        self._profile = profile
        self._selected_peer: str | None = None

    def _objects(self, peer: str) -> tuple[int, LiveObjects]:
        base = self._executor.base_address()
        return base, self._probe.locate(base, peer, self._profile)

    def expression(self, base: int, live: LiveObjects, peer: str, text: str, *,
                   operation: str, dry_run: bool = False, member: GroupMember | None = None) -> str:
        p = self._profile
        units = code_units(text)
        expected = ((0xfffc,) + units) if member is not None else units
        numbers = ','.join(str(u) for u in (*units,0))
        expected_numbers = ','.join(str(u) for u in (*expected,0))
        peer_numbers = ','.join(str(u) for u in (*peer.encode(),0))
        def address(rva: int) -> str:
            return f"{base + rva}ULL"

        # Every native mutation is guarded in the SAME Qt-main-thread expression.
        parts = [
            "([]()->long {",
            "unsigned long wxvm="
            f"{live.vm}"
            "ULL,wxeditor="
            f"{live.editor}"
            "ULL,wxview="
            f"{live.view}"
            "ULL,wxcell="
            f"{live.cell}"
            "ULL;",
            "if(*(unsigned long*)wxvm!="
            f"{address(p.vm_vptr)}"
            "||*(unsigned long*)wxeditor!="
            f"{address(p.editor_vptr)}"
            "||*(unsigned long*)wxview!="
            f"{address(p.view_vptr)}"
            "||*(unsigned long*)wxcell!="
            f"{address(p.cell_vptr)}"
            ")return -10L;",
            f"unsigned char wxpeer[]={{{peer_numbers}}};",
            "unsigned char* wxs=(unsigned char*)(wxcell+0x158);unsigned long wxlen=wxs[23];",
            "if(wxlen&128){wxlen=*(unsigned long*)(wxs+8);wxs=*(unsigned char**)wxs;}",
            f"if(wxlen!={len(peer.encode())})return -10L;",
            f"for(int wxi=0;wxi<{len(peer.encode())};++wxi)if(wxs[wxi]!=wxpeer[wxi])return -10L;",
            "struct WxOrigin{void*p;void*c;};WxOrigin wxorigin={0,0};",
            "if(!((bool(*)(void*,const void*,const void*))"
            f"{address(p.navigate)}"
            ")((void*)wxvm,(void*)"
            f"{live.peer_object}"
            "ULL,&wxorigin))return -1L;",
            "auto wxselected=[&]()->bool{return *(unsigned long*)(*(unsigned "
            "long*)(wxvm+0xf8)+0x40)=="
            f"{live.cell}"
            "ULL;};",
            "if(!wxselected())return -1L;",
            "struct WxString{void*d;~WxString(){int wxr=*(int*)d;if(wxr==0||(wxr!=-1&&__atomic_fet"
            "ch_sub((int*)d,1,4)==1))",
            "((void(*)(void*,unsigned long,unsigned long))"
            f"{address(p.qarray_dealloc)}"
            ")(d,2,8);}};",
            "auto wxread=[&](const unsigned short* wxwanted,int wxn)->long{",
            f"WxString wxout={{(void*){address(p.shared_null)}}};void*wxargs[]={{&wxout}};",
            f"((void(*)(void*,int,int,void**)){address(p.metacall)})((void*)wxeditor,1,9,wxargs);",
            "int wxsize=*((int*)wxout.d+1);long wxoffset=*(long*)((char*)wxout.d+16);",
            "if(wxsize<0||wxsize>65536||wxoffset<24||wxoffset>1048576)return -10L;",
            "if(wxn<0)return wxsize;if(wxsize!=wxn)return -3L;",
            "for(int wxi=0;wxi<wxn;++wxi)if(((unsigned short*)((char*)wxout."
            "d+wxoffset))[wxi]!=wxwanted[wxi])return -3L;return 1L;};",
        ]
        if operation == 'select':
            return ''.join(parts+['return 1L;})()'])
        if operation == 'draft':
            return ''.join(parts+['return wxread(0,-1);})()'])
        if operation != 'clear':
            parts.append('if(wxread(0,-1)!=0)return -2L;')
        if dry_run:
            return ''.join(parts+['return 1L;})()'])
        if member is not None:
            from boxagent.infrastructure.wechat.mentions import native_member_expression
            parts.append(native_member_expression(base, p, member))
        parts.extend(
            [
                f"unsigned short wxwant[]={{{numbers}}},wxexpected[]={{{expected_numbers}}};",
                "void*wxdata=((void*(*)(unsigned long))"
                f"{address(p.operator_new)}"
                ")("
                f"{26 + 2 * len(units)}"
                ");",
                "*(int*)wxdata=1;*(int*)((char*)wxdata+4)="
                f"{len(units)}"
                ";*(unsigned int*)((char*)wxdata+8)="
                f"{len(units) + 1}"
                ";",
                "*(unsigned int*)((char*)wxdata+12)=0;*(long*)((char*)wxdata+16)=24;",
                "for(int wxi=0;wxi<="
                f"{len(units)}"
                ";++wxi)((unsigned short*)((char*)wxdata+24))[wxi]=wxwant[wxi];",
                "WxString wxinput={wxdata};void*wxset[]={0,&wxinput};",
                "((void(*)(void*,int,int,void**))"
                f"{address(p.metacall)}"
                ")((void*)wxeditor,0,"
                f"{27 if member else 16}"
                ",wxset);",
                f"if(wxread(wxexpected,{len(expected)})!=1)return -3L;",
                "if(!wxselected())return -1L;",
            ]
        )
        if operation == "send":
            parts.extend(
                [
                    f"if(wxread(wxexpected,{len(expected)})!=1)return -3L;",
                    f"int wxaction={p.send_action};void*wxsend[]={{0,&wxaction}};",
                    f"((void(*)(void*,int,int,void**)){address(p.send)})((void*)wxview,0,{p.send_index},wxsend);",
                ]
            )
        return "".join(parts + ["return 1L;})()"])

    def _run(self, peer: str, text: str, operation: str, *, dry_run: bool = False,
             member: GroupMember | None = None) -> int:
        base, live = self._objects(peer)
        expression = self.expression(
            base, live, peer, text, operation=operation, dry_run=dry_run, member=member
        )
        result = self._executor.evaluate({operation: expression}).get(operation)
        if result is None:
            raise SendRejected('Native operation returned no result')
        if result == -2:
            raise DraftNotEmpty('Composer draft is not empty; use explicit clear if intended')
        if result == -3:
            raise WriteRejected('Exact UTF-16 readback differed; draft retained for inspection')
        if result < 0:
            raise SendRejected('Target/composer guard rejected the operation')
        self._selected_peer = peer
        return result

    def select_peer(self, peer: str) -> bool:
        try:
            return self._run(peer,'','select') == 1
        except SendRejected:
            return False

    def draft(self) -> DraftSnapshot:
        if self._selected_peer is None:
            raise ValueError('Select a peer or use draft_for(peer)')
        return self.draft_for(self._selected_peer)

    def draft_for(self, peer: str) -> DraftSnapshot:
        return DraftSnapshot(self._run(peer,'','draft'))

    def clear_draft(self, peer: str) -> None:
        self._run(peer,'','clear')

    def send(self, peer: str, text: str, *, dry_run: bool = False) -> SendOutcome:
        if not text.strip():
            raise ValueError('Text is empty')
        code_units(text)
        self._run(peer,text,'send',dry_run=dry_run)
        return SendOutcome(peer,text,submitted=not dry_run,dry_run=dry_run)

    def send_mention(
        self, peer: str, member: GroupMember, text: str, *, dry_run: bool = False
    ) -> SendOutcome:
        from boxagent.infrastructure.wechat.mentions import validate_member
        validate_member(peer,member)
        if not text.strip():
            raise ValueError('Mention suffix is empty')
        code_units(text)
        self._run(peer,text,'send',dry_run=dry_run,member=member)
        return SendOutcome(peer,text,submitted=not dry_run,dry_run=dry_run)


class SQLiteSendRequests:
    """The intent survives process crashes, including an unknown native result."""

    def __init__(self, path: Path) -> None:
        private_dir(path.parent)
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        self.path = path
        connection = sqlite3.connect(path)
        try:
            connection.execute("CREATE TABLE IF NOT EXISTS sends(request_id TEXT PRIMARY KEY, "
                               "fingerprint TEXT NOT NULL, outcome TEXT)")
            connection.commit()
        finally:
            connection.close()

    def claim(self, request_id: str, fingerprint: str) -> tuple[bool, SendOutcome | None]:
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT fingerprint,outcome FROM sends WHERE request_id=?", (request_id,)
            ).fetchone()
            if row is not None:
                if row[0] != fingerprint:
                    raise ValueError("request_id already belongs to another message/action")
                outcome = parse_outcome(str(row[1])) if row[1] is not None else None
                return False, outcome
            connection.execute("INSERT INTO sends VALUES (?,?,NULL)", (request_id, fingerprint))
            connection.commit()
            return True, None
        finally:
            connection.close()

    def finish(self, request_id: str, outcome: SendOutcome) -> None:
        connection = sqlite3.connect(self.path)
        try:
            with connection:
                connection.execute("UPDATE sends SET outcome=? WHERE request_id=?",
                                   (json.dumps(asdict(outcome), ensure_ascii=False), request_id))
        finally:
            connection.close()


def parse_outcome(raw: str) -> SendOutcome:
    value: object = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Invalid stored send outcome")
    peer, text, request_id, error = (
        value.get(k) for k in ("peer", "text", "request_id", "error_code")
    )
    local, server = value.get("local_id"), value.get("server_id")
    submitted, dry = value.get("submitted"), value.get("dry_run")
    if not isinstance(peer, str) or not isinstance(text, str) or not isinstance(request_id, str):
        raise ValueError("Invalid stored outcome identity")
    if not (error is None or isinstance(error, str)):
        raise ValueError("Invalid stored outcome error")
    if not (local is None or isinstance(local, int)) or not (
        server is None or isinstance(server, int)
    ):
        raise ValueError("Invalid stored receipt identifiers")
    if not isinstance(submitted, bool) or not isinstance(dry, bool):
        raise ValueError("Invalid stored outcome state")
    return SendOutcome(peer, text, local, server, request_id, submitted, dry, error)
