"""Typed LOL observations and event policy, independent of HTTP and voice."""

from dataclasses import dataclass
import json
from typing import Literal


@dataclass(frozen=True)
class LolEvent:
    event_id: int
    name: str
    at: float
    killer: str = ""
    victim: str = ""
    result: str = ""
    raw_killer: str = ""
    raw_victim: str = ""


@dataclass(frozen=True)
class LolSnapshot:
    game_time: float
    player: str
    champion: str
    health: float | None
    max_health: float | None
    gold: float | None
    kills: int | None
    deaths: int | None
    assists: int | None
    events: tuple[LolEvent, ...]

    def facts(self) -> dict[str, str | float | int | None]:
        return {"source": "LOL 本地公开接口", "time": round(self.game_time, 1),
            "champion": self.champion or None, "health": self.health,
            "max_health": self.max_health, "gold": self.gold,
            "kills": self.kills, "deaths": self.deaths, "assists": self.assists}

    def reference(self) -> str:
        """Compact selected facts, excluding account identities and event history."""
        return json.dumps(self.facts(), ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class LolRead:
    status: Literal["ready", "unavailable", "invalid"]
    snapshot: LolSnapshot | None = None


@dataclass(frozen=True)
class LolMoment:
    name: str
    priority: int
    instruction: str
    snapshot: LolSnapshot


class LolCompanionPolicy:
    """Consume observed events once; never queue stale commentary behind speech."""

    def __init__(self) -> None:
        self.previous: LolSnapshot | None = None
        self.cursor = -1
        self.last_spoken = float("-inf")
        self.spoken: dict[str, float] = {}
        self.ended = False

    def observe(self, snapshot: LolSnapshot, now: float) -> LolMoment | None:
        previous = self.previous
        new_game = previous is None or snapshot.game_time < previous.game_time - 5
        if new_game:
            self.cursor = max((e.event_id for e in snapshot.events), default=-1)
            self.spoken.clear()
            self.last_spoken = float("-inf")
            self.ended = False
        self.previous = snapshot
        candidates: list[LolMoment] = []
        if new_game:
            candidates.append(LolMoment("进入对局", 20,
                "简短打个招呼，陪用户轻松玩这一局；中途接入不要说游戏刚开始。", snapshot))
        else:
            for event in snapshot.events:
                if event.event_id <= self.cursor:
                    continue
                self.cursor = max(self.cursor, event.event_id)
                if not 0 <= snapshot.game_time - event.at <= 10:
                    continue
                if event.name == "GameEnd":
                    self.ended = True
                    # Win/Lose is the player's result; other values remain unknown.
                    result = {"Win": "获胜", "Lose": "失利"}.get(event.result, "结束")
                    candidates.append(LolMoment("对局结束", 100,
                        f"对局{result}，自然收尾陪伴一句；不臆测战绩或胜负原因。", snapshot))
                elif event.name == "ChampionKill" and snapshot.player:
                    if event.victim == snapshot.player:
                        candidates.append(LolMoment("本人阵亡", 90,
                            "明确回应这次阵亡，温和安慰一句，不责怪，不猜凶手、位置或原因，不保证下次获胜。", snapshot))
                    elif event.killer == snapshot.player:
                        candidates.append(LolMoment("本人击杀", 80,
                            "轻松夸一句刚才的击杀，不猜操作细节或敌方英雄。", snapshot))
                elif event.name in {"DragonKill", "TurretKilled", "InhibKilled"}:
                    label = {"DragonKill": "小龙被击杀", "TurretKilled": "防御塔被摧毁",
                             "InhibKilled": "水晶被摧毁"}[event.name]
                    candidates.append(LolMoment(label, 40,
                        f"本次事件是「{label}」。只用中性短句确认这件事，不加建议。"
                        "未提供双方归属，不得推测我方/敌方或据此评价优势、被动、局势。", snapshot))
        if not self.ended and snapshot.health is not None and snapshot.max_health:
            low = 0 < snapshot.health / snapshot.max_health <= .25
            was_low = bool(previous and previous.health is not None and previous.max_health
                           and 0 < previous.health / previous.max_health <= .25)
            if low and not was_low:
                candidates.append(LolMoment("低血量", 70,
                    "温柔提醒血量偏低，最多一句，不猜药水、技能、敌人位置。", snapshot))
        eligible = [m for m in candidates if now - self.spoken.get(m.name, float("-inf"))
                    >= (90 if m.name == "低血量" else 45)]
        # Death/end must not be suppressed by a recent routine comment.
        urgent = [m for m in eligible if m.name in {"本人阵亡", "对局结束"}]
        if urgent:
            return max(urgent, key=lambda m: m.priority)
        if now - self.last_spoken < 20:
            return None
        return max(eligible, key=lambda m: m.priority, default=None)

    def delivered(self, moment: LolMoment, now: float) -> None:
        self.last_spoken = now
        self.spoken[moment.name] = now
