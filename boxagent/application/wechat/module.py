"""One application-facing surface for CLI, tools and future Engine wiring."""
from __future__ import annotations
import asyncio
from collections.abc import Callable

from boxagent.application.wechat.monitor import WeChatMonitor
from boxagent.application.wechat.service import WeChatService
from boxagent.domain.wechat.contracts import HealthProvider, MomentsPublisher, OperationRejected
from boxagent.domain.wechat.models import Capability, Contact, Conversation, GroupMember, Message, PublishOutcome, SendOutcome, WeChatHealth


class WeChatModule:
    def __init__(self, service: WeChatService, monitor: WeChatMonitor,
                 moments: MomentsPublisher, health: HealthProvider) -> None:
        self.service = service
        self.monitor = monitor
        self.moments = moments
        self._health = health

    def contacts(self, query: str = "", *, limit: int = 100) -> tuple[Contact, ...]:
        return self.service.reader.contacts(query, limit=limit)

    def sessions(self, *, unread_only: bool = False, limit: int = 20) -> tuple[Conversation, ...]:
        return self.service.reader.sessions(unread_only=unread_only, limit=limit)

    def history(self, recipient: str, *, limit: int = 50) -> tuple[Message, ...]:
        return self.service.reader.history(self.service.resolve(recipient).peer, limit=limit)

    def search(self, query: str, *, recipient: str | None = None, limit: int = 50) -> tuple[Message, ...]:
        peer = self.service.resolve(recipient).peer if recipient else None
        return self.service.reader.search(query, peer=peer, limit=limit)

    def members(self, group: str) -> tuple[GroupMember, ...]:
        return self.service.reader.members(self.service.resolve(group).peer)

    def send(self, recipient: str, text: str, *, request_id: str, dry_run: bool = False,
             mention: str | None = None) -> SendOutcome:
        return self.service.send(recipient, text, request_id=request_id, dry_run=dry_run, mention=mention)

    def clear_draft(self, recipient: str) -> None:
        self.service.transport().clear_draft(self.service.resolve(recipient).peer)

    def publish(self, text: str, visibility: str, *, request_id: str, dry_run: bool = True) -> PublishOutcome:
        return self.moments.publish(text, visibility, request_id=request_id, dry_run=dry_run)

    def health(self) -> WeChatHealth:
        return self._health.health()

    async def start(self) -> None:
        await self.monitor.start()

    def status(self) -> dict[str, object]:
        return {"running": self.monitor.running, "error": self.monitor.last_error}

    async def close(self) -> None:
        await self.monitor.close()


class DeferredWeChatModule:
    """Missing/revoked account access degrades WeChat without preventing Engine startup."""
    def __init__(self, factory: Callable[[], WeChatModule]) -> None:
        self.factory = factory
        self.module: WeChatModule | None = None

    def _ready(self) -> WeChatModule:
        if self.module is None:
            raise OperationRejected('wechat_setup_required')
        return self.module

    def contacts(self, query: str = '', *, limit: int = 100) -> tuple[Contact, ...]:
        return self._ready().contacts(query, limit=limit)

    def sessions(self, *, unread_only: bool = False, limit: int = 20) -> tuple[Conversation, ...]:
        return self._ready().sessions(unread_only=unread_only, limit=limit)

    def history(self, recipient: str, *, limit: int = 50) -> tuple[Message, ...]:
        return self._ready().history(recipient, limit=limit)

    def search(self, query: str, *, recipient: str | None = None, limit: int = 50) -> tuple[Message, ...]:
        return self._ready().search(query, recipient=recipient, limit=limit)

    def members(self, group: str) -> tuple[GroupMember, ...]:
        return self._ready().members(group)

    def send(self, recipient: str, text: str, *, request_id: str, dry_run: bool = False,
             mention: str | None = None) -> SendOutcome:
        return self._ready().send(recipient, text, request_id=request_id, dry_run=dry_run, mention=mention)

    def publish(self, text: str, visibility: str, *, request_id: str, dry_run: bool = True) -> PublishOutcome:
        return self._ready().publish(text, visibility, request_id=request_id, dry_run=dry_run)

    def health(self) -> WeChatHealth:
        if self.module is not None:
            return self.module.health()
        unavailable = Capability('not_configured', 'wechat_setup_required')
        return WeChatHealth('', unavailable, unavailable, unavailable, unavailable)

    async def start(self) -> None:
        if self.module is None:
            self.module = await asyncio.to_thread(self.factory)
        await self.module.start()

    def status(self) -> dict[str, object]:
        return self.module.status() if self.module is not None else {'running': False, 'error': 'wechat_setup_required'}

    async def close(self) -> None:
        if self.module is not None:
            await self.module.close()
