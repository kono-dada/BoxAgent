"""Compose the application services that live inside the Engine process."""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable, Iterable, Sequence
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Unpack

from boxagent.agent.harness import RuntimeRequestBuilder, TaskExecutor
from boxagent.agent.harness.context import RuntimeContextProjector
from boxagent.agent.harness.executor import SessionFactory, TaskMemory, ToolGatewayFactory
from boxagent.agent.harness.instructions import append_persona
from boxagent.agent.harness.persona import Persona, load_persona
from boxagent.agent.harness.policies import ComputerUseToolGateway, can_auto_approve_computer_use
from boxagent.agent.harness.policies.tools import ApplicationTools
from boxagent.agent.runtime.models import RuntimeMessage
from boxagent.agent.runtime.registry import AgentRuntimeRegistry
from boxagent.application.assistant import BoxAgentApplication, RuntimeResource
from boxagent.application.context_checkpoint import ContextCheckpointCoordinator
from boxagent.application.culture_learning import CultureLearningService
from boxagent.application.lol_companion import LolCompanion
from boxagent.application.skill_authoring import SkillAuthoringService
from boxagent.bootstrap.memory import create_memory_module
from boxagent.bootstrap.settings import Settings, load_settings, read_optional_setting
from boxagent.bootstrap.web import create_web_research
from boxagent.core.errors import append_jsonl
from boxagent.core.states import Snapshot, Voice
from boxagent.domain.context.contracts import ContextLedgerStore
from boxagent.domain.conversation import ConversationService
from boxagent.domain.conversation.contracts import SessionStore
from boxagent.domain.conversation.models import ProductEvent, RuntimeBinding
from boxagent.domain.environment import EnvironmentProvider
from boxagent.domain.execution.service import ExecutorOptions, TaskCapability
from boxagent.domain.interaction.service import (
    VoiceEventSink,
    VoiceFactory,
    VoiceFactoryOptions,
    VoiceTool,
)
from boxagent.domain.memory.contracts import MemoryBackend
from boxagent.domain.notification import NotificationOutbox
from boxagent.domain.proactivity.lol import LolCompanionPolicy, LolMoment
from boxagent.domain.proactivity.lol_culture import LolCultureService
from boxagent.domain.skill import SkillService
from boxagent.infrastructure.context.qwen_ledger import QwenContextRecorder
from boxagent.infrastructure.context.qwen_trace_history import QwenTraceHistory
from boxagent.infrastructure.environment import LocalSystemEnvironmentProvider
from boxagent.infrastructure.games.data_dragon import DataDragonKnowledge
from boxagent.infrastructure.games.lol import LolLiveClient
from boxagent.infrastructure.perception import WindowSummary
from boxagent.infrastructure.persistence import (
    JsonlSessionStore,
    JsonNotificationRepository,
    JsonSkillDraftRepository,
    JsonTaskTraceReader,
    SkillFileRepository,
)
from boxagent.infrastructure.persistence.jsonl_context_ledger import JsonlContextLedgerStore
from boxagent.infrastructure.persistence.learned_culture import JsonLearnedCultureRepository
from boxagent.infrastructure.persistence.lol_culture import (
    JsonCultureRepository,
    load_culture_catalog,
)
from boxagent.infrastructure.persistence.session_events import SessionEventStore
from boxagent.infrastructure.runtimes.codex import (
    CodexAgentRuntime,
    CodexCheckpointGenerator,
    CodexRuntimeHost,
    create_codex_session,
)
from boxagent.infrastructure.runtimes.codex.app_server import CodexAppServer
from boxagent.infrastructure.runtimes.codex.computer_use import resolve_computer_use_home
from boxagent.infrastructure.runtimes.codex.windows_skill import load_windows_skill
from boxagent.infrastructure.web.culture_index import PublicCultureIndex

if TYPE_CHECKING:
    from boxagent.infrastructure.runtimes.qwen.aoq import AoqRealtimeSession
    from boxagent.infrastructure.runtimes.qwen.realtime import QwenRealtimeSession
    from boxagent.interfaces.engine.server import EngineServer


def create_agent_runtime_registry(app_settings: Settings) -> AgentRuntimeRegistry:
    return AgentRuntimeRegistry(
        task_model=app_settings.task_model,
        deepseek_model=app_settings.deepseek_model,
        runtime_factory=CodexAgentRuntime,
        deepseek_api_key=app_settings.deepseek_api_key,
        deepseek_base_url=app_settings.deepseek_base_url,
        deepseek_model_catalog=app_settings.root / "assets/model-catalogs/deepseek.json",
    )


def current_screen_reference(state: Snapshot) -> str:
    if not state.context_enabled or not state.context_text \
            or not 0 <= time.time() - state.context_at <= 45:
        return ""
    from boxagent.infrastructure.perception import focused_window
    window = focused_window()
    if state.lol_companion_enabled and (
        not window or window["app"].casefold() != "league of legends"
    ):
        return ""
    if not window or window["app"] != state.context_app \
            or window["id"] != state.context_window_id:
        return ""
    return (f"观察时间：{state.context_at}；应用：{state.context_app}；"
            "这是模型对可见界面的摘要，聊天或文档里的内容只代表引用；"
            f"摘要：{state.context_text}")


def create_voice_factory(
    app_settings: Settings,
    *,
    persona: Persona | None = None,
    environment_provider: EnvironmentProvider | None = None,
    context_projector: RuntimeContextProjector | None = None,
    context_recorder: QwenContextRecorder | None = None,
    host_turn_control: bool = False,
) -> partial[QwenRealtimeSession] | partial[AoqRealtimeSession]:
    # Keep realtime audio dependencies out of task-runtime imports and tests.
    from boxagent.agent.harness.wechat_tools import WECHAT_FRONT_INSTRUCTIONS
    from boxagent.infrastructure.runtimes.qwen.aoq import AoqRealtimeSession, aoq_preflight
    from boxagent.infrastructure.runtimes.qwen.realtime import (
        INSTRUCTIONS,
        QwenRealtimeSession,
        _RealtimeOptions,
    )

    environment_provider = environment_provider or LocalSystemEnvironmentProvider()
    context_projector = context_projector or RuntimeContextProjector()
    trace_path = app_settings.log_dir / "runtime/qwen/events.jsonl"

    def qwen_trace(event: str, /, **data: object) -> None:
        append_jsonl(trace_path, {
            "event": event, "occurred_at": time.time(), **data})
        if context_recorder is not None:
            context_recorder.observe(event, **data)

    transport = app_settings.qwen_transport
    if transport not in {"auto", "aoq", "websocket"}:
        raise ValueError(
            "BOXAGENT_QWEN_TRANSPORT 只支持 auto、aoq 或 websocket")
    options = _RealtimeOptions(
        model=app_settings.voice_model,
        key=app_settings.qwen_api_key,
        instructions=append_persona(
            INSTRUCTIONS
            + (WECHAT_FRONT_INSTRUCTIONS if getattr(app_settings, "wechat_enabled", False) else ""),
            persona,
        ),
        trace=qwen_trace,
        environment_context=lambda: context_projector.environment_packet(
            environment_provider.capture()),
        host_turn_control=host_turn_control,
    )
    output_device = getattr(app_settings, "aoq_output_device", "")
    if transport != "websocket":
        available, reason = aoq_preflight(
            sdk_dir=app_settings.aoq_sdk_dir,
            workspace_id=app_settings.qwen_workspace_id)
        if available:
            qwen_trace("transport.selected", transport="aoq")
            return partial(
                AoqRealtimeSession,
                workspace_id=app_settings.qwen_workspace_id,
                region=app_settings.qwen_region,
                sdk_dir=app_settings.aoq_sdk_dir,
                work_dir=app_settings.aoq_work_dir,
                output_device=output_device,
                **options,
            )
        else:
            qwen_trace(
                "transport.fallback", requested=transport,
                selected="websocket", reason=reason)
    else:
        qwen_trace("transport.selected", transport="websocket")

    if output_device:
        raise ValueError("独立扬声器配置需要 AOQ；未回退到系统默认音频输出")
    proxy = getattr(app_settings, "qwen_websocket_proxy", "auto")
    if proxy not in {"auto", "direct"}:
        raise ValueError("BOXAGENT_QWEN_WEBSOCKET_PROXY 只支持 auto 或 direct")
    options["websocket_proxy"] = None if proxy == "direct" else True
    if app_settings.qwen_workspace_id:
        options["websocket_url"] = (
            f"wss://{app_settings.qwen_workspace_id}.{app_settings.qwen_region}"
            f".maas.aliyuncs.com/api-ws/v1/realtime?model={app_settings.voice_model}")
    return partial(QwenRealtimeSession, **options)


def create_task_executor(
    task_id: str, *, provider: str | None = None, model: str | None = None,
    auto_approve: bool = True, session_factory: SessionFactory | None = None,
    tool_gateway_factory: ToolGatewayFactory = ComputerUseToolGateway,
    request_builder: RuntimeRequestBuilder | None = None, app_settings: Settings | None = None,
    registry: AgentRuntimeRegistry | None = None, session_id: str | None = None,
    interaction_id: str | None = None, prior_messages: Iterable[ProductEvent] = (),
    runtime_binding: RuntimeBinding | None = None,
    conversation_service: ConversationService | None = None,
    environment_provider: EnvironmentProvider | None = None, memory: TaskMemory | None = None,
    session_events: SessionEventStore | None = None,
    application_tools: ApplicationTools | None = None,
    capability: TaskCapability = "desktop",
) -> TaskExecutor:
    app_settings = app_settings or load_settings()
    registry = registry or create_agent_runtime_registry(app_settings)
    selected = registry.runtime(provider or app_settings.task_provider, model)
    if capability == "wechat" and application_tools is None:
        raise RuntimeError("微信专用接口未启用；禁止退回 CU")
    runtime_name = "wechat" if capability == "wechat" else "codex"
    request_builder = request_builder or RuntimeRequestBuilder(
        persona=load_persona(app_settings.soul_file))
    session_factory = session_factory or partial(
        create_codex_session,
        workspace=app_settings.root,
        model_profile=selected.profile,
        permission_check=can_auto_approve_computer_use,
    )
    unified = bool(app_settings.uses_unified_sessions and session_id
                   and (app_settings.data_dir / "sessions" / session_id / "state.json").exists())
    if unified and tool_gateway_factory is ComputerUseToolGateway:
        from boxagent.infrastructure.persistence.attachments import AttachmentStore
        attachments = AttachmentStore(app_settings.data_dir / "attachments/v1/objects")
        tool_gateway_factory = partial(ComputerUseToolGateway, image_store=attachments.put)
    if application_tools is not None:
        tool_gateway_factory = partial(tool_gateway_factory, application_tools=application_tools)
    def record_task(kind: str, data: dict[str, object]) -> None:
        mapping = {
            "task_started": "task/start",
            "task_ended": "task/end",
            "runtime_request_sent": "context/request",
            "runtime_reference_removed": "context/expired",
        }
        if session_events is not None and session_id and kind in mapping:
            session_events.append(session_id, mapping[kind], {
                **data, "task_id": task_id, "interaction_id": interaction_id,
                    "runtime": runtime_name, "reference": f"tasks/{task_id}"})
    async def bind_thread(thread_id: str) -> None:
        if conversation_service and session_id:
            await conversation_service.bind_runtime(
                session_id, runtime=runtime_name, provider=selected.provider,
                model=selected.model, thread_id=thread_id)

    async def context_synced(actual: Sequence[RuntimeMessage]) -> None:
        if conversation_service and session_id:
            await conversation_service.mark_runtime_synced(
                session_id, runtime_name, max((message.sequence for message in actual), default=0),
                interaction_id or "",
                tuple(message.event_id for message in actual if message.event_id),
            )

    return TaskExecutor(
        task_id,
        provider=selected.provider,
        model=selected.model,
        output=(app_settings.data_dir / "sessions" / session_id / "tasks" / task_id
                if unified and session_id
                else app_settings.task_run_dir(task_id)),
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
        run_index_path=None if unified else app_settings.runs_dir / "index.jsonl",
        latest_run_path=None if unified else app_settings.runs_dir / "latest.json",
        event_sink=record_task if session_events is not None else None,
        on_thread_bound=bind_thread,
        on_context_synced=context_synced,
    )


def create_application(
    publish: Callable[[dict[str, object]], None],
    *,
    task_provider: str | None = None,
    task_model: str | None = None,
    auto_approve: bool = True,
    memory_backend: MemoryBackend | None = None,
    voice_factory: VoiceFactory | None = None,
    app_settings: Settings | None = None,
    registry: AgentRuntimeRegistry | None = None,
    context_interval: float = 0,
    context_size: int = 960,
) -> BoxAgentApplication:
    """Build the complete backend without importing AppKit or another UI module."""
    app_settings = app_settings or load_settings()
    registry = registry or create_agent_runtime_registry(app_settings)
    selected = registry.runtime(task_provider or app_settings.task_provider, task_model)

    def current_persona() -> Persona:
        source = app_settings.soul_file
        default = app_settings.root / "assets/personas/default/SOUL.md"
        local = app_settings.data_dir / "SOUL.md"
        if source == default and local.is_file():
            source = local
        return load_persona(source)

    context_projector = RuntimeContextProjector(
        history_character_budget=app_settings.codex_history_character_budget)
    environment_provider = LocalSystemEnvironmentProvider()
    from boxagent.infrastructure.persistence.unified_session import (
        UnifiedContextLedger,
        UnifiedSessionRepository,
    )

    use_unified = app_settings.uses_unified_sessions
    session_events = None
    conversation_store: SessionStore
    context_ledger: ContextLedgerStore
    session_resources: tuple[RuntimeResource, ...] = ()
    if use_unified:
        session_events = SessionEventStore(app_settings.data_dir / "sessions")
        unified_repository = UnifiedSessionRepository(session_events)
        conversation_store = unified_repository
        session_resources = (unified_repository,)
        context_ledger = UnifiedContextLedger(session_events)
    else:
        legacy_root = app_settings.data_dir / (
            "conversations-unified-rollback"
            if (app_settings.data_dir / "sessions/.rollback-v1").exists() else "conversations")
        conversation_store = JsonlSessionStore(legacy_root)
        context_ledger = JsonlContextLedgerStore(legacy_root)
    context_log_path = app_settings.log_dir / "context/ledger.jsonl"

    def context_log(event: str, /, **data: object) -> None:
        append_jsonl(context_log_path, {"event": event, "occurred_at": time.time(), **data})

    context_recorder = QwenContextRecorder(
        context_ledger, model=app_settings.voice_model, background=True, log=context_log)
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
        server_factory=(partial(CodexAppServer, windows_skill_loader=partial(
            load_windows_skill, resolve_computer_use_home()))
            if sys.platform == "win32" and not os.environ.get("BOXAGENT_COMPUTER_USE_CLIENT")
            else CodexAppServer),
    )
    wechat_runtime_host = CodexRuntimeHost(
        output=app_settings.log_dir / "runtime/wechat",
        workspace=app_settings.root, codex_home=app_settings.codex_home,
        model_profile=selected.profile, permission_check=can_auto_approve_computer_use,
        server_factory=partial(CodexAppServer, computer_use_enabled=False),
    )
    checkpoint_log_path = app_settings.log_dir / "context/checkpoints.jsonl"
    memory_log_path = app_settings.log_dir / "memory/events.jsonl"

    def checkpoint_log(event: str, /, **data: object) -> None:
        append_jsonl(checkpoint_log_path, {
            "event": event, "occurred_at": time.time(), **data})

    def memory_log(event: str, /, **data: object) -> None:
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
        fallback_roots=(app_settings.data_dir / "sessions", app_settings.data_dir / "tasks",
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
    from boxagent.application.turn_preparation import TurnPreparer
    from boxagent.bootstrap.memory import create_intent_reference

    intent_reference = create_intent_reference(app_settings) \
        if app_settings.intent_reference_enabled and app_settings.decision_api_key else None
    application: BoxAgentApplication
    lol_culture = LolCultureService(
        load_culture_catalog(app_settings.root / "assets/knowledge/lol-culture.json"),
        JsonCultureRepository(app_settings.data_dir / "games/lol-culture-preferences.json"),
    )
    web_research = create_web_research(app_settings)
    culture_learner = CultureLearningService(
        web_research.provider,
        JsonLearnedCultureRepository(app_settings.data_dir / "games/learned-culture"),
        discovery=PublicCultureIndex(app_settings.data_dir / "games/culture-search-index.json"),
    )
    turn_preparer = TurnPreparer(
        memory,
        culture=lol_culture,
        culture_learner=culture_learner,
        intent=intent_reference,
        environment=lambda: context_projector.environment_packet(environment_provider.capture()),
        screen=lambda: current_screen_reference(application.events.state),
        game=lambda: application.lol_companion.reference() if application.lol_companion else "",
    )
    front_voice_factory = voice_factory
    if front_voice_factory is None and app_settings.qwen_api_key:
        realtime_factory = create_voice_factory(
            app_settings, environment_provider=environment_provider,
            context_projector=context_projector,
            context_recorder=context_recorder, host_turn_control=True)
        base_instructions = str(realtime_factory.keywords["instructions"])

        def persona_voice_factory(
            handle_tool: VoiceTool, emit: VoiceEventSink, /,
            **options: Unpack[VoiceFactoryOptions],
        ) -> Voice:
            return realtime_factory(
                handle_tool, emit,
                instructions=append_persona(base_instructions, current_persona()), **options)

        front_voice_factory = persona_voice_factory

    def task_executor(task_id: str, /, **context: Unpack[ExecutorOptions]) -> TaskExecutor:
        capability = context.get("capability", "desktop")
        return create_task_executor(
            task_id,
            provider=task_provider,
            model=task_model,
            auto_approve=auto_approve,
            session_factory=(
                wechat_runtime_host if capability == "wechat" else runtime_host
            ).create_session,
            request_builder=RuntimeRequestBuilder(
                persona=current_persona(), context_assembler=context_projector),
            app_settings=app_settings, registry=registry,
            environment_provider=environment_provider,
            memory=memory,
            conversation_service=conversation_service, session_events=session_events,
            application_tools=application.wechat_tools, **context)

    application = BoxAgentApplication(
        publish,
        task_executor,
        front_voice_factory,
        memory=memory,
        turn_preparer=turn_preparer if voice_factory is None else None,
        web_research=web_research,
        lol_knowledge=DataDragonKnowledge(
            app_settings.data_dir / "games" / "data-dragon",
            version=read_optional_setting("BOXAGENT_LOL_DATA_VERSION"),
            locale=read_optional_setting("BOXAGENT_LOL_DATA_LOCALE") or "zh_CN",
        ),
        lol_culture=lol_culture,
        conversation_service=conversation_service,
        notification_outbox=notification_outbox,
        skill_service=skill_service,
        skill_authoring=skill_authoring,
        task_trace_reader=task_trace_reader,
        context_ledger=context_ledger,
        context_observer=context_recorder,
        context_history=QwenTraceHistory(
            app_settings.log_dir / "runtime/qwen/events.jsonl",
            lambda store, clock: QwenContextRecorder(
                store, model=app_settings.voice_model, clock=clock
            ),
            memory_log_path=memory_log_path,
        ),
        runtime_resources=(
            runtime_host,
            wechat_runtime_host,
            *session_resources,
            *((intent_reference,) if intent_reference else ()),
            turn_preparer,
            checkpoint_coordinator,
        ),
        voice_history_builder=partial(
            context_projector.restore_context,
            character_budget=app_settings.qwen_history_character_budget,
        ),
    )
    game_enabled = sys.platform == "darwin" and app_settings.game_companion_enabled
    game_entry_inbox = None
    if app_settings.wechat_enabled or game_enabled:
        from boxagent.bootstrap.external_events import configure_external_events

        event_host, game_entry_inbox = configure_external_events(
            application,
            app_settings,
            builder=RuntimeRequestBuilder(
                persona=current_persona(), context_assembler=context_projector
            ),
            profile=selected.profile,
            environment=environment_provider,
            game_enabled=game_enabled,
        )
        application.runtime_resources = (*application.runtime_resources, event_host)
    from boxagent.application.camera import CameraInteractionService

    if sys.platform == "darwin":
        from boxagent.application.game_news import GameNewsMonitor
        from boxagent.infrastructure.games.steam import SteamGames, steam_game_topic

        steam = SteamGames(Path.home() / "Library/Application Support/Steam/steamapps")
        application.game_news = GameNewsMonitor(steam.running, steam_game_topic,
                                                application.announce_game_news)
        if game_enabled and game_entry_inbox is not None:
            from boxagent.bootstrap.game_companion import install_game_companion
            install_game_companion(application, app_settings, game_entry_inbox)
    application.interaction_service.companion_name = lambda: current_persona().name
    application.camera = CameraInteractionService(
        application.events, application.interaction_service
    )
    observer = WindowSummary(
        application.emit, context_interval or 15,
        model=app_settings.perception_model,
        max_size=context_size,
        workspace=app_settings.root,
        python=app_settings.perception_python,
        worker_script=app_settings.root / "scripts/window_summary_worker.py",
        log_path=app_settings.log_dir / "context-worker.log",
        window_allowed=lambda window: (
            not application.state.lol_companion_enabled
            or window["app"].casefold() == "league of legends"),
    )
    from boxagent.bootstrap.memory import configure_visual_memory

    application.observer = observer
    configure_visual_memory(memory, observer,
                            app_settings=app_settings, conversation_store=conversation_store)

    def announce_lol(moment: LolMoment, still_current: Callable[[], bool],
                     playback_started: Callable[[], None]) -> bool:
        def present() -> None:
            playback_started()
            application.emit("companion.comment", companion_comment=moment.line)

        return application.interaction_service.schedule_lol(moment.line, still_current, present)

    def publish_lol(status: str, text: str) -> None:
        if status == "active" and not application.interaction_service.is_connected:
            status = "voice_waiting"
            text = (
                "对局数据正常；语音正在恢复连接"
                if application.state.voice == "connecting"
                else "对局数据正常；请开启语音以接收陪玩提醒"
            )
        application.events.emit(
            "lol.companion",
            lol_companion_enabled=status != "off",
            lol_companion_status=status,
            lol_companion_text=text,
        )

    lol_client = LolLiveClient()
    application.lol_companion = LolCompanion(
        lol_client.read, LolCompanionPolicy(), announce_lol, publish_lol)
    return application


def create_engine_server(
    socket_path: str | Path, *, app_settings: Settings, task_provider: str,
    task_model: str | None, auto_approve: bool, context_interval: float, context_size: int,
) -> EngineServer:
    """Assemble the socket server that owns one Engine application."""
    from boxagent.interfaces.engine.server import EngineServer

    return EngineServer(
        socket_path,
        lambda publish: create_application(
            publish,
            task_provider=task_provider,
            task_model=task_model,
            auto_approve=auto_approve,
            app_settings=app_settings,
            context_interval=context_interval,
            context_size=context_size,
        ),
        start_context=context_interval > 0,
    )
