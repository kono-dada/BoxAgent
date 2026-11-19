import asyncio
import json
import tempfile
import unittest
from collections.abc import Callable, Mapping
from pathlib import Path

from boxagent.application.game_entry import GameEntry
from boxagent.domain.proactivity.game_entry import QuestReading, TrackedQuest
from boxagent.infrastructure.perception.deepseek_quest import DeepSeekQuestVision
from boxagent.infrastructure.perception.wuwa import WindowDescription, visible_game_window
from boxagent.infrastructure.perception.wuwa_quest import tracked_quest
from boxagent.infrastructure.persistence.game_entry import QuestProgressFile


class GameEntryTests(unittest.IsolatedAsyncioTestCase):
    async def test_quest_reference_requires_fresh_two_frame_confirmation_in_same_launch(self) -> None:
        now = 10.0

        async def silent(receipt: str, line: str, valid: Callable[[], bool]) -> bool:
            return False

        with tempfile.TemporaryDirectory() as directory:
            entry = GameEntry(QuestProgressFile(Path(directory) / "game.json"), silent, clock=lambda: now)
            self.addAsyncCleanup(entry.close)
            quest = TrackedQuest("山雨欲来风满楼", "和库洛夫互动")
            entry.observe("launch", quest, now, lambda: True, can_announce=False)
            self.assertEqual(entry.reference("launch"), "")
            now += .5
            entry.observe("launch", quest, now, lambda: True, can_announce=False)
            reference = entry.reference("launch")
            self.assertIn('"title": "山雨欲来风满楼"', reference)
            self.assertIn('"objective": "和库洛夫互动"', reference)
            self.assertEqual(entry.reference("other-launch"), "")
            now += 3.1
            self.assertEqual(entry.reference("launch"), "")
            now -= 3.1
            entry.pause()
            self.assertEqual(entry.reference("launch"), "", "persisted quest is not live screen evidence")

    def test_game_window_remains_visible_when_focus_moves_to_another_display(self) -> None:
        game: WindowDescription = {
            "kCGWindowNumber": 42, "kCGWindowOwnerPID": 5, "kCGWindowName": "鸣潮  ",
            "kCGWindowLayer": 0, "kCGWindowBounds": {"X": 0, "Y": 0, "Width": 1440, "Height": 900},
        }
        codex: WindowDescription = {
            "kCGWindowNumber": 43, "kCGWindowOwnerPID": 6, "kCGWindowLayer": 0,
            "kCGWindowBounds": {"X": 1440, "Y": 0, "Width": 900, "Height": 600},
        }
        games = {5: "鸣潮"}
        self.assertEqual(visible_game_window([codex, game], games, 6), (42, 5))
        overlay: WindowDescription = {
            "kCGWindowNumber": 44, "kCGWindowOwnerPID": 7, "kCGWindowLayer": 0,
            "kCGWindowBounds": {"X": 294, "Y": 406, "Width": 126, "Height": 126},
        }
        self.assertEqual(visible_game_window([overlay, game], games, 7), (42, 5))
        codex["kCGWindowBounds"]["X"] = 20
        self.assertIsNone(visible_game_window([codex, game], games, 6))

    async def test_cloud_reader_requires_a_visible_quest_and_posts_only_the_crop(self) -> None:
        received: list[Mapping[str, object]] = []
        fields = {"title": "嘤鸣初相召", "objective": "前往中枢信标", "distance": "110米"}

        def post(url: str, payload: Mapping[str, object], *, api_key: str, timeout: float) -> object:
            self.assertEqual(url, "https://api.deepseek.com/chat/completions")
            self.assertEqual(api_key, "test-key")
            received.append(payload)
            return {"choices": [{"message": {"content": json.dumps(fields)}}]}

        reader = DeepSeekQuestVision(api_key="test-key", base_url="https://api.deepseek.com/v1", post=post)
        self.assertEqual(await reader(b"crop"), TrackedQuest(fields["title"], fields["objective"]))
        self.assertIn("data:image/png;base64,Y3JvcA==", json.dumps(received[0]))
        fields["distance"] = "看不清"
        self.assertIsNone(await reader(b"crop"))

    async def test_reentry_uses_persisted_real_observations_and_plays_once(self) -> None:
        quest = TrackedQuest("呜鸣初相召", "抵达今州城")
        now = 10.0
        outbound: list[tuple[str, str]] = []

        async def send(receipt: str, line: str, valid: Callable[[], bool]) -> bool:
            if not valid():
                return False
            outbound.append((receipt, line))
            return True

        with tempfile.TemporaryDirectory() as directory:
            store = QuestProgressFile(Path(directory) / "game.json")
            game = GameEntry(store, send, clock=lambda: now)
            game.observe("first", quest, now, lambda: True)
            self.assertIsNone(store.read().current)
            now += .5
            game.observe("first", quest, now, lambda: True)
            await asyncio.sleep(0)
            self.assertIn("现在", outbound[-1][1])
            game.playback_started(outbound[-1][0])
            await game.close()
            self.assertEqual(store.read().greeted_launch, "first")

            game = GameEntry(store, send, clock=lambda: now)
            self.addAsyncCleanup(game.close)
            now += 20
            game.observe("second", quest, now, lambda: True)
            now += .5
            game.observe("second", quest, now, lambda: True)
            now += .1
            game.observe("second", None, now, lambda: True)
            await asyncio.sleep(0)
            self.assertIn("上次咱们的任务目标就是「抵达今州城」", outbound[-1][1])
            game.playback_started(outbound[-1][0])
            game.pause()
            for _ in range(4):
                now += .5
                game.observe("second", quest, now, lambda: True)
                await asyncio.sleep(0)
            self.assertEqual(len(outbound), 2)

    def test_requires_a_quest_target_aligned_with_the_distance_cue(self) -> None:
        title = QuestReading("呜鸣初相召", .10, .71, .04)
        target = QuestReading("抵达今州城", .10, .62, .043)
        distance = QuestReading("70 米▲", .11, .55, .041)
        self.assertEqual(tracked_quest((distance, title, target)),
                         TrackedQuest(title.text, target.text))
        self.assertIsNone(tracked_quest((title, target)))
