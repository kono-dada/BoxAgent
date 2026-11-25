"""Source-backed, expiring research notes; separate from curated esports facts."""

import json
import re
from dataclasses import dataclass
from typing import Protocol

from boxagent.domain.web.contracts import WebSource


@dataclass(frozen=True)
class CultureEvidence:
    url: str
    title: str
    excerpt: str


@dataclass(frozen=True)
class LearnedCultureNote:
    topic: str
    evidence: tuple[CultureEvidence, ...]
    retrieved_at: float
    expires_at: float

    def reference(self, query_id: str) -> str:
        excerpts = "；另一份资料说：".join(item.excerpt for item in self.evidence)
        qualification = (
            "目前只读到一份正文，尚未交叉核实。"
            if len(self.evidence) == 1
            else "以下是不同来源的说法，不代表已确认最早起源。"
        )
        payload = {
            "query_id": query_id,
            "id": "learned:" + self.topic,
            "answer": "按英雄联盟社区语境，查到的资料是："
            + excerpts.rstrip("。；， ")
            + "。"
            + qualification,
            "source_urls": [item.url for item in self.evidence],
            "retrieved_at": self.retrieved_at,
            "expires_at": self.expires_at,
            "kind": "attributed_research_note",
        }
        return (
            "<boxagent_culture_reference>"
            + json.dumps(payload, ensure_ascii=False)
            + "</boxagent_culture_reference>"
        )


class LearnedCultureRepository(Protocol):
    def load(self, topic: str) -> LearnedCultureNote | None: ...
    def save(self, note: LearnedCultureNote) -> None: ...


class CultureLearner(Protocol):
    async def reference(self, query: str, *, query_id: str, context: str = "") -> str: ...


class CultureDiscovery(Protocol):
    async def discover(self, topic: str) -> tuple[WebSource, ...]: ...


def learning_topic(query: str, context: str) -> str:
    """Only send the public term, never conversation history, to search."""
    if re.search(r"(?i:dota)|生化危机|植物大战|王者荣耀", query):
        return ""
    if not re.search(
        r"英雄联盟|电竞|(?i:lol|lpl|lck|theshy|bin|blg|faker|rng|skt)|加里奥|沙皇",
        query + " " + context,
    ):
        return ""
    if re.search(
        r"不要|别说|别讲|屏蔽|关闭|恢复|发给|发送|记住|我家|我的|密码|密钥|电话|地址|邮箱|身份证",
        query,
    ):
        return ""
    match = re.search(r"(.+?)(?:是什么意思|是什么梗|是什么|什么意思|是谁|指谁|怎么来的)", query)
    if match is None:
        match = re.search(
            r"(?:讲讲|解释一下|介绍一下|说说|说一下)(.{2,24}?)[吗呢吧？?。！!]*$", query
        )
        if match is None:
            return ""
    topic = match[1]
    topic = re.sub(r"^.*(?:你知道|你了解|介绍一下|解释一下|告诉我)", "", topic)
    topic = re.sub(r"^(?:那|诶[，,]?|哎[，,]?|请问|呃[，,]?)+", "", topic)
    topic = re.sub(r"^(?:英雄联盟|(?i:lol)|电竞|社区)(?:社区)?(?:里的|中的|里|中)?", "", topic)
    topic = topic.strip(" \t，,。？!?‘’“”\"'")
    return topic if re.fullmatch(r"[\w\u4e00-\u9fff ·-]{2,24}", topic) else ""
