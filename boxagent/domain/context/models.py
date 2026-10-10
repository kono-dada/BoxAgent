"""Session Context Ledger records and their pure projections.

The ledger is an append-only log of model-visible context facts for one
Product Session. A ``context/rebuild`` record starts a new provider context
generation: entries before it stay in the timeline but leave the current
composition.
"""

from dataclasses import dataclass, field
from typing import Any


LEDGER_VERSION = 1

# Category order is also the display order of the composition bar.
CATEGORIES = (
    ("instructions", "系统指令"),
    ("persona", "角色设定"),
    ("tools", "工具定义"),
    ("profile", "用户画像"),
    ("checkpoint", "上下文检查点"),
    ("history", "恢复历史"),
    ("environment", "环境包"),
    ("memory", "记忆包"),
    ("user", "用户消息"),
    ("assistant", "助手回复"),
    ("tool_call", "工具调用"),
    ("tool_result", "工具结果"),
    ("notification", "任务通知"),
    ("system_other", "其他系统消息"),
)
CATEGORY_LABELS = dict(CATEGORIES)

SURFACE_TYPES = frozenset({
    "system/message", "user/message", "assistant/message",
    "tool/call", "tool/result", "request/header",
})
PREVIEW_CHARS = 160


@dataclass(frozen=True)
class LedgerEvent:
    seq: int
    type: str
    time: float
    data: dict[str, Any] = field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        return {"type": self.type, "seq": self.seq, "time": self.time,
                "data": self.data}

    @classmethod
    def from_payload(cls, value: dict[str, Any]):
        if not isinstance(value, dict) or not isinstance(value.get("type"), str):
            raise ValueError("Context Ledger 记录缺少 type")
        seq = value.get("seq")
        if not isinstance(seq, int) or seq < 1:
            raise ValueError("Context Ledger 记录缺少有效 seq")
        return cls(seq=seq, type=value["type"], time=float(value.get("time") or 0),
                   data=dict(value.get("data") or {}))


def distribute_percent(values, *, digits=1):
    """Largest-remainder rounding so the shares always sum to exactly 100."""
    total = sum(values)
    if total <= 0:
        return [0.0 for _ in values]
    scale = 10 ** digits
    exact = [value * 100 * scale / total for value in values]
    floors = [int(item) for item in exact]
    remainder = 100 * scale - sum(floors)
    order = sorted(range(len(values)), key=lambda index: exact[index] - floors[index],
                   reverse=True)
    for index in order[:remainder]:
        floors[index] += 1
    return [item / scale for item in floors]


def _entry(event: LedgerEvent) -> dict[str, Any]:
    content = str(event.data.get("content") or "")
    preview = content.replace("\n", " ").strip()
    if len(preview) > PREVIEW_CHARS:
        preview = preview[:PREVIEW_CHARS] + "…"
    category = event.data.get("category") or "system_other"
    return {
        "seq": event.seq,
        "type": event.type,
        "time": event.time,
        "category": category,
        "label": CATEGORY_LABELS.get(category, category),
        "role": event.data.get("role", ""),
        "chars": int(event.data.get("chars") or 0),
        "tokens": int(event.data.get("tokens") or 0),
        "preview": preview,
    }


def _composition(entries):
    rows = []
    for key, label in CATEGORIES:
        selected = [item for item in entries if item["category"] == key]
        rows.append({
            "key": key, "label": label, "count": len(selected),
            "chars": sum(item["chars"] for item in selected),
            "tokens": sum(item["tokens"] for item in selected),
        })
    for row, percent in zip(rows, distribute_percent([row["tokens"] for row in rows])):
        row["percent"] = percent
    return rows


def _turns(events):
    """Fold lifecycle facts into per-turn latency rows."""
    turns, current = [], None

    def open_turn(event, channel):
        nonlocal current
        current = {"index": len(turns) + 1, "started_at": event.time,
                   "channel": channel, "user": "", "environment_count": 0,
                   "memory": None, "response_created_at": None,
                   "assistant_at": None, "tool_calls": []}
        turns.append(current)

    for event in events:
        data = event.data
        category = data.get("category")
        if event.type == "turn/start":
            open_turn(event, data.get("channel") or "text")
        elif current is None:
            continue
        elif event.type == "user/message" and category == "user":
            current["user"] = str(data.get("content") or "")[:80]
        elif event.type == "system/message" and category == "environment":
            current["environment_count"] += 1
        elif event.type == "turn/prepare":
            current["memory"] = dict(data.get("memory") or {})
            current["memory"]["recorded_at"] = event.time
            current["prepare_wall_ms"] = data.get("wall_ms")
        elif event.type == "response/created" and current["response_created_at"] is None:
            current["response_created_at"] = event.time
        elif event.type == "assistant/message" and current["assistant_at"] is None:
            current["assistant_at"] = event.time
        elif event.type == "tool/call":
            current["tool_calls"].append(str(data.get("name") or ""))

    rows = []
    for turn in turns:
        def elapsed(moment, origin=turn["started_at"]):
            return None if moment is None else round((moment - origin) * 1000)
        memory = turn["memory"]
        memory_row = None
        if memory is not None:
            injected_at = memory.get("recorded_at")
            memory_row = {
                "status": memory.get("status", ""),
                "ms": memory.get("ms"),
                "chars": memory.get("chars", 0),
                # A packet appended after the provider created the response
                # cannot influence that response.
                "late": (turn["response_created_at"] is not None
                         and injected_at is not None
                         and injected_at > turn["response_created_at"]),
            }
        rows.append({
            "index": turn["index"], "channel": turn["channel"],
            "started_at": turn["started_at"], "user": turn["user"],
            "environment_count": turn["environment_count"],
            "memory": memory_row,
            "response_ms": elapsed(turn["response_created_at"]),
            "assistant_ms": elapsed(turn["assistant_at"]),
            "tool_calls": turn["tool_calls"],
        })
    return rows


def project_context(session_id: str, events) -> dict[str, Any]:
    """Return the current composition and the full per-turn timeline."""
    events = sorted(events, key=lambda item: item.seq)
    rebuilds = [item for item in events if item.type == "context/rebuild"]
    start = rebuilds[-1].seq if rebuilds else 0
    current = [item for item in events if item.seq >= start]
    entries = [_entry(item) for item in current if item.type in SURFACE_TYPES]
    composition = _composition(entries)
    total_tokens = sum(row["tokens"] for row in composition)
    total_chars = sum(row["chars"] for row in composition)
    header = rebuilds[-1].data if rebuilds else {}
    counts = {row["key"]: row["count"] for row in composition}
    return {
        "schema_version": LEDGER_VERSION,
        "session_id": session_id,
        "runtime": header.get("runtime", ""),
        "model": header.get("model", ""),
        "generation": len(rebuilds),
        "generation_started_at": rebuilds[-1].time if rebuilds else None,
        "event_count": len(events),
        "totals": {"chars": total_chars, "tokens": total_tokens,
                   "entries": len(entries)},
        "composition": composition,
        "entries": entries,
        "turns": _turns(current),
        "all_turn_count": len(_turns(events)),
        "accumulation": {
            "environment": counts.get("environment", 0),
            "memory": counts.get("memory", 0),
        },
    }
