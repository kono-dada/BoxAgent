import asyncio
import json
import unittest
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory

from boxagent.application.game_companion import GameCompanion
from boxagent.application.game_entry import GameEntry
from boxagent.application.memory import DisabledMemoryModule
from boxagent.application.turn_preparation import TurnPreparer
from boxagent.core.events import StateEvents
from boxagent.domain.conversation import ConversationService
from boxagent.domain.interaction.service import InteractionService
from boxagent.domain.proactivity.game_entry import GameCapture, TrackedQuest
from boxagent.domain.proactivity.game_highlight import (
    GameFrame,
    GameHighlight,
    GameObservation,
    HighlightReview,
)
from boxagent.infrastructure.persistence import JsonlSessionStore
from boxagent.infrastructure.persistence.game_entry import QuestProgressFile
from boxagent.infrastructure.runtimes.qwen.realtime import QwenRealtimeSession

PRAISE = HighlightReview("settled", "刚那下躲得好帅！", "攻击扫过时，角色已经闪到了侧面。", (1, 4))


class FakeVision:
    window: int | None = 42
    launch = "test-game"
    quest: TrackedQuest | None = None
    quest_image: bytes | None = None
    captures = 0
    result = PRAISE
    hold_review: asyncio.Event | None = None
    description = ""
    relevant = True

    def visible_window(self) -> int | None:
        return self.window

    def launch_id(self) -> str:
        return self.launch

    async def capture(self, window: int) -> GameCapture:
        self.captures += 1
        return GameCapture(b"frame", self.quest, quest_image=self.quest_image)

    async def describe(self, frame: GameFrame) -> GameObservation | None:
        return (GameObservation(self.description, frame.captured_at, frame.captured_at)
                if self.description else None)

    async def review(self, frames: tuple[GameFrame, ...]) -> HighlightReview:
        if self.hold_review is not None:
            await self.hold_review.wait()
        return self.result

    async def still_relevant(self, review: HighlightReview, frame: GameFrame) -> bool:
        return self.relevant

    async def close(self) -> None:
        pass

    async def pause_capture(self) -> None:
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

    async def test_active_companionship_requires_fresh_captured_frames(self) -> None:
        self.assertFalse(self.game.is_active())
        await self.tick()
        self.assertTrue(self.game.is_active())
        self.now += 1.6
        self.assertFalse(self.game.is_active())
        self.vision.window = None
        await self.tick()
        self.assertFalse(self.game.is_active())

    async def test_explicit_mode_disables_capture_and_discards_old_context(self) -> None:
        enabled = False
        self.game.enabled = lambda: enabled
        await self.tick()
        self.assertEqual(self.vision.captures, 0)
        enabled = True
        await self.tick()
        self.assertEqual(self.vision.captures, 1)
        self.assertTrue(self.game.is_active())
        enabled = False
        self.assertEqual(self.game.reference(), "")
        await self.tick()
        self.assertEqual(self.vision.captures, 1)
        self.assertFalse(self.game.is_active())

    async def test_silent_scene_supplies_conversation_context_until_expired(self) -> None:
        # This observation is available even though there is no proactive line.
        self.vision.result = HighlightReview(
            "unclear",
            observation=GameObservation("走进洞里后，前方出现一个高大的黑影。", 102.5, 103),
        )
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

    async def test_remembers_silent_play_and_only_greets_on_recent_entry(self) -> None:
        quest = TrackedQuest("嘤鸣初相召", "前往中枢信标")
        lines: list[tuple[str, str]] = []

        async def speak(receipt: str, line: str, current: Callable[[], bool]) -> bool:
            self.assertTrue(current())
            lines.append((receipt, line))
            return True

        with TemporaryDirectory() as directory:
            store = QuestProgressFile(Path(directory) / "game.json")
            self.game.entry = GameEntry(store, speak, clock=lambda: self.now)
            self.voice = None
            self.vision.quest = quest
            await self.tick(2)
            progress = store.read().current
            assert progress is not None
            self.assertEqual(progress.quest, quest)
            self.assertEqual(lines, [])
            self.assertEqual(len(self.game.frames), 0)

            self.vision.window = None
            await self.tick()
            self.assertEqual(self.vision.captures, 2)
            self.now += 9
            self.vision.window = 42
            self.voice = object()
            await self.tick(2)
            self.assertEqual(lines, [])

            self.vision.launch = "reopened-game"
            self.voice = None
            await self.tick(2)
            self.assertEqual(lines, [])
            self.voice = object()
            await self.tick()
            self.assertEqual(len(lines), 1)
            self.assertIn("上次咱们的任务目标就是「前往中枢信标」", lines[0][1])
            self.game.entry.playback_started(lines[0][0])
            self.assertEqual(store.read().greeted_launch, "reopened-game")

    async def test_quest_inference_keeps_capturing_and_discards_previous_window(self) -> None:
        held = asyncio.Event()
        quest = TrackedQuest("嘤鸣初相召", "前往中枢信标")
        input_frames: list[bytes] = []

        async def recognize(crop: bytes) -> TrackedQuest:
            input_frames.append(crop)
            await held.wait()
            return quest

        async def speak(receipt: str, line: str, current: Callable[[], bool]) -> bool:
            self.fail("A silent session must never request speech")

        with TemporaryDirectory() as directory:
            store = QuestProgressFile(Path(directory) / "game.json")
            self.game.entry = GameEntry(store, speak, clock=lambda: self.now)
            self.game.recognize_quest = recognize
            self.voice = None
            self.vision.quest_image = b"tracker region only"
            await self.tick(3)
            self.assertEqual(self.vision.captures, 3)
            self.assertEqual(input_frames, [self.vision.quest_image])

            self.vision.window = None
            await self.tick()
            self.vision.window = 42
            await self.tick()
            held.set()
            await asyncio.sleep(0)
            self.assertIsNone(self.game.entry.reading)
            await self.tick(3)
            progress = store.read().current
            assert progress is not None
            self.assertEqual(progress.quest, quest)

    async def test_cloud_scene_offers_a_model_selected_comment_outside_combat(self) -> None:
        self.game.cloud_scene = True
        self.voice = None
        self.vision.result = HighlightReview(
            "moment", "这条金线一路带上台阶，走，看看上面有什么。", "金色路线沿着台阶往上。", (1,),
            GameObservation("金色路线沿着台阶往上。", 100.5, 100.5))
        await self.tick()
        self.assertEqual(len(self.announced), 1)
        self.game.playback_started(self.announced[0].receipt_id)
        self.voice = object()
        self.assertIn("金色路线", self.game.reference())
        await self.tick(70)
        self.assertEqual(len(self.announced), 1)

    async def test_game_comment_can_play_without_opening_microphone(self) -> None:
        opened: list[bool] = []
        delivered: list[str] = []

        class Speaker:
            async def announce_game_line(self, receipt: str, text: str,
                                         still_current: Callable[[], bool]) -> bool:
                delivered.append(text)
                return still_current()

        interaction = InteractionService(StateEvents(lambda _event: None), None, None)
        interaction.voice_factory = object()
        interaction.events.emit("game.companion_mode_changed", game_companion_enabled=True)

        async def connect(*, microphone: bool) -> None:
            opened.append(microphone)
            interaction.voice = Speaker()

        interaction._connect = connect
        self.assertTrue(
            await interaction.announce_game_line(
                "game-highlight:scene", "这片灯光真好看。", lambda: True
            )
        )
        self.assertEqual(opened, [False])
        self.assertEqual(delivered, ["这片灯光真好看。"])

    async def test_cloud_mode_greets_with_remembered_progress_before_scene_chat(self) -> None:
        lines: list[tuple[str, str]] = []

        async def speak(receipt: str, line: str, current: Callable[[], bool]) -> bool:
            self.assertTrue(current())
            lines.append((receipt, line))
            return True

        async def prepare_audio() -> None:
            pass

        with TemporaryDirectory() as directory:
            self.game.cloud_scene = True
            self.game.prepare_audio = prepare_audio
            self.game.entry = GameEntry(
                QuestProgressFile(Path(directory) / "game.json"), speak, clock=lambda: self.now)
            self.vision.quest = TrackedQuest("山雨欲来风满楼", "和库洛夫互动")
            await self.tick(2)
            self.assertEqual(len(lines), 1)
            self.assertEqual(self.announced, [])
            self.game.entry_playback_started(lines[-1][0])
            self.now += 35
            self.vision.launch = "second-launch"
            await self.tick(2)
            self.assertEqual(len(lines), 2)
            self.assertIn("上次", lines[-1][1])
            self.assertIn("和库洛夫互动", lines[-1][1])
            self.assertEqual(self.announced, [])
            self.game.entry_playback_started(lines[-1][0])
            self.assertEqual(self.game.entry.history.greeted_launch, "second-launch")
            await self.tick(6)
            self.assertEqual(self.announced, [])

    async def test_current_comment_is_dropped_when_latest_scene_fails_verification(self) -> None:
        self.game.cloud_scene = True
        self.vision.relevant = False
        self.vision.result = HighlightReview(
            "moment", "右边那片花真好看。", "路边有一片花。", (1,),
            GameObservation("路边有花。", 100.5, 100.5))
        await self.tick()
        self.assertEqual(self.announced, [])

    async def test_scene_change_while_waiting_in_inbox_invalidates_comment(self) -> None:
        self.game.cloud_scene = True
        self.vision.result = HighlightReview(
            "moment", "前面的灯真好看。", "台阶前有灯。", (1,),
            GameObservation("台阶前有灯。", 100.5, 100.5))
        validity: list[Callable[[], bool]] = []

        async def queue(highlight: GameHighlight, current: Callable[[], bool]) -> bool:
            validity.append(current)
            return True

        self.game.announce = queue
        await self.tick()
        self.assertTrue(validity[0]())
        self.game.frames.append(GameFrame(self.now, b"new room"))
        self.assertFalse(validity[0]())

    def test_comment_timing_requires_evidence_and_sets_different_deadlines(self) -> None:
        frames = (GameFrame(10, b"before"), GameFrame(11, b"after"))
        raw = {"scene": "moment", "praise": "刚才那下闪得漂亮！", "detail": "角色避开攻击。",
               "frames": [1, 2], "observation": "角色避开攻击。", "observed_frames": [1, 2],
               "timing": "recollection"}
        review = HighlightReview.parse(json.dumps(raw), frames)
        self.assertEqual(review.timing, "recollection")
        self.assertEqual(GameHighlight("recall", review, 11).expires_at, 21)
        raw["praise"] = "右边快躲！"
        self.assertEqual(HighlightReview.parse(json.dumps(raw), frames).scene, "unclear")
        raw["timing"] = "urgent"
        urgent = HighlightReview.parse(json.dumps(raw), frames)
        self.assertEqual(GameHighlight("urgent", urgent, 11).expires_at, 13)

    async def test_scene_change_after_response_created_cancels_first_audio(self) -> None:
        messages: list[dict] = []
        captions: list[str] = []
        current = True

        class Voice(QwenRealtimeSession):
            async def send(self, message: dict) -> None:
                messages.append(message)

        def emit(name: str, **changes: str) -> None:
            if name == "front.assistant_text":
                captions.append(changes["assistant_text"])

        voice = Voice(None, emit)
        await voice.announce_game_line("game-highlight:old", "右边有东西。", lambda: current)
        await voice.receive({"type": "response.created", "response": {"id": "old-scene"}})
        current = False
        await voice.receive(
            {"type": "response.audio.delta", "response_id": "old-scene", "delta": ""}
        )
        await asyncio.sleep(0)
        self.assertIn("old-scene", voice.cancelled_responses)
        self.assertEqual(messages[-1]["type"], "response.cancel")
        self.assertEqual(captions, [])

    async def test_played_caption_remains_in_the_companion_conversation(self) -> None:
        with TemporaryDirectory() as directory:
            conversation = ConversationService(JsonlSessionStore(Path(directory)))
            session = await conversation.start()
            interaction = InteractionService(
                StateEvents(lambda _event: None), None, None, conversation=conversation
            )
            receipt = "game-highlight:visible-text"
            payload = {
                "notification_id": receipt,
                "game_line": PRAISE.praise,
                "session_id": session.session_id,
            }
            await interaction._notification_event("playback_started", payload)
            await interaction._notification_event("playback_started", payload)
            messages = await conversation.recent_messages(session.session_id)
            self.assertEqual(
                [(item.event_id, item.content) for item in messages], [(receipt, PRAISE.praise)]
            )

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

    async def test_voice_query_refreshes_game_evidence_after_turn_preparation(self) -> None:
        evidence = ["已过期的旧场景"]
        delivered: list[str] = []

        class Preparation:
            async def prepare(self, query: str, **kwargs: object) -> tuple[str, dict[str, object]]:
                evidence[0] = "<boxagent_game_quest>新画面任务：和库洛夫互动</boxagent_game_quest>"
                return "", {}

        class Voice:
            async def respond_to_query(self, packet: str, *, query_id: str) -> None:
                delivered.append(packet)

        with TemporaryDirectory() as directory:
            conversation = ConversationService(JsonlSessionStore(Path(directory)))
            await conversation.start()
            interaction = InteractionService(StateEvents(lambda _: None), None, None,
                conversation=conversation, turn_preparer=Preparation())
            interaction.voice = Voice()
            interaction.game_reference = lambda: evidence[0]
            await interaction._conversation_event(
                "user_final", {"transcript": "查一下当前任务的攻略"}
            )
            self.assertIn("新画面任务：和库洛夫互动", delivered[-1])
            self.assertNotIn("已过期的旧场景", delivered[-1])

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
        frames = tuple(GameFrame(i * 0.5, bytes([i])) for i in range(6))
        result = {
            "scene": "settled",
            "praise": PRAISE.praise,
            "detail": PRAISE.detail,
            "frames": [1, 4],
        }
        self.assertEqual(HighlightReview.parse(json.dumps(result), frames), PRAISE)
        repeated = tuple(GameFrame(frame.captured_at, b"same") for frame in frames)
        self.assertEqual(HighlightReview.parse(json.dumps(result), repeated).scene, "unclear")
        for numbers in ([1, 1], [1, 7], [True, 4], [1]):
            result["frames"] = numbers
            self.assertEqual(HighlightReview.parse(json.dumps(result), frames).scene, "unclear")

    async def test_voice_checks_busy_then_reuses_real_playback_receipt(self) -> None:
        messages: list[dict] = []
        receipts: list[tuple[str, dict]] = []
        captions: list[str] = []

        class Voice(QwenRealtimeSession):
            async def send(self, message: dict) -> None:
                messages.append(message)

        def emit(name: str, **changes: str) -> None:
            if name == "front.assistant_text":
                captions.append(changes["assistant_text"])

        voice = Voice(None, emit,
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
        self.assertEqual(captions, [])
        voice.playback_changed(True)
        self.assertEqual(receipts, [("playback_started", {
            "notification_id": highlight.receipt_id, "game_line": PRAISE.praise, "session_id": "",
        })])
        self.assertEqual(captions, [PRAISE.praise])

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
