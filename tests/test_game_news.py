import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import mock_open, patch

from boxagent.application.game_news import GameNewsMonitor
from boxagent.domain.proactivity.game_news import Game, GameNews
from boxagent.infrastructure.games.steam import SteamGames, latest_steam_news


class GameNewsTests(unittest.IsolatedAsyncioTestCase):
    async def test_launch_announces_once_and_ignores_games_already_open(self) -> None:
        old = Game(10, "Already running")
        new = Game(20, "Just launched")
        running = {old}
        looked_up: list[int] = []
        announcements: list[tuple[Game, GameNews | None]] = []
        sampled = asyncio.Event()
        news = GameNews("Update", "Steam", "https://example.com", 1)

        def running_games() -> set[Game]:
            sampled.set()
            return running.copy()

        def latest(app_id: int) -> GameNews:
            looked_up.append(app_id)
            return news

        async def announce(game: Game, item: GameNews | None) -> None:
            announcements.append((game, item))

        monitor = GameNewsMonitor(running_games, latest, announce, interval=.001)
        await monitor.start()
        try:
            await asyncio.wait_for(sampled.wait(), 1)
            running.add(new)
            async with asyncio.timeout(1):
                while not announcements:
                    await asyncio.sleep(.001)
            running.remove(new)
            sampled.clear()
            await asyncio.wait_for(sampled.wait(), 1)
            running.add(new)
            sampled.clear()
            await asyncio.wait_for(sampled.wait(), 1)
            self.assertEqual(announcements, [(new, news)])
            self.assertEqual(looked_up, [new.app_id])
        finally:
            await monitor.close()

    def test_news_prefers_recent_official_posts(self) -> None:
        now = int(time.time())
        base = {"url": "https://example.com/article", "feedlabel": "Steam"}
        rows = [
            {**base, "title": "Media", "date": now - 10},
            {**base, "title": "Old", "date": now - 31 * 86400,
             "feedname": "steam_community_announcements"},
            {**base, "title": "<b>Official &amp; current</b>", "date": now - 100,
             "feedname": "steam_community_announcements"},
        ]
        response = json.dumps({"appnews": {"appid": 20, "newsitems": rows}}).encode()
        with patch("boxagent.infrastructure.games.steam.urlopen", mock_open(read_data=response)):
            self.assertEqual(latest_steam_news(20), GameNews(
                "Official & current", "Steam", "https://example.com/article", now - 100))

    def test_manifest_matches_game_identity_across_libraries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            steamapps = root / "steamapps"
            extra = root / "extra library" / "steamapps"
            steamapps.mkdir()
            extra.mkdir(parents=True)
            (steamapps / "libraryfolders.vdf").write_text(
                f'"libraryfolders" {{ "1" {{ "path" "{extra.parent}" }} }}')
            (extra / "appmanifest_20.acf").write_text(
                '"AppState" { "appid" "20" "name" "Game 20" "installdir" "Game" }')
            self.assertEqual(SteamGames(steamapps)._installed_games(),
                             {extra.resolve() / "common/Game": Game(20, "Game 20")})
