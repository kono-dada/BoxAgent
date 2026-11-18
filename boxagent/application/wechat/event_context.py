"""Resolve display labels for WeChat events without changing unread state."""
from __future__ import annotations

import asyncio
import json
import re
import xml.etree.ElementTree as ET

from boxagent.domain.events.models import EventBatch, EventDecision
from boxagent.domain.wechat.contracts import WeChatCapability
from boxagent.domain.wechat.models import CALL_PREVIEW


_INTERNAL_LABEL = re.compile(r"(?:wxid_\w+|\S+@(?:chatroom|openim)|Msg_[0-9a-f]{32})", re.ASCII)
_MESSAGE_XML = re.compile(r"(?:<\?xml[^>]*\?>\s*)?<(msg|voipmsg)(?=[\s/>])")
_MEDIA_LABELS = {"img": "[图片]", "emoji": "[表情]", "voicemsg": "[语音]",
                 "videomsg": "[视频]", "location": "[位置]"}


def _display_label(*candidates: str, identifier: str) -> str:
    for candidate in candidates:
        label = candidate.strip()
        if label and label != identifier and not _INTERNAL_LABEL.fullmatch(label):
            return label
    return ""


def _display_body(body: str) -> str:
    text = body.strip()
    wrapper = _MESSAGE_XML.match(text)
    if not wrapper:
        return " ".join(text.split()) or "[非文字消息]"
    if wrapper[1] == "voipmsg":
        return CALL_PREVIEW  # Events queued before the listener classified calls.

    # The outer message can also carry download URLs and encryption metadata.
    # Only extract the human-facing title or the recognized media type.
    if len(text) <= 65536 and "<!" not in text:
        try:
            message = ET.fromstring(text)
        except ET.ParseError:
            return "[非文字消息]"
        for tag, label in _MEDIA_LABELS.items():
            if message.find(tag) is not None:
                return label
        if (app_message := message.find("appmsg")) is not None:
            title = " ".join((app_message.findtext("title") or "").split())
            return title or "[分享消息]"
    return "[非文字消息]"


class WeChatEventContext:
    def __init__(self, module: WeChatCapability) -> None:
        self.module = module

    async def __call__(self, batch: EventBatch) -> str:
        labels = await asyncio.to_thread(self._labels, batch)
        return json.dumps({"source": "wechat", "display_names": labels}, ensure_ascii=False)

    async def notice(self, batch: EventBatch) -> EventDecision:
        """Relay each incoming message as quoted data without model filtering."""
        labels = await asyncio.to_thread(self._labels, batch)
        budget = max(1, 960 // max(1, len(batch.events)) - 60)
        lines: list[str] = []
        for event in batch.events:
            sender = _display_label(labels.get(event.sender_id, ""), event.sender_name,
                                    identifier=event.sender_id)
            subject = _display_label(labels.get(event.subject_id, ""), event.subject_name,
                                     identifier=event.subject_id)
            body = event.content
            if event.subject_id.endswith("@chatroom"):
                header, newline, remainder = body.partition("\n")
                if newline and event.sender_id and header.removesuffix("\r") == f"{event.sender_id}:":
                    body = remainder
                speaker = f"{(sender or '一位群成员')[:20]}在「{(subject or '群聊')[:20]}」"
            else:
                speaker = (sender or subject or "一位联系人")[:20]
            text = _display_body(body)
            if text == CALL_PREVIEW:
                lines.append(f"{speaker} · {CALL_PREVIEW}")
                continue
            preview = text[:budget] + ("…" if len(text) > budget else "")
            lines.append(f"{speaker}说：“{preview}”")
        return EventDecision("notify", "微信新消息：\n" + "\n".join(lines),
                             tuple(event.event_id for event in batch.events), "user_notify_all")

    def _labels(self, batch: EventBatch) -> dict[str, str]:
        identifiers = {identifier for event in batch.events for identifier in
                       (event.subject_id, event.sender_id) if identifier}
        labels: dict[str, str] = {}
        for identifier in sorted(identifiers):
            exact = [contact for contact in self.module.contacts(identifier, limit=20)
                     if contact.peer == identifier]
            if len(exact) == 1:
                label = _display_label(exact[0].remark, exact[0].nickname, identifier=identifier)
                if label:
                    labels[identifier] = label
        return labels
