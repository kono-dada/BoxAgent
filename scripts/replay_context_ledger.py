"""Replay an existing Qwen wire trace into Context Ledgers (offline, no API calls).

Usage::

    python scripts/replay_context_ledger.py [trace.jsonl] [--memory-log memory/events.jsonl]
        [--out DIR] [--json]

The replay uses the production ``QwenContextRecorder``, so the resulting ledgers
are what REQ-01 would have recorded live. Recall latency is joined from the
memory log by ``session_id`` and time order. Ledgers go to a temporary
directory by default; real Session directories are never modified.
"""

import argparse
import json
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from boxagent.domain.context import project_context  # noqa: E402
from boxagent.infrastructure.context.qwen_ledger import QwenContextRecorder  # noqa: E402
from boxagent.infrastructure.persistence.jsonl_context_ledger import (  # noqa: E402
    JsonlContextLedgerStore,
)


DATA = ROOT / ".runtime/pet"


def load(path):
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def replay(trace_path, memory_path, out_dir):
    clock = {"now": 0.0}
    store = JsonlContextLedgerStore(out_dir)
    recorder = QwenContextRecorder(store, model="replay", clock=lambda: clock["now"])
    recalls = defaultdict(list)
    for row in load(memory_path):
        if row.get("event") == "memory_recall_finished":
            recalls[row.get("session_id")].append(row)
    sessions = set()
    # The live host calls ``turn_started`` before text-turn recall; the wire
    # trace has no such record, so emulate it: a memory item that does not
    # follow a voice transcript belongs to a new text turn.
    voice = defaultdict(lambda: {"open": False, "user": False, "memory": False})
    for row in load(trace_path):
        session_id = row.get("product_session_id")
        if not session_id:
            continue
        clock["now"] = float(row.get("occurred_at") or 0)
        sessions.add(session_id)
        event = row.pop("event")
        state = voice[session_id]
        if event == "input_audio_buffer.speech_started":
            state.update(open=True, user=False, memory=False)
        elif event == "conversation.item.input_audio_transcription.completed":
            state["user"] = True
        elif event == "client.request":
            item = (row.get("request") or {}).get("item") or {}
            text = "".join(str(part.get("text") or "") for part in item.get("content") or [])
            if item.get("role") == "system" and text.startswith("<boxagent_memory_context>"):
                if state["open"] and state["user"] and not state["memory"]:
                    state["memory"] = True
                else:
                    state["open"] = False
                    recorder.turn_started(session_id, channel="text")
        if event == "memory_context.injected":
            # Join the recall that finished closest before this injection.
            candidates = [item for item in recalls.get(session_id, [])
                          if item["occurred_at"] <= clock["now"] + 0.01]
            recall = candidates[-1] if candidates else {}
            recorder.turn_prepared(session_id, memory={
                "status": "injected", "ms": recall.get("elapsed_ms"),
                "chars": row.get("character_count", 0)})
            continue
        recorder.observe(event, **row)
    return store, sorted(sessions)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("trace", nargs="?", default=str(DATA / "logs/runtime/qwen/events.jsonl"))
    parser.add_argument("--memory-log", default=str(DATA / "logs/memory/events.jsonl"))
    parser.add_argument("--out")
    parser.add_argument("--json", action="store_true")
    arguments = parser.parse_args()
    with tempfile.TemporaryDirectory() as temporary:
        out_dir = Path(arguments.out or temporary)
        store, sessions = replay(Path(arguments.trace), Path(arguments.memory_log), out_dir)
        report = []
        for session_id in sessions:
            try:
                events = store.read(session_id)
            except ValueError:
                continue
            snapshot = project_context(session_id, events)
            all_turns = project_context(session_id, [
                item for item in events if item.type != "context/rebuild"])["turns"]
            report.append({"snapshot": snapshot, "all_turns": all_turns})
        if arguments.json:
            print(json.dumps(report, ensure_ascii=False))
            return
        for item in report:
            snapshot = item["snapshot"]
            print(snapshot["session_id"], snapshot["generation"], snapshot["totals"],
                  snapshot["accumulation"], len(item["all_turns"]))


if __name__ == "__main__":
    main()
