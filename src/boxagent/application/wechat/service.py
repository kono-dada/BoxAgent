"""Resolve stable identities and coordinate one idempotent send attempt."""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import replace
from typing import Protocol

from boxagent.domain.wechat.contracts import (
    MessageHistory,
    OperationRejected,
    PeerResolutionError,
    SendRequests,
    WeChatReader,
    WeChatTransport,
)
from boxagent.domain.wechat.models import Contact, GroupMember, SendOutcome

logger = logging.getLogger(__name__)


class TransportFactory(Protocol):
    def __call__(self) -> WeChatTransport: ...


class WeChatService:
    def __init__(self, reader: WeChatReader, history: MessageHistory, transport: TransportFactory,
                 requests: SendRequests) -> None:
        self.reader = reader
        self.history = history
        self.transport = transport
        self.requests = requests
        self._lock = threading.RLock()

    def resolve(self, query: str) -> Contact:
        if not query.strip():
            raise ValueError("Recipient is empty")
        candidates = self.reader.contacts(query, limit=10000)
        exact_id = [c for c in candidates if c.peer == query]
        if len(exact_id) == 1:
            return exact_id[0]
        exact = [
            c
            for c in candidates
            if query.casefold() in (c.nickname.casefold(), c.remark.casefold())
        ]
        choices = exact or list(candidates)
        if len(choices) != 1:
            raise PeerResolutionError(query, tuple(choices))
        return choices[0]

    def resolve_member(self, group: str, query: str) -> GroupMember:
        members = self.reader.members(group)
        contacts = {c.peer: c for c in self.reader.contacts(limit=10000)}
        selected = [m for m in members if m.peer == query]
        if not selected:
            selected = [
                m
                for m in members
                if query.casefold()
                in {
                    m.display_name.casefold(),
                    contacts[m.peer].remark.casefold() if m.peer in contacts else "",
                }
            ]
        if len(selected) != 1:
            raise PeerResolutionError(
                query, tuple(Contact(m.peer, m.display_name) for m in selected)
            )
        return selected[0]

    def send(self, recipient: str, text: str, *, request_id: str, dry_run: bool = False,
             mention: str | None = None) -> SendOutcome:
        if not request_id.strip() or len(request_id) > 256:
            raise ValueError("A stable nonempty request_id (up to 256 characters) is required")
        if not text.strip() or "\0" in text or len(text.encode("utf-16-le")) > 8192:
            raise ValueError("Invalid message text or expression budget exceeded")
        with self._lock:
            target = self.resolve(recipient)
            member = self.resolve_member(target.peer, mention) if mention is not None else None
            fingerprint = hashlib.sha256(json.dumps([
                self.reader.account_id, target.peer, text, dry_run,
                (member.peer, member.display_name) if member else None,
            ], ensure_ascii=False).encode()).hexdigest()
            claimed, previous = self.requests.claim(request_id, fingerprint)
            if not claimed:
                return previous or SendOutcome(
                    target.peer, text, request_id=request_id, error_code="outcome_unknown"
                )
            attempted = False
            outcome = SendOutcome(target.peer, text, request_id=request_id)
            try:
                cursor = self.history.delivery_cursor(target.peer) if not dry_run else None
                transport = self.transport()
                attempted = True
                outcome = (
                    transport.send_mention(target.peer, member, text, dry_run=dry_run)
                    if member
                    else transport.send(target.peer, text, dry_run=dry_run)
                )
                outcome = replace(outcome, request_id=request_id)
                if cursor is not None and outcome.submitted:
                    try:
                        check = self.history.find_delivery(target.peer, text, after=cursor,
                                                           mention=member.peer if member else None)
                        if check.matched:
                            outcome = replace(
                                outcome, local_id=check.local_id, server_id=check.server_id
                            )
                    except Exception:
                        logger.exception(
                            "WeChat receipt lookup failed after submission; do not resend"
                        )
                        outcome = replace(outcome, error_code="receipt_unavailable")
            except OperationRejected as error:
                logger.info("WeChat send rejected: %s", type(error).__name__)
                outcome = replace(outcome, error_code=type(error).__name__)
            except Exception:
                logger.exception("WeChat native send failed; keeping durable request identity")
                outcome = replace(
                    outcome, error_code="outcome_unknown" if attempted else "preflight_failed"
                )
            self.requests.finish(request_id, outcome)
            return outcome
