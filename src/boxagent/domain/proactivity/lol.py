"""Typed LOL observations and event policy, independent of HTTP and voice."""

import json
from dataclasses import dataclass
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
    multikill_count: int | None = None


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
    line: str


_MULTIKILL_LABELS = {2: "双杀", 3: "三杀", 4: "四杀", 5: "五杀"}
_OBJECTIVE_LABELS = {"DragonKill": "小龙被击杀", "TurretKilled": "防御塔被摧毁",
                     "InhibKilled": "高地水晶被摧毁"}


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
            candidates.append(LolMoment("进入对局", 20, "这局我陪你打，有事喊我。"))
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
                    line = {"Win": "赢啦！这局打完了，辛苦啦。", "Lose": "这局输了，先缓一下。"}
                    candidates.append(LolMoment("对局结束", 100,
                                                line.get(event.result, "这局打完了，辛苦啦。")))
                elif event.name == "ChampionKill" and snapshot.player:
                    if event.victim == snapshot.player:
                        candidates.append(LolMoment("本人阵亡", 90, "这次倒了，没事，等复活再来。"))
                    elif event.killer == snapshot.player:
                        candidates.append(LolMoment("本人击杀", 80, "漂亮，刚刚那个人头拿下了。"))
                elif (
                    event.name == "Multikill"
                    and snapshot.player
                    and event.killer == snapshot.player
                ):
                    label = _MULTIKILL_LABELS.get(event.multikill_count or 0)
                    if label:
                        candidates.append(LolMoment(label, 85, f"漂亮，这波拿下{label}！"))
                elif event.name in _OBJECTIVE_LABELS:
                    label = _OBJECTIVE_LABELS[event.name]
                    candidates.append(LolMoment(label, 40, f"{label}了。"))
        if not self.ended and snapshot.health is not None and snapshot.max_health:
            low = 0 < snapshot.health / snapshot.max_health <= .25
            was_low = bool(previous and previous.health is not None and previous.max_health
                           and 0 < previous.health / previous.max_health <= .25)
            if low and not was_low:
                candidates.append(LolMoment("低血量", 70, "血量有点低了，留意一下。"))
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
