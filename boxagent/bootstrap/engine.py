"""Compose the application services that live inside the Engine process."""

from functools import partial
import time

from boxagent.agent.harness import RuntimeRequestBuilder, TaskExecutor
from boxagent.agent.harness.context import RuntimeContextProjector
from boxagent.agent.harness.instructions import append_persona
from boxagent.agent.harness.persona import load_persona
from boxagent.agent.harness.policies import ComputerUseToolGateway, can_auto_approve_computer_use
from boxagent.agent.runtime.registry import AgentRuntimeRegistry
from boxagent.application.assistant import BoxAgentApplication
from boxagent.application.context_checkpoint import ContextCheckpointCoordinator
from boxagent.application.memory_context import MemoryContextProvider
from boxagent.application.memory_extraction import MemoryExtractionCoordinator
from boxagent.bootstrap.settings import load_settings
from boxagent.domain.conversation import ConversationService
from boxagent.domain.notification import NotificationOutbox
from boxagent.domain.memory.service import MemoryService
from boxagent.domain.skill import SkillService
from boxagent.core.errors import append_jsonl
from boxagent.infrastructure.memory import JevMemoryWorker
from boxagent.infrastructure.environment import LocalSystemEnvironmentProvider
from boxagent.infrastructure.perception import WindowSummary
from boxagent.infrastructure.persistence import (
    JsonNotificationRepository,
    JsonMemoryLedger,
    JsonlSessionStore,
    SkillFileRepository,
)
from boxagent.infrastructure.runtimes.codex import (
    CodexAgentRuntime,
    CodexCheckpointGenerator,
    CodexMemoryCandidateExtractor,
    CodexRuntimeHost,
    create_codex_session,
)


def create_agent_runtime_registry(app_settings):
    return AgentRuntimeRegistry(
        task_model=app_settings.task_model,
        deepseek_model=app_settings.deepseek_model,
        runtime_factory=CodexAgentRuntime,
        deepseek_api_key=app_settings.deepseek_api_key,
        deepseek_base_url=app_settings.deepseek_base_url,
        deepseek_model_catalog=app_settings.root / "assets/model-catalogs/deepseek.json",
    )


def create_voice_factory(app_settings, *, persona=None,
                         environment_provider=None, context_projector=None):
    # Keep realtime audio dependencies out of task-runtime imports and tests.
    from boxagent.infrastructure.runtimes.qwen.realtime import INSTRUCTIONS, QwenRealtimeSession

    environment_provider = environment_provider or LocalSystemEnvironmentProvider()
    context_projector = context_projector or RuntimeContextProjector()
    return partial(
        QwenRealtimeSession,
        model=app_settings.voice_model,
        key=app_settings.qwen_api_key,
        instructions=append_persona(INSTRUCTIONS, persona),
        environment_context=lambda: context_projector.environment_packet(
            environment_provider.capture()),
    )


def create_task_executor(task_id, *, provider=None, model=None, auto_approve=True,
                         session_factory=None, tool_gateway_factory=ComputerUseToolGateway,
                         request_builder=None, app_settings=None, registry=None,
                         session_id=None, interaction_id=None, prior_messages=(),
                         runtime_binding=None, conversation_service=None,
                         environment_provider=None, memory_provider=None):
    app_settings = app_settings or load_settings()
    registry = registry or create_agent_runtime_registry(app_settings)
    selected = registry.runtime(provider or app_settings.task_provider, model)
    request_builder = request_builder or RuntimeRequestBuilder(
        persona=load_persona(app_settings.soul_file))
    session_factory = session_factory or partial(
        create_codex_session,
        workspace=app_settings.root,
        model_profile=selected.profile,
        permission_check=can_auto_approve_computer_use,
    )
    return TaskExecutor(
        task_id,
        provider=selected.provider,
        model=selected.model,
        output=app_settings.log_dir / "tasks" / task_id,
        runtime_factory=selected.factory,
        session_factory=session_factory,
        tool_gateway_factory=tool_gateway_factory,
        request_factory=request_builder.prepare,
        auto_approve=auto_approve,
        session_id=session_id,
        interaction_id=interaction_id,
        prior_messages=prior_messages,
        runtime_binding=runtime_binding,
        environment_provider=(environment_provider
                              or LocalSystemEnvironmentProvider()),
        memory_provider=memory_provider,
        on_thread_bound=(
            lambda thread_id: conversation_service.bind_runtime(
                session_id, runtime="codex", provider=selected.provider,
                model=selected.model, thread_id=thread_id)
            if conversation_service and session_id else None),
    )


def create_application(publish, *, task_provider=None, task_model=None,
                       auto_approve=True, memory_backend=None,
                       voice_factory=None, app_settings=None, registry=None,
                       context_interval=15, context_size=960):
    """Build the complete backend without importing AppKit or another UI module."""
    app_settings = app_settings or load_settings()
    registry = registry or create_agent_runtime_registry(app_settings)
    selected = registry.runtime(task_provider or app_settings.task_provider, task_model)
    persona = load_persona(app_settings.soul_file)
    request_builder = RuntimeRequestBuilder(
        persona=persona,
        context_assembler=RuntimeContextProjector(
            history_character_budget=app_settings.codex_history_character_budget))
    environment_provider = LocalSystemEnvironmentProvider()
    conversation_store = JsonlSessionStore(app_settings.data_dir / "conversations")
    memory_ledger = JsonMemoryLedger(app_settings.data_dir / "memory")
    notification_outbox = NotificationOutbox(JsonNotificationRepository(
        app_settings.data_dir / "notifications/outbox.json"))
    skill_service = SkillService(SkillFileRepository(
        builtin_root=app_settings.builtin_skills_dir,
        user_root=app_settings.user_skills_dir,
    ))
    runtime_host = CodexRuntimeHost(
        output=app_settings.log_dir / "runtime/codex",
        workspace=app_settings.root,
        codex_home=app_settings.codex_home,
        model_profile=selected.profile,
        permission_check=can_auto_approve_computer_use,
        skill_service=skill_service,
    )
    checkpoint_log_path = app_settings.log_dir / "context-checkpoint.jsonl"

    def checkpoint_log(event, **data):
        append_jsonl(checkpoint_log_path, {
            "event": event, "occurred_at": time.time(), **data})

    checkpoint_generator = CodexCheckpointGenerator(
        runtime_host.create_session,
        model=selected.model,
        provider=selected.provider,
        log=checkpoint_log,
    )
    checkpoint_coordinator = ContextCheckpointCoordinator(
        conversation_store,
        checkpoint_generator,
        trigger_characters=app_settings.qwen_checkpoint_trigger_characters,
        source_character_limit=app_settings.checkpoint_source_character_limit,
        log=checkpoint_log,
    )
    backend = memory_backend if memory_backend is not None else JevMemoryWorker(
        python=app_settings.jev_memory_python,
        source=app_settings.jev_memory_source,
        cache_dir=app_settings.jev_memory_cache,
        backend=app_settings.jev_memory_backend,
        worker_script=app_settings.root / "scripts/jev_memory_worker.py",
        workspace=app_settings.root,
        typesafe_api_key=app_settings.typesafe_api_key,
        log_path=app_settings.log_dir / "memory-worker.log",
    )
    memory_context = MemoryContextProvider(
        memory_ledger, backend,
        profile_path=app_settings.data_dir / "memory/profiles/stable-profile.json")
    memory_service = MemoryService(
        backend, memory_ledger,
        projection_callback=memory_context.refresh_stable_profile)
    memory_extractor = CodexMemoryCandidateExtractor(
        runtime_host.create_session,
        model=selected.model,
        provider=selected.provider,
        log=checkpoint_log,
    )
    memory_extraction = MemoryExtractionCoordinator(
        conversation_store, memory_ledger, memory_extractor, backend,
        log=checkpoint_log, memory_service=memory_service,
    )
    memory_service.index_enqueue = memory_extraction.schedule_index
    conversation_service = ConversationService(
        conversation_store, extraction_sink=memory_extraction,
        checkpoint_sink=checkpoint_coordinator)
    application = BoxAgentApplication(
        publish,
        lambda task_id, **context: create_task_executor(
            task_id, provider=task_provider, model=task_model, auto_approve=auto_approve,
            session_factory=runtime_host.create_session, request_builder=request_builder,
            app_settings=app_settings, registry=registry,
            environment_provider=environment_provider,
            memory_provider=memory_context,
            conversation_service=conversation_service, **context),
        voice_factory or create_voice_factory(
            app_settings, persona=persona,
            environment_provider=environment_provider,
            context_projector=request_builder.context_assembler),
        memory_backend=backend,
        memory_ledger=memory_ledger,
        conversation_service=conversation_service,
        notification_outbox=notification_outbox,
        skill_service=skill_service,
        memory_context_provider=memory_context,
        memory_service=memory_service,
        runtime_resources=(runtime_host, checkpoint_coordinator, memory_extraction),
        voice_history_builder=partial(
            request_builder.context_assembler.restore_context,
            character_budget=app_settings.qwen_history_character_budget),
    )
    application.observer = WindowSummary(
        application.emit, context_interval or 15,
        model=app_settings.perception_model,
        max_size=context_size,
        workspace=app_settings.root,
        python=app_settings.root / ".venv/bin/python",
        worker_script=app_settings.root / "scripts/window_summary_worker.py",
        log_path=app_settings.log_dir / "context-worker.log",
    )
    return application
