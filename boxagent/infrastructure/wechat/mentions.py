"""Verified short-string AtUsersMemberInfo path; full layout is not inferred."""
from __future__ import annotations

from boxagent.domain.wechat.models import GroupMember
from boxagent.infrastructure.wechat.addresses import BuildProfile


def validate_member(group: str, member: GroupMember) -> None:
    if not group.endswith("@chatroom") or member.group != group or not member.membership_verified:
        raise ValueError("Mention target must be a verified member of the exact group")
    if not member.peer or not member.display_name or "\0" in member.peer+member.display_name:
        raise ValueError("Invalid mention identity/display name")
    if len(member.peer.encode()) > 22 or len(member.display_name.encode()) > 22:
        raise ValueError("Only the verified Native24 short-string mention layout is supported")


def native_member_expression(base: int, profile: BuildProfile, member: GroupMember) -> str:
    validate_member(member.group, member)
    data = bytearray(72)
    for offset, value in ((0, member.peer), (48, member.display_name)):
        encoded = value.encode()
        data[offset:offset+len(encoded)] = encoded
        data[offset+23] = len(encoded)
    numbers = ",".join(str(byte) for byte in data)
    # The un-recovered aggregate destructor is never guessed. Keep this owned
    # 72-byte argument until process exit, as in the verified experiment.
    return (f"unsigned char wxmemberbytes[]={{{numbers}}};"
            f"void*wxmember=((void*(*)(unsigned long)){base+profile.operator_new}ULL)(72);"
            "for(int wxi=0;wxi<72;++wxi)((unsigned char*)wxmember)[wxi]=wxmemberbytes[wxi];"
            "void*wxmention[]={0,wxmember};"
            f"((void(*)(void*,int,int,void**)){base+profile.send}ULL)((void*)wxview,0,12,wxmention);")
