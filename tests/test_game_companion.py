import asyncio
import json
import unittest
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory

from boxagent.application.game_companion import GameCompanion
from boxagent.application.memory import DisabledMemoryModule
from boxagent.application.turn_preparation import TurnPreparer
from boxagent.core.events import StateEvents
from boxagent.domain.conversation import ConversationService
from boxagent.domain.interaction.service import InteractionService
from boxagent.domain.proactivity.game_entry import GameCapture
from boxagent.domain.proactivity.game_highlight import GameFrame, GameHighlight, GameObservation, HighlightReview
from boxagent.infrastructure.persistence import JsonlSessionStore
from boxagent.infrastructure.runtimes.qwen.realtime import QwenRealtimeSession


PRAISE = HighlightReview("settled", "刚那下躲得好帅！", "攻击扫过时，角色已经闪到了侧面。", (1, 4))


class FakeVision:
    window: int | None = 42
    captures = 0
    result = PRAISE
    hold_review: asyncio.Event | None = None
    description = ""

    def foreground(self) -> int | None:
        return self.window

    def launch_id(self) -> str:
        return "test-game"

    async def capture(self, window: int) -> GameCapture:
        self.captures += 1
        return GameCapture(b"frame")

    async def describe(self, frame: GameFrame) -> GameObservation | None:
        return (GameObservation(self.description, frame.captured_at, frame.captured_at)
                if self.description else None)

    async def review(self, frames: tuple[GameFrame, ...]) -> HighlightReview:
        if self.hold_review is not None:
            await self.hold_review.wait()
        return self.result

    async def close(self) -> None:
        pass


class GameCompanionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.vision = FakeVision()
        self.now = 100.0
        self.voice: object | None = object()
        self.announced: list[GameHighlight] = []

        async def announce(highlight: GameHighlight, current: Callable[[], bool]) -> bool:
            self.assertTrue(current())
            self.announced.append(highlight)
            return True

        self.game = GameCompanion(self.vision, lambda: self.voice, announce, clock=lambda: self.now)
        self.addAsyncCleanup(self.game.close)

    async def tick(self, count: int = 1) -> None:
        for _ in range(count):
            self.now += .5
            await self.game._tick(self.now)
            await asyncio.sleep(0)

    async def test_captures_only_in_matching_voice_call_and_window(self) -> None:
        voice, self.voice = self.voice, None
        await self.tick()
        self.voice, self.vision.window = voice, None
        await self.tick()
        self.assertEqual(self.vision.captures, 0)
        self.vision.window = 42
        await self.tick(6)
        self.assertEqual(len(self.announced), 1)
        self.assertEqual(self.game.reference(), "")

        interaction = InteractionService(StateEvents(lambda _event: None), None, None)
        interaction.game_playback_started = self.game.playback_started
        await interaction._notification_event(
            "playback_started", {"notification_id": self.announced[0].receipt_id})
        self.assertIn(PRAISE.detail, self.game.reference())
        self.vision.window = 43
        self.assertEqual(self.game.reference(), "")
        await self.tick()
        self.vision.window = 42
        self.assertEqual(self.game.reference(), "")

    async def test_silent_scene_supplies_conversation_context_until_expired(self) -> None:
        # This observation is available even though there is no proactive line.
        self.vision.result = HighlightReview(
            "unclear", observation=GameObservation("走进洞里后，前方出现一个高大的黑影。", 102.5, 103))
        await self.tick(6)
        self.assertEqual(self.announced, [])
        reference = self.game.reference()
        self.assertIn("高大的黑影", reference)
        self.assertIn('"seconds_ago": [0.5, 0.0]', reference)
        self.now += 8.1
        self.assertEqual(self.game.reference(), "")
        self.now -= 8.1
        self.voice = object()
        self.assertEqual(self.game.reference(), "")

    async def test_normal_voice_turns_use_the_recent_scene_without_phrase_matching(self) -> None:
        self.vision.result = HighlightReview("unclear")
        self.vision.description = "走进洞里后，前方出现一个高大的黑影。"
        await self.tick(6)
        prepared: list[str] = []

        class Voice:
            async def respond_to_query(self, packet: str, *, query_id: str) -> None:
                prepared.append(packet)

        with TemporaryDirectory() as directory:
            conversation = ConversationService(JsonlSessionStore(Path(directory)))
            await conversation.start()
            preparer = TurnPreparer(DisabledMemoryModule())
            self.addAsyncCleanup(preparer.close)
            interaction = InteractionService(
                StateEvents(lambda _event: None), None, None,
                conversation=conversation, turn_preparer=preparer)
            interaction.voice = Voice()
            interaction.game_reference = self.game.reference
            for transcript in ("好恐怖啊", "我鸡皮疙瘩都起来了"):
                await interaction._conversation_event("user_final", {"transcript": transcript})
                self.assertIn("前方出现一个高大的黑影", prepared[-1])
            self.vision.window = None
            await interaction._conversation_event("user_final", {"transcript": "然后呢？"})
            self.assertNotIn("高大的黑影", prepared[-1])

    async def test_review_late_or_disconnected_cannot_open_its_mouth(self) -> None:
        self.vision.hold_review = asyncio.Event()
        await self.tick(6)
        self.now += 6
        self.vision.hold_review.set()
        await asyncio.sleep(0)
        self.assertEqual(self.announced, [])
        self.vision.hold_review = asyncio.Event()
        await self.tick(6)
        self.voice = None
        await self.tick()
        self.vision.hold_review.set()
        await asyncio.sleep(0)
        self.assertEqual(self.announced, [])

    async def test_requires_new_combat_and_expires_previous_reference(self) -> None:
        await self.tick(6)
        self.game.playback_started(self.announced[0].receipt_id)
        await self.tick(70)
        self.assertEqual(len(self.announced), 1)
        self.assertEqual(self.game.reference(), "")
        self.vision.result = HighlightReview("fighting")
        await self.tick(5)
        self.vision.result = PRAISE
        await self.tick(5)
        self.assertEqual(len(self.announced), 2)


class GameHighlightContractTests(unittest.IsolatedAsyncioTestCase):
    def test_scene_observation_does_not_require_a_highlight(self) -> None:
        frames = tuple(GameFrame(i * .5, b"same frame") for i in range(6))
        raw = {"scene": "unclear", "observation": "角色停在通道入口。", "observed_frames": [6]}
        result = HighlightReview.parse(json.dumps(raw), frames)
        self.assertEqual(result.observation, GameObservation("角色停在通道入口。", 2.5, 2.5))
        self.assertFalse(result.praise)
        raw["observed_frames"] = [7]
        self.assertIsNone(HighlightReview.parse(json.dumps(raw), frames).observation)

    def test_rejects_invalid_or_duplicate_visual_evidence(self) -> None:
        frames = tuple(GameFrame(i * .5, bytes([i])) for i in range(6))
        result = {"scene": "settled", "praise": PRAISE.praise, "detail": PRAISE.detail, "frames": [1, 4]}
        self.assertEqual(HighlightReview.parse(json.dumps(result), frames), PRAISE)
        repeated = tuple(GameFrame(frame.captured_at, b"same") for frame in frames)
        self.assertEqual(HighlightReview.parse(json.dumps(result), repeated).scene, "unclear")
        for numbers in ([1, 1], [1, 7], [True, 4], [1]):
            result["frames"] = numbers
            self.assertEqual(HighlightReview.parse(json.dumps(result), frames).scene, "unclear")

    async def test_voice_checks_busy_then_reuses_real_playback_receipt(self) -> None:
        messages: list[dict] = []
        receipts: list[tuple[str, dict]] = []

        class Voice(QwenRealtimeSession):
            async def send(self, message: dict) -> None:
                messages.append(message)

        voice = Voice(None, lambda *_args, **_kwargs: None,
                      notification_event=lambda name, payload: receipts.append((name, payload)))
        highlight = GameHighlight("game-highlight:one", PRAISE, 1)
        voice.input_turn_pending = True
        self.assertFalse(await voice.announce_game_highlight(highlight, lambda: True))
        voice.input_turn_pending = False
        self.assertFalse(await voice.announce_game_highlight(highlight, lambda: False))
        self.assertEqual(messages, [])
        self.assertTrue(await voice.announce_game_highlight(highlight, lambda: True))
        self.assertEqual([message["type"] for message in messages], ["response.create"])
        self.assertIn(PRAISE.praise, messages[0]["response"]["instructions"])
        await voice.receive({"type": "response.created", "response": {"id": "r1"}})
        self.assertFalse(voice._dispatch_tool_call({"call_id": "action", "name": "run_task"}))
        await voice.receive({"type": "response.audio.delta", "response_id": "r1", "delta": ""})
        self.assertEqual(receipts, [])
        voice.playback_changed(True)
        self.assertEqual(receipts, [("playback_started", {"notification_id": highlight.receipt_id})])

    async def test_voice_cancels_if_user_starts_before_response_id(self) -> None:
        messages: list[dict] = []

        class Voice(QwenRealtimeSession):
            async def send(self, message: dict) -> None:
                messages.append(message)

        voice = Voice(None, lambda *_args, **_kwargs: None)
        self.assertTrue(await voice.announce_game_highlight(
            GameHighlight("game-highlight:one", PRAISE, 1), lambda: True))
        await voice.receive({"type": "input_audio_buffer.speech_started"})
        await voice.receive({"type": "response.created", "response": {"id": "r1"}})
        self.assertIn("r1", voice.cancelled_responses)
        self.assertEqual(messages[-1]["type"], "response.cancel")
