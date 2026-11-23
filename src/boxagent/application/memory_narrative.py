"""Project complete Context Checkpoints into Jev-Mem Narrative nodes."""

from boxagent.domain.conversation.models import ContextCheckpoint, RuntimeContextSegment
from boxagent.domain.memory.contracts import BackendRecord, MemoryBackend, MemoryLog


class NarrativeProjector:
    def __init__(self, backend: MemoryBackend, *, log: MemoryLog | None = None) -> None:
        self.backend = backend
        self.log = log or (lambda *_args, **_kwargs: None)

    async def checkpoint_created(
        self, checkpoint: ContextCheckpoint, segment: RuntimeContextSegment
    ) -> BackendRecord:
        result = await self.backend.remember_narrative(
            checkpoint.content,
            metadata={
                "checkpoint_id": checkpoint.checkpoint_id,
                "session_id": checkpoint.session_id,
                "runtime": checkpoint.runtime,
                "source_from_sequence": checkpoint.source_from_sequence,
                "covered_through_sequence": checkpoint.covered_through_sequence,
                "source_hash": checkpoint.source_hash,
                "segment_id": segment.segment_id,
                "narrative_level": "segment",
                "source_event_ids": [
                    item.get("event_id")
                    for item in checkpoint.content.get("salient_events", [])
                    if isinstance(item, dict) and item.get("event_id")
                ],
            },
        )
        self.log("memory_narrative_projected",
                 checkpoint_id=checkpoint.checkpoint_id,
                 created_ids=[item.get("id") for item in result.get("created", [])])
        return result
