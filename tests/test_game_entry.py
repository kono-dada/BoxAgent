import asyncio
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path

from boxagent.application.game_entry import GameEntry
from boxagent.domain.proactivity.game_entry import QuestReading, TrackedQuest
from boxagent.infrastructure.perception.wuwa_quest import tracked_quest
from boxagent.infrastructure.persistence.game_entry import QuestProgressFile


class GameEntryTests(unittest.IsolatedAsyncioTestCase):
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
