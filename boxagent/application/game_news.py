"""Announce recent news when an installed game starts."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from boxagent.domain.proactivity.game_news import Game, GameNews

logger = logging.getLogger(__name__)


class GameNewsMonitor:
    def __init__(self, running_games: Callable[[], set[Game]],
                 latest_news: Callable[[int], GameNews | None],
                 announce: Callable[[Game, GameNews | None], Awaitable[None]],
                 *, interval: float = 3, cooldown: float = 1800) -> None:
        self.running_games = running_games
        self.latest_news = latest_news
        self.announce = announce
        self.interval = interval
        self.cooldown = cooldown
        self.task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self.task = asyncio.create_task(self._watch(), name="boxagent-game-news")

    async def close(self) -> None:
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None

    async def _watch(self) -> None:
        previous: set[Game] | None = None
        announced: dict[int, float] = {}
        while True:
            try:
                current = self.running_games()
                launched = current - previous if previous is not None else set()
                previous = current
                now = time.monotonic()
                for game in sorted(launched, key=lambda item: item.app_id):
                    if now - announced.get(game.app_id, -self.cooldown) < self.cooldown:
                        continue
                    try:
                        news = await asyncio.to_thread(self.latest_news, game.app_id)
                    except (OSError, ValueError):
                        logger.warning("Steam news lookup failed for app %s", game.app_id)
                        news = None
                    if game in self.running_games():
                        announced[game.app_id] = now
                        await self.announce(game, news)
            except Exception:
                logger.exception("Game news monitor failed")
            await asyncio.sleep(self.interval)
