"""Source-backed visual episodes; media never masquerades as user speech."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import re
from typing import Any, Literal, Protocol, runtime_checkable

EvidenceKind = Literal["visual_observation", "user_report"]
Aspect = Literal["lineup", "equipment", "encounter", "result", "other"]


@dataclass(frozen=True)
class VisualFrame:
    image: bytes
    caption: str
    captured_at: float
    app: str
    window_id: int


@dataclass(frozen=True)
class VisualEvidence:
    evidence_id: str
    session_id: str
    interaction_id: str
    source_event_id: str
    user_request: str
    caption: str
    captured_at: float
    requested_at: float
    app: str
    window_id: int
    sha256: str

    def payload(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_payload(cls, value: object) -> VisualEvidence:
        if not isinstance(value, dict):
            raise ValueError("画面证据必须是对象")
        names = ("evidence_id", "session_id", "interaction_id", "source_event_id",
                 "user_request", "caption", "app", "sha256")
        if any(not isinstance(value.get(name), str) for name in names):
            raise ValueError("画面证据缺少字符串字段")
        times = (value.get("captured_at"), value.get("requested_at"))
        if any(not isinstance(t, (int, float)) or isinstance(t, bool) or not math.isfinite(t) for t in times):
            raise ValueError("画面证据时间无效")
        if type(value.get("window_id")) is not int:
            raise ValueError("画面证据窗口无效")
        if not re.fullmatch(r"[a-f0-9]{64}", value["sha256"]):
            raise ValueError("画面证据哈希无效")
        return cls(**{name: value[name] for name in cls.__dataclass_fields__})


@dataclass(frozen=True)
class EpisodeAtom:
    text: str
    kind: EvidenceKind
    quote: str


@dataclass(frozen=True)
class VisualEpisode:
    evidence: VisualEvidence
    atoms: tuple[EpisodeAtom, ...]
    game: str = ""
    subject: str = ""
    aspect: Aspect = "other"
    variant: str = ""
    game_version: str = ""

    @property
    def summary(self) -> str:
        return "\n".join(f"{'画面观察' if atom.kind == 'visual_observation' else '用户原话'}：{atom.text}"
                         for atom in self.atoms)

    def payload(self) -> dict[str, Any]:
        return {"evidence": self.evidence.payload(),
                "atoms": [asdict(atom) for atom in self.atoms],
                "game": self.game, "subject": self.subject, "aspect": self.aspect,
                "variant": self.variant, "game_version": self.game_version}

    @classmethod
    def from_payload(cls, value: object) -> VisualEpisode:
        if not isinstance(value, dict):
            raise ValueError("经历必须是对象")
        evidence = VisualEvidence.from_payload(value.get("evidence"))
        raw_atoms = value.get("atoms")
        if not isinstance(raw_atoms, list) or not 1 <= len(raw_atoms) <= 6:
            raise ValueError("经历必须有 1 至 6 条有来源的事实")
        atoms: list[EpisodeAtom] = []
        for atom in raw_atoms:
            if not isinstance(atom, dict) or atom.get("kind") not in ("visual_observation", "user_report"):
                raise ValueError("事实来源类型无效")
            kind: EvidenceKind = atom["kind"]
            quote = atom.get("quote")
            source = evidence.caption if kind == "visual_observation" else evidence.user_request
            if not isinstance(quote, str) or not quote.strip() or len(quote) > 500 or quote not in source:
                raise ValueError("事实必须引用对应来源的原文")
            if atom.get("text") != quote:
                raise ValueError("事实不能扩写来源不支持的内容")
            atoms.append(EpisodeAtom(quote, kind, quote))
        context = evidence.user_request + "\n" + evidence.caption
        fields: dict[str, str] = {}
        for name in ("game", "subject", "variant", "game_version"):
            field = value.get(name, "")
            if not isinstance(field, str) or len(field) > 80 or (field and field not in context):
                raise ValueError("游戏实体和版本必须有来源")
            fields[name] = field
        aspect = value.get("aspect", "other")
        if aspect not in ("lineup", "equipment", "encounter", "result", "other"):
            raise ValueError("经历方面无效")
        return cls(evidence, tuple(atoms), aspect=aspect, **fields)


class VisualMemorySource(Protocol):
    def current_frame(self, *, requested_at: float) -> VisualFrame | None: ...
    async def describe(self, image: bytes, question: str) -> str: ...


class VisualEpisodeExtractor(Protocol):
    async def extract(self, evidence: VisualEvidence) -> VisualEpisode: ...


@runtime_checkable
class VisualEpisodeBackend(Protocol):
    async def remember_episode(self, episode: dict[str, Any]) -> dict[str, Any]: ...
    async def forget(self, memory_ids: list[str]) -> dict[str, Any]: ...
    async def forget_episode(self, evidence_id: str) -> dict[str, Any]: ...


class VisualEvidenceStore(Protocol):
    async def put(self, evidence: VisualEvidence, image: bytes) -> VisualEvidence: ...
    async def pending(self) -> list[VisualEvidence]: ...
    async def attempt(self, evidence_id: str) -> bool: ...
    async def bind(self, evidence_id: str, memory_ids: list[str]) -> None: ...
    async def compiled(self, evidence_id: str) -> VisualEpisode | None: ...
    async def save_compiled(self, episode: VisualEpisode) -> None: ...
    async def write_allowed(self, evidence_id: str) -> bool: ...
    async def failure(self, evidence_id: str, error: str) -> None: ...
    async def for_memory(self, memory_id: str) -> VisualEvidence | None: ...
    async def memory_ids(self, evidence_id: str) -> list[str]: ...
    async def read(self, evidence: VisualEvidence) -> bytes: ...
    async def tombstone(self, evidence_id: str) -> None: ...
    async def deleting(self) -> list[tuple[str, list[str]]]: ...
    async def finish_delete(self, evidence_id: str) -> None: ...


def requests_visual_memory(text: str) -> bool:
    """An image-retention request must come from the persisted human message."""
    if re.search(r"(不要|别|不用|无需|不准).{0,4}(记住|保存)|忘记|回忆|还记得|don't|do not|never", text, re.IGNORECASE):
        return False
    remember = re.search(r"记住|记下来|保存|remember|save", text, re.IGNORECASE)
    target = re.search(r"刚才这[个套]|这张|这套|画面|截图|屏幕|配队|配装|装备|阵容|screen|image|lineup|loadout",
                       text, re.IGNORECASE)
    return bool(remember and target)


def references_past_visual_memory(text: str) -> bool:
    """Old media cannot be matched to the current foreground frame implicitly."""
    return bool(re.search(r"上次|昨天|前天|上周|上个月|去年|之前那|以前那|yesterday|previous|"
                          r"last\s+(?:time|week|month|year)", text, re.IGNORECASE))
