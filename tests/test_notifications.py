"""Persistent completion notification and delivery state contracts."""

import asyncio
import tempfile
import unittest
from pathlib import Path

from boxagent.infrastructure.persistence import JsonNotificationRepository
from boxagent.infrastructure.persistence import JsonlSessionStore
from boxagent.application.assistant import BoxAgentApplication
from boxagent.domain.conversation import ConversationService
from boxagent.domain.notification import NotificationOutbox


class NotificationOutboxTests(unittest.IsolatedAsyncioTestCase):
    async def test_enqueue_is_idempotent_and_ack_persists(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notifications/outbox.json"
            outbox = NotificationOutbox(JsonNotificationRepository(path))
            await outbox.start()
            first = await outbox.enqueue(
                session_id="ses_1", interaction_id="int_1", task_id="task_1",
                outcome="succeeded", summary="音乐已经开始播放")
            duplicate = await outbox.enqueue(
                session_id="ses_1", interaction_id="int_1", task_id="task_1",
                outcome="succeeded", summary="重复结果")
            self.assertEqual(first.notification_id, duplicate.notification_id)
            self.assertEqual(len(await outbox.pending()), 1)

            await outbox.claim(first.notification_id, "voice")
            await outbox.acknowledge(
                first.notification_id, "voice", "playback_started")
            reopened = NotificationOutbox(JsonNotificationRepository(path))
            await reopened.start()

            self.assertEqual(await reopened.pending(), [])
            stored = reopened.repository.list()[0]
            self.assertEqual(stored.status, "delivered")
            self.assertEqual(stored.attempts[-1]["receipt"], "playback_started")

    async def test_restart_returns_interrupted_delivery_to_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notifications/outbox.json"
            outbox = NotificationOutbox(JsonNotificationRepository(path))
            await outbox.start()
            item = await outbox.enqueue(
                session_id="ses_1", interaction_id="int_1", task_id="task_1",
                outcome="failed", summary="任务失败")
            await outbox.claim(item.notification_id, "voice")

            reopened = NotificationOutbox(JsonNotificationRepository(path))
            await reopened.start()
            pending = await reopened.pending()

            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0].status, "pending")


class ForegroundAndNotificationFlowTests(unittest.IsolatedAsyncioTestCase):
    class Executor:
        instances = []

        def __init__(self, task_id, **context):
            self.task_id = task_id
            self.context = context
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.__class__.instances.append(self)

        async def run(self, goal, progress, approve):
            self.goal = goal
            self.started.set()
            await self.release.wait()
            return {"outcome": "completed", "summary": "音乐已经开始播放",
                    "evidence": []}

        async def cancel(self):
            self.release.set()

    class FrontRuntime:
        def __init__(self, handle_tool, emit, **kwargs):
            self.handle_tool = handle_tool
            self.emit = emit
            self.conversation_event = kwargs["conversation_event"]
            self.notification_event = kwargs["notification_event"]
            self.microphone = kwargs["microphone"]
            self.ready = asyncio.Event()
            self.stopped = asyncio.Event()
            self.submitted = []
            self.notifications = []
            self.response_index = 0

        async def run(self):
            self.ready.set()
            await self.stopped.wait()

        async def stop(self):
            self.stopped.set()

        async def submit_text(self, text):
            self.submitted.append(text)
            self.response_index += 1
            response_id = f"front-{self.response_index}"
            await self.conversation_event(
                "response_created", {"response_id": response_id, "origin": "user"})
            if text == "打开音乐":
                await self.conversation_event("assistant_final", {
                    "response_id": response_id, "transcript": "好，我去播放，你可以继续聊。"})
                await self.conversation_event("response_done", {
                    "response_id": response_id, "cancelled": False,
                    "has_tool_calls": True})
                self.task_acceptance = await self.handle_tool(
                    "run_task", {"goal": text},
                    {"response_id": response_id, "call_id": "call-1"})
            else:
                await self.conversation_event("assistant_final", {
                    "response_id": response_id, "transcript": "当然，后台任务不会阻塞我们聊天。"})
                await self.conversation_event("response_done", {
                    "response_id": response_id, "cancelled": False,
                    "has_tool_calls": False})

        async def notify_task_result(self, item):
            self.notifications.append(item)

    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.events = []
        self.Executor.instances = []
        self.front = None

        def create_front(*args, **kwargs):
            self.front = self.FrontRuntime(*args, **kwargs)
            return self.front

        conversation = ConversationService(JsonlSessionStore(root / "conversations"))
        outbox = NotificationOutbox(
            JsonNotificationRepository(root / "notifications/outbox.json"))
        self.application = BoxAgentApplication(
            self.events.append, self.Executor, create_front,
            conversation_service=conversation, notification_outbox=outbox)
        await self.application.start()

    async def asyncTearDown(self):
        await self.application.close()
        self.temporary.cleanup()

    async def test_plain_text_uses_front_runtime_without_starting_codex(self):
        accepted = await self.application.submit_text("今天聊点轻松的")

        self.assertEqual(accepted["status"], "accepted")
        self.assertFalse(self.front.microphone)
        self.assertEqual(self.front.submitted, ["今天聊点轻松的"])
        self.assertEqual(self.Executor.instances, [])
        session = await self.application.conversation_service.active_session()
        messages = [event["content"] for event in await self.application.session_events(
            session.session_id) if event["type"] == "message.final"]
        self.assertEqual(messages, ["今天聊点轻松的",
                                    "当然，后台任务不会阻塞我们聊天。"])

    async def test_task_can_finish_after_another_chat_and_waits_for_playback_receipt(self):
        first = await self.application.submit_text("打开音乐")
        executor = self.Executor.instances[0]
        await executor.started.wait()

        second = await self.application.submit_text("任务执行时我还能继续聊吗")
        self.assertEqual(first["status"], "accepted")
        self.assertEqual(self.front.task_acceptance["status"], "accepted")
        self.assertEqual(second["status"], "accepted")
        self.assertFalse(self.application.job.done())

        executor.release.set()
        result = await self.application.job
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(len(self.front.notifications), 1)
        pending = await self.application.list_notifications()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["status"], "delivering")

        await self.front.notification_event("playback_started", {
            "notification_id": pending[0]["notification_id"]})
        self.assertEqual(await self.application.list_notifications(), [])
        stored = await self.application.list_notifications(pending_only=False)
        self.assertEqual(stored[0]["status"], "delivered")
        self.assertEqual(stored[0]["attempts"][-1]["receipt"], "playback_started")

        session = await self.application.conversation_service.active_session()
        messages = [event["content"] for event in await self.application.session_events(
            session.session_id) if event["type"] == "message.final"]
        self.assertEqual(messages, [
            "打开音乐", "好，我去播放，你可以继续聊。",
            "任务执行时我还能继续聊吗",
            "当然，后台任务不会阻塞我们聊天。",
            "音乐已经开始播放",
        ])


if __name__ == "__main__":
    unittest.main()
