"""Controlled replay of the original FIX-03 multi-turn failure conditions."""

import asyncio
import json
import tempfile
import unittest
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from boxagent.application.assistant import BoxAgentApplication
from boxagent.application.memory import DisabledMemoryModule
from boxagent.application.turn_preparation import TurnPreparer
from boxagent.core.states import Approval
from boxagent.domain.conversation import ConversationService
from boxagent.domain.notification import NotificationOutbox
from boxagent.infrastructure.persistence import JsonlSessionStore, JsonNotificationRepository
from boxagent.infrastructure.runtimes.qwen.realtime import QwenRealtimeSession


@dataclass(frozen=True)
class ReplayTurn:
    query: str
    reply: str


def load_turns() -> tuple[ReplayTurn, ...]:
    data = json.loads((Path(__file__).parent / "fixtures/voice_task_sequence.json").read_text())
    rows = data["turns"]
    if not isinstance(rows, list) or len(rows) != 6:
        raise ValueError("FIX-03 requires the six recorded turns")
    turns = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("query"), str) or not isinstance(row.get("reply"), str):
            raise ValueError("Replay turns must contain text query and reply")
        turns.append(ReplayTurn(row["query"], row["reply"]))
    return tuple(turns)


async def wait_until(predicate: Callable[[], bool]) -> None:
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(.001)


class VoiceTaskSequenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_second_task_survives_six_turns_and_old_notification(self) -> None:
        for delayed_ack in (False, True):
            for completion in ("provider", "audio_done"):
                with self.subTest(delayed_ack=delayed_ack, completion=completion):
                    await self.replay(delayed_ack, completion)

    async def replay(self, delayed_ack: bool, completion: str) -> None:
        turns = load_turns()
        releases = [asyncio.Event(), asyncio.Event()]
        started = [asyncio.Event(), asyncio.Event()]
        preparation_entered = asyncio.Event()
        preparation_release = asyncio.Event()
        goals: list[tuple[str, str, str]] = []
        trace: list[tuple[str, dict[str, Any]]] = []

        class Executor:
            def __init__(self, task_id: str, **context: Any) -> None:
                self.task_id = task_id
                self.interaction_id = str(context["interaction_id"])

            async def run(self, goal: str, progress: Callable[[str], None],
                          approve: Approval) -> dict[str, Any]:
                index = len(goals)
                goals.append((goal, self.interaction_id, self.task_id))
                started[index].set()
                await releases[index].wait()
                return {"outcome": "blocked", "summary": "测试执行器未操作电脑，需要澄清。"}

            async def cancel(self) -> None:
                for release in releases:
                    release.set()

        class Memory(DisabledMemoryModule):
            async def context_packet(self, query: str, *, session_id: str,
                                     top_k: int) -> str:
                if query == turns[3].query:
                    preparation_entered.set()
                    await preparation_release.wait()
                return "仅服务当前回合的测试参考"

        class Intent:
            async def estimate(self, query: str, *, context: str = "") -> dict[str, Any]:
                action = query in (turns[0].query, turns[3].query)
                return {"p_action": .98 if action else .01,
                        "p_status": .0, "p_conversation": .01 if action else .99}

            async def review_delegation(self, query: str, reply: str, *, context: str = "") -> dict[str, Any]:
                return {"p_delegation": .99 if query == turns[3].query else .01}

        def record_trace(event: str, **data: Any) -> None:
            trace.append((event, data))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory = Memory()
            preparer = TurnPreparer(memory, intent=Intent(), budget=2)
            app = BoxAgentApplication(
                lambda _: None, Executor, None, memory=memory,
                conversation_service=ConversationService(JsonlSessionStore(root)),
                notification_outbox=NotificationOutbox(JsonNotificationRepository(root / "notifications")),
                turn_preparer=preparer, runtime_resources=(preparer,))
            interaction = app.interaction_service
            voice = QwenRealtimeSession(
                interaction._handle_tool, app.events.emit, host_turn_control=True,
                conversation_event=interaction._conversation_event,
                notification_event=interaction._notification_event, trace=record_trace,
                delegation_review=interaction._review_delegation)

            class Socket:
                def __init__(self) -> None:
                    self.sent: list[dict[str, Any]] = []
                    self.response_count = 0
                    self.speed_requested = asyncio.Event()

                async def send(self, raw: str) -> None:
                    row = json.loads(raw)
                    self.sent.append(row)
                    if row["type"] == "response.create":
                        self.response_count += 1
                        if self.response_count == 2:
                            self.speed_requested.set()
                            if delayed_ack:
                                return
                        await voice.receive({"type": "response.created", "response": {
                            "id": f"reply-{self.response_count}"}})
                    elif row["type"] == "conversation.item.delete":
                        await voice.receive({"type": "conversation.item.deleted", "item_id": row["item_id"]})
                    elif row["type"] == "conversation.item.create" and row["item"].get("role") == "system":
                        await voice.receive({"type": "conversation.item.created", "item": {
                            **row["item"], "id": f"reference-{len(self.sent)}"}})

                async def close(self) -> None:
                    pass

            socket = Socket()
            voice.ws = socket
            class Audio:
                def clear(self) -> None:
                    pass

            voice.audio = Audio()
            voice.ready.set()
            # A live lifecycle handle lets the real application enqueue its notifications.
            connected = asyncio.create_task(asyncio.Event().wait())
            interaction.voice_factory = lambda *_args, **_kwargs: voice
            interaction.voice = voice
            interaction.task = connected
            workers = [asyncio.create_task(voice.deliver_notifications())]
            query_ids: list[str] = []

            async def begin(index: int) -> asyncio.Task[None]:
                await voice.receive({"type": "input_audio_buffer.speech_started"})
                await voice.receive({"type": "input_audio_buffer.speech_stopped"})
                await voice.receive({"type": "conversation.item.input_audio_transcription.completed",
                                     "item_id": f"asr-{index}", "transcript": turns[index].query})
                await wait_until(lambda: interaction.interaction is not None
                                 and interaction.interaction.user_event.content == turns[index].query)
                query_ids.append(interaction.interaction.interaction_id)
                return voice.preparation_task

            async def finish(index: int, response_id: str) -> None:
                await voice.receive({"type": "response.audio_transcript.done",
                                     "response_id": response_id, "transcript": turns[index].reply})
                if completion == "provider":
                    await voice.receive({"type": "response.done", "response": {"id": response_id}})
                else:
                    await voice.receive({"type": "response.audio.done", "response_id": response_id})
                    await wait_until(lambda: response_id in voice.finished_responses)
                await asyncio.gather(*tuple(voice.deliveries))

            try:
                await app.start()
                await (await begin(0))
                await voice.receive({"type": "response.function_call_arguments.done",
                                     "response_id": "reply-1", "call_id": "calculator-call",
                                     "name": "run_task", "arguments": "{}"})
                await asyncio.wait_for(started[0].wait(), 2)
                await finish(0, "reply-1")
                old_job = app.job

                speed = await begin(1)
                await asyncio.wait_for(socket.speed_requested.wait(), 2)
                model = await begin(2)
                await asyncio.gather(speed, return_exceptions=True)
                if delayed_ack:
                    self.assertEqual(socket.response_count, 2)
                    await voice.receive({"type": "response.created", "response": {"id": "reply-2"}})
                await model
                # A late speed tool call must not bind to the model discussion.
                await voice.receive({"type": "response.function_call_arguments.done",
                                     "response_id": "reply-2", "call_id": "stale-speed-call",
                                     "name": "run_task", "arguments": "{}"})
                await voice.receive({"type": "response.done", "response": {"id": "reply-2"}})
                self.assertTrue(voice.input_turn_pending)
                self.assertEqual(voice.response_context["reply-3"]["query_id"], query_ids[2])
                await finish(2, "reply-3")

                science = await begin(3)
                await asyncio.wait_for(preparation_entered.wait(), 2)
                releases[0].set()
                await asyncio.wait_for(old_job, 2)
                await wait_until(lambda: bool(voice.pending_notifications))
                # Deliver the old call result after the new input is durable.
                workers.append(asyncio.create_task(voice.deliver_results()))
                await wait_until(lambda: any(event == "tool.stale_followup_suppressed" for event, _ in trace))
                self.assertEqual(socket.response_count, 3)
                self.assertFalse(any("[可信任务结果]" in json.dumps(row, ensure_ascii=False) for row in socket.sent))
                preparation_release.set()
                await science
                self.assertEqual(voice.response_context["reply-4"]["query_id"], query_ids[3])
                # Exact reported promise: no provider run_task; the host must repair once.
                await finish(3, "reply-4")
                await asyncio.wait_for(started[1].wait(), 2)
                await voice.receive({"type": "response.function_call_arguments.done",
                                     "response_id": "reply-4", "call_id": "late-science-call",
                                     "name": "run_task", "arguments": "{}"})
                science_job = app.job
                for index in (4, 5):
                    await (await begin(index))
                    await finish(index, f"reply-{index + 1}")
                self.assertEqual([(goal, query_id) for goal, query_id, _ in goals],
                                 [(turns[index].query, query_ids[index]) for index in (0, 3)])
                self.assertEqual(len(app.execution_service.pending), 0)
                self.assertIs(app.job, science_job)
                self.assertFalse(science_job.done())
                await wait_until(lambda: bool(voice.notification_responses))
                notification_id = next(iter(voice.notification_responses))
                self.assertEqual(voice.response_context[notification_id]["kind"], "notification")
                await voice.receive({"type": "response.audio_transcript.done", "response_id": notification_id,
                                     "transcript": "旧任务需要澄清，这次没有操作电脑。"})
                await voice.receive({"type": "response.done", "response": {"id": notification_id}})

                active = await app.conversation_service.active_session()
                events = await app.conversation_service.events(active.session_id)
                self.assertEqual([event.content for event in events if event.role == "user"],
                                 [turn.query for turn in turns])
                for index in (2, 4, 5):
                    terminal = [event for event in events if event.type == "interaction.finalized"
                                and event.interaction_id == query_ids[index]]
                    self.assertEqual([event.status for event in terminal], ["succeeded"])
                    self.assertTrue(all(event.task_id is None for event in terminal))
                self.assertFalse(any(event.type == "interaction.finalized" and event.interaction_id == query_ids[3]
                                     for event in events))
                notices = await app.list_notifications(pending_only=False)
                self.assertEqual([notice["interaction_id"] for notice in notices], [query_ids[0]])
                self.assertEqual([notice["task_id"] for notice in notices], [goals[0][2]])
                self.assertEqual(len(goals), 2)
            finally:
                preparation_release.set()
                for release in releases:
                    release.set()
                for worker in workers:
                    worker.cancel()
                await asyncio.gather(*workers, return_exceptions=True)
                connected.cancel()
                await asyncio.gather(connected, return_exceptions=True)
                interaction.task = None
                await app.close()
