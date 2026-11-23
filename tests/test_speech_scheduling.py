"""Exercise real scheduling with a deterministic Qwen socket and device callbacks."""

import asyncio
import json
import unittest
from unittest.mock import Mock

from boxagent.infrastructure.runtimes.qwen.realtime import QwenRealtimeSession


class ProviderSocket:
    def __init__(self, voice: QwenRealtimeSession) -> None:
        self.voice = voice
        self.requests: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self.sent: list[str] = []

    async def send(self, raw: str) -> None:
        request = json.loads(raw)
        kind = request["type"]
        self.sent.append(kind)
        if kind == "conversation.item.create":
            await self.voice.receive({"type": "conversation.item.created", "item": {
                **request["item"], "id": f"item-{len(self.sent)}"}})
        elif kind == "conversation.item.delete":
            await self.voice.receive(
                {"type": "conversation.item.deleted", "item_id": request["item_id"]}
            )
        elif kind == "response.create":
            origin = self.voice.response_origins[-1]
            response_id = f"response-{len(self.sent)}"
            await self.voice.receive({"type": "response.created", "response": {"id": response_id}})
            self.requests.put_nowait((origin["kind"], response_id))


class SpeechSchedulingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.receipts = Mock()
        self.voice = QwenRealtimeSession(
            None, Mock(), host_turn_control=True, notification_event=self.receipts
        )
        self.socket = ProviderSocket(self.voice)
        self.voice.ws = self.socket
        self.worker = asyncio.create_task(self.voice.deliver_notifications())

    async def asyncTearDown(self) -> None:
        children = [self.worker, *self.voice.deliveries]
        for child in children:
            child.cancel()
        await asyncio.gather(*children, return_exceptions=True)

    async def next_response(self, expected: str) -> str:
        origin, response_id = await asyncio.wait_for(self.socket.requests.get(), 1)
        self.assertEqual(origin, expected)
        return response_id

    async def finish(self, response_id: str) -> None:
        await self.voice.receive({"type": "response.done", "response": {"id": response_id}})
        self.voice.playback_changed(False)

    async def notice(self, identifier: str = "wechat-1") -> None:
        await self.voice.notify_task_result(
            {"notification_id": identifier, "kind": "external_event", "summary": "小林说七点见"}
        )

    async def start_audio(self, response_id: str) -> None:
        await self.voice.receive(
            {"type": "response.audio.delta", "response_id": response_id, "delta": "AQI="}
        )
        self.voice.playback_changed(True)

    async def respond_to_user(self) -> None:
        await self.voice.receive({"type": "input_audio_buffer.speech_stopped"})
        self.voice.last_user_activity = 0.0  # Advance past the existing one-second speech guard.
        self.voice.begin_query("user-question")
        await self.voice.respond_to_query("", query_id="user-question")
        await self.finish(await self.next_response("user"))

    async def test_lol_takes_first_idle_slot_and_waiting_wechat_takes_next(self) -> None:
        await self.notice()
        starts = Mock()
        self.assertTrue(self.voice.schedule_lol("漂亮，刚刚那个人头拿下了。",
                                               still_current=lambda: True, playback_started=starts))
        first = await self.next_response("lol_companion")
        starts.assert_not_called()
        await self.start_audio(first)
        starts.assert_called_once_with()

        latest = Mock()
        for _ in range(5):
            self.assertTrue(
                self.voice.schedule_lol(
                    "这波打得漂亮。", still_current=lambda: True, playback_started=latest
                )
            )
        await self.finish(first)
        incoming = await self.next_response("notification")
        await self.start_audio(incoming)
        self.receipts.assert_called_once_with("playback_started", {"notification_id": "wechat-1"})
        await self.finish(incoming)
        await self.start_audio(await self.next_response("lol_companion"))
        latest.assert_called_once_with()

    async def test_user_goes_first_then_wechat_and_expired_lol_is_dropped(self) -> None:
        await self.voice.receive({"type": "input_audio_buffer.speech_started"})
        await self.notice()
        current = Mock(return_value=True)
        starts = Mock()
        self.voice.schedule_lol("漂亮！", still_current=current, playback_started=starts)
        await asyncio.sleep(0)
        self.assertEqual(self.socket.sent, [])
        current.return_value = False
        await self.respond_to_user()
        incoming = await self.next_response("notification")
        await self.start_audio(incoming)
        await self.finish(incoming)
        await asyncio.sleep(0)
        starts.assert_not_called()
        self.assertIsNone(self.voice.pending_lol)
        self.assertTrue(self.socket.requests.empty())

    async def test_lol_expires_between_request_and_playback(self) -> None:
        current = Mock(return_value=True)
        starts = Mock()
        self.voice.schedule_lol("漂亮！", still_current=current, playback_started=starts)
        requested = await self.next_response("lol_companion")
        current.return_value = False
        await self.voice.receive(
            {"type": "response.audio.delta", "response_id": requested, "delta": "AQI="}
        )
        await asyncio.sleep(0)
        self.assertIn("response.cancel", self.socket.sent)
        self.assertIn(requested, self.voice.cancelled_responses)
        starts.assert_not_called()

    async def test_user_interrupt_before_start_retries_wechat_after_answer(self) -> None:
        await self.notice()
        incoming = await self.next_response("notification")
        await self.voice.receive({"type": "input_audio_buffer.speech_started"})
        # The cancellation acknowledgement can lag behind the whole user turn.
        await self.respond_to_user()
        await self.finish(incoming)
        self.receipts.assert_not_called()
        repeated = await self.next_response("notification")
        await self.start_audio(repeated)
        self.receipts.assert_called_once_with("playback_started", {"notification_id": "wechat-1"})

    async def test_user_interrupt_after_start_does_not_repeat_wechat(self) -> None:
        await self.notice()
        incoming = await self.next_response("notification")
        await self.start_audio(incoming)
        await self.voice.receive({"type": "input_audio_buffer.speech_started"})
        await self.finish(incoming)
        await self.respond_to_user()
        await asyncio.sleep(0)
        self.assertIn("response.cancel", self.socket.sent)
        self.assertTrue(self.socket.requests.empty())
        self.receipts.assert_called_once_with("playback_started", {"notification_id": "wechat-1"})
