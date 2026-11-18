"""Compose the production Memory capability behind one application boundary."""

from boxagent.application.memory import DisabledMemoryModule, MemoryModule
from boxagent.application.memory_context import MemoryContextProvider
from boxagent.application.memory_ingestion import MemoryIngestionCoordinator
from boxagent.application.memory_migration import LegacyCanonicalMemoryImporter
from boxagent.application.memory_narrative import NarrativeProjector
from boxagent.application.memory_profile import (
    DeepSeekProfileConstructor,
    JsonProfileStore,
    ProfileProjector,
)
from boxagent.domain.memory.service import MemoryService
from boxagent.infrastructure.memory import JevMemWorker
from boxagent.infrastructure.persistence import JsonMemoryJobStore
from boxagent.bootstrap.settings import Settings
from boxagent.infrastructure.context.intent import IntentReference
from boxagent.application.visual_memory import VisualMemoryCoordinator
from boxagent.domain.memory.visual import VisualEvidence, VisualMemorySource, VisualEpisodeBackend
from boxagent.infrastructure.persistence.visual_evidence import LocalVisualEvidenceStore
from boxagent.infrastructure.memory.visual_extractor import GroundedVisualEpisodeExtractor
from boxagent.domain.conversation.contracts import SessionStore
from boxagent.domain.conversation.models import ProductEvent
from boxagent.core.ids import new_event_id
from typing import Any
import time


def create_intent_reference(app_settings: Settings) -> IntentReference:
    worker = JevMemWorker(
        python=app_settings.jev_mem_python, workspace=app_settings.root,
        command=[str(app_settings.jev_mem_python), str(app_settings.root / "scripts/intent_worker.py")],
        decision_api_key=app_settings.decision_api_key,
        decision_base_url=app_settings.decision_base_url,
        decision_model=app_settings.decision_model,
        log_path=app_settings.log_dir / "intent-worker.log")
    return IntentReference(worker)


def create_memory_module(*, app_settings, conversation_store, runtime_host,
                         model, provider, log, backend_override=None):
    del runtime_host, model, provider
    # This store carries durable ingestion jobs only. Jev-Mem owns all memory
    # nodes, relations, indexes and snapshots.
    job_store = JsonMemoryJobStore(app_settings.data_dir / "memory")
    backend = backend_override if backend_override is not None else JevMemWorker(
        python=app_settings.jev_mem_python,
        cache_dir=app_settings.jev_mem_cache,
        backend=app_settings.jev_mem_backend,
        workspace=app_settings.root,
        decision_api_key=app_settings.decision_api_key,
        decision_base_url=app_settings.decision_base_url,
        decision_model=app_settings.decision_model,
        log_path=app_settings.log_dir / "memory/worker.log",
    )
    profile_store = JsonProfileStore(
        app_settings.data_dir / "memory/projections/profile.json")
    profile_projector = ProfileProjector(
        profile_store,
        DeepSeekProfileConstructor(
            api_key=app_settings.deepseek_api_key,
            base_url=app_settings.deepseek_base_url,
            model=app_settings.deepseek_model,
        ),
    )
    context = MemoryContextProvider(backend, profile_store)
    service = MemoryService(
        backend, job_store,
        projection_callback=context.refresh_stable_profile,
        profile_projector=profile_projector,
    )
    ingestion = MemoryIngestionCoordinator(
        conversation_store, job_store,
        log=log, service=service,
    )
    narrative = NarrativeProjector(backend, log=log)
    migration = LegacyCanonicalMemoryImporter(
        backend,
        app_settings.data_dir / "memory/ledger/snapshot.json",
        app_settings.data_dir / "memory/migrations/canonical-ledger-v1.json",
        profile_projector=profile_projector,
        log=log,
    )
    return MemoryModule(
        service, context, ingestion, narrative, migration, trace=log)


def configure_visual_memory(memory: MemoryModule | DisabledMemoryModule, source: VisualMemorySource, *,
                            app_settings: Settings, conversation_store: SessionStore) -> None:
    if isinstance(memory, DisabledMemoryModule):
        return

    async def completed(evidence: VisualEvidence, result: dict[str, Any]) -> None:
        await conversation_store.append_event(ProductEvent(
            sequence=0, event_id=new_event_id(), type="memory.visual.completed",
            session_id=evidence.session_id, interaction_id=evidence.interaction_id,
            occurred_at=time.time(), source="visual_memory",
            data={"evidence_id": evidence.evidence_id, **result}))

    backend = memory._service.backend
    if isinstance(backend, VisualEpisodeBackend):
        memory.configure_visual(VisualMemoryCoordinator(
            backend, LocalVisualEvidenceStore(app_settings.data_dir / "memory/media"),
            GroundedVisualEpisodeExtractor(api_key=app_settings.deepseek_api_key,
                                           base_url=app_settings.deepseek_base_url,
                                           model=app_settings.deepseek_model),
            source, completed=completed))
