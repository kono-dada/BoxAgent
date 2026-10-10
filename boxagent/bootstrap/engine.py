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
from boxagent.bootstrap.memory import create_memory_module
from boxagent.application.skill_authoring import SkillAuthoringService
from boxagent.bootstrap.settings import load_settings
from boxagent.domain.conversation import ConversationService
from boxagent.domain.notification import NotificationOutbox
from boxagent.domain.skill import SkillService
from boxagent.core.errors import append_jsonl
from boxagent.infrastructure.environment import LocalSystemEnvironmentProvider
from boxagent.infrastructure.perception import WindowSummary
from boxagent.infrastructure.persistence import (
    JsonNotificationRepository,
    JsonSkillDraftRepository,
    JsonTaskTraceReader,
    JsonlSessionStore,
    SkillFileRepository,
)
from boxagent.infrastructure.runtimes.codex import (
    CodexAgentRuntime,
    CodexCheckpointGenerator,
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
    from boxagent.infrastructure.runtimes.qwen.aoq import AoqRealtimeSession, aoq_preflight

    environment_provider = environment_provider or LocalSystemEnvironmentProvider()
    context_projector = context_projector or RuntimeContextProjector()
    trace_path = app_settings.log_dir / "runtime/qwen/events.jsonl"

    def qwen_trace(event, **data):
        append_jsonl(trace_path, {
            "event": event, "occurred_at": time.time(), **data})

    transport = app_settings.qwen_transport
    if transport not in {"auto", "aoq", "websocket"}:
        raise ValueError(
            "BOXAGENT_QWEN_TRANSPORT 只支持 auto、aoq 或 websocket")
    session_class = QwenRealtimeSession
    transport_options = {}
    if transport != "websocket":
        available, reason = aoq_preflight(
            sdk_dir=app_settings.aoq_sdk_dir,
            workspace_id=app_settings.qwen_workspace_id)
        if available:
            session_class = AoqRealtimeSession
            transport_options = {
                "workspace_id": app_settings.qwen_workspace_id,
                "region": app_settings.qwen_region,
                "sdk_dir": app_settings.aoq_sdk_dir,
                "work_dir": app_settings.aoq_work_dir,
            }
            qwen_trace("transport.selected", transport="aoq")
        else:
            qwen_trace(
                "transport.fallback", requested=transport,
                selected="websocket", reason=reason)
    else:
        qwen_trace("transport.selected", transport="websocket")

    return partial(
        session_class,
        model=app_settings.voice_model,
        key=app_settings.qwen_api_key,
        instructions=append_persona(INSTRUCTIONS, persona),
        trace=qwen_trace,
        environment_context=lambda: context_projector.environment_packet(
            environment_provider.capture()),
        **transport_options,
    )


def create_task_executor(task_id, *, provider=None, model=None, auto_approve=True,
                         session_factory=None, tool_gateway_factory=ComputerUseToolGateway,
                         request_builder=None, app_settings=None, registry=None,
                         session_id=None, interaction_id=None, prior_messages=(),
                         runtime_binding=None, conversation_service=None,
                         environment_provider=None, memory=None):
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
        output=app_settings.task_run_dir(task_id),
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
        memory=memory,
        run_index_path=app_settings.runs_dir / "index.jsonl",
        latest_run_path=app_settings.runs_dir / "latest.json",
        on_thread_bound=(
            lambda thread_id: conversation_service.bind_runtime(
                session_id, runtime="codex", provider=selected.provider,
                model=selected.model, thread_id=thread_id)
            if conversation_service and session_id else None),
    )


def create_application(publish, *, task_provider=None, task_model=None,
                       auto_approve=True, memory_backend=None,
                       voice_factory=None, app_settings=None, registry=None,
                       context_interval=0, context_size=960):
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
    checkpoint_log_path = app_settings.log_dir / "context/checkpoints.jsonl"
    memory_log_path = app_settings.log_dir / "memory/events.jsonl"

    def checkpoint_log(event, **data):
        append_jsonl(checkpoint_log_path, {
            "event": event, "occurred_at": time.time(), **data})

    def memory_log(event, **data):
        append_jsonl(memory_log_path, {
            "event": event, "occurred_at": time.time(), **data})

    checkpoint_generator = CodexCheckpointGenerator(
        runtime_host.create_session,
        model=selected.model,
        provider=selected.provider,
        log=checkpoint_log,
    )
    skill_authoring = SkillAuthoringService(
        skill_service,
        JsonSkillDraftRepository(app_settings.data_dir / "skills/.drafts"),
        runtime_host.create_session,
        model=selected.model,
        provider=selected.provider,
        log=checkpoint_log,
    )
    task_trace_reader = JsonTaskTraceReader(
        app_settings.runs_dir,
        fallback_roots=(app_settings.data_dir / "tasks",
                        app_settings.log_dir / "tasks"))
    memory = create_memory_module(
        app_settings=app_settings,
        conversation_store=conversation_store,
        runtime_host=runtime_host,
        model=selected.model,
        provider=selected.provider,
        log=memory_log,
        backend_override=memory_backend,
    )
    checkpoint_coordinator = ContextCheckpointCoordinator(
        conversation_store,
        checkpoint_generator,
        trigger_characters=app_settings.qwen_checkpoint_trigger_characters,
        source_character_limit=app_settings.checkpoint_source_character_limit,
        log=checkpoint_log,
        checkpoint_sinks=(memory,),
    )
    conversation_service = ConversationService(
        conversation_store, message_sinks=(memory,),
        finalization_sinks=(memory,),
        checkpoint_sink=checkpoint_coordinator)
    application = BoxAgentApplication(
        publish,
        lambda task_id, **context: create_task_executor(
            task_id, provider=task_provider, model=task_model, auto_approve=auto_approve,
            session_factory=runtime_host.create_session, request_builder=request_builder,
            app_settings=app_settings, registry=registry,
            environment_provider=environment_provider,
            memory=memory,
            conversation_service=conversation_service, **context),
        voice_factory or create_voice_factory(
            app_settings, persona=persona,
            environment_provider=environment_provider,
            context_projector=request_builder.context_assembler),
        memory=memory,
        conversation_service=conversation_service,
        notification_outbox=notification_outbox,
        skill_service=skill_service,
        skill_authoring=skill_authoring,
        task_trace_reader=task_trace_reader,
        runtime_resources=(runtime_host, checkpoint_coordinator),
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
