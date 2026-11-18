"""Assemble connector -> Inbox -> isolated Agent -> existing notification outbox."""
from __future__ import annotations

from functools import partial

from boxagent.agent.harness.request_builder import RuntimeRequestBuilder
from boxagent.agent.runtime.models import ModelProfile
from boxagent.application.assistant import BoxAgentApplication
from boxagent.application.event_context import ExternalEventContext
from boxagent.application.event_runs import DirectEventNotice, EventRuns, ExternalEventIntegration
from boxagent.domain.events.models import EventBatch, EventDecision
from boxagent.application.wechat.event_context import WeChatEventContext
from boxagent.bootstrap.settings import Settings
from boxagent.bootstrap.wechat import build_wechat_module, build_wechat_tools
from boxagent.bootstrap.wechat_settings import wechat_config
from boxagent.application.wechat.module import DeferredWeChatModule
from boxagent.infrastructure.environment import LocalSystemEnvironmentProvider
from boxagent.infrastructure.persistence.sqlite_event_inbox import SQLiteEventInbox
from boxagent.infrastructure.runtimes.codex.app_server import CodexAppServer
from boxagent.infrastructure.runtimes.codex.event_agent import CodexEventAgent
from boxagent.infrastructure.runtimes.codex.session import CodexRuntimeHost, CodexTaskSession


def configure_external_events(application: BoxAgentApplication, settings: Settings, *,
                              builder: RuntimeRequestBuilder, profile: ModelProfile,
                              environment: LocalSystemEnvironmentProvider) -> CodexRuntimeHost:
    runtime_dir = settings.data_dir / "external-events/runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    host = CodexRuntimeHost(
        output=settings.log_dir / "runtime/event-agent", workspace=runtime_dir,
        codex_home=settings.codex_home, model_profile=profile,
        permission_check=lambda *_args, **_kwargs: False,
        server_factory=partial(CodexAppServer, computer_use_enabled=False))

    async def reject_tool(name: str, arguments: dict) -> dict:
        raise RuntimeError("External event decisions cannot call tools")

    def session() -> CodexTaskSession:
        return host.create_session(on_tool_call=reject_tool, approve=lambda _: False,
            current_app=lambda: "", report=lambda *_: None, log=lambda *_args, **_kwargs: None,
            auto_approve=False)

    inbox = SQLiteEventInbox(settings.data_dir / "external-events/inbox.sqlite")
    context = ExternalEventContext(application.conversation_service, builder,
                                   application.memory, environment.capture)
    direct_notices: dict[str, DirectEventNotice] = {}
    runs = EventRuns(inbox, context, CodexEventAgent(session, model=profile.model),
                     application.publish_event_notification, direct_notices=direct_notices)
    module = DeferredWeChatModule(lambda: build_wechat_module(wechat_config(settings), event_sink=runs))
    wechat_context = WeChatEventContext(module)
    context.sources = {"wechat": wechat_context}
    if settings.wechat_notify_all:
        async def notify_all(batch: EventBatch) -> tuple[str, EventDecision]:
            return await context.session_for(batch), await wechat_context.notice(batch)
        direct_notices["wechat"] = notify_all
    application.wechat_tools = build_wechat_tools(module, allow_write=True)
    application.external_events = ExternalEventIntegration(runs, {"wechat": module})
    return host
