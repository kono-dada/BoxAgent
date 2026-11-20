"""Read Riot's documented local Live Client Data API; no LCU or process access."""

import json
import math
import ssl
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, HTTPRedirectHandler, ProxyHandler, build_opener

from boxagent.domain.proactivity.lol import LolEvent, LolRead, LolSnapshot

URL = "https://127.0.0.1:2999/liveclientdata/allgamedata"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


def _object(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _number(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return float(value) if math.isfinite(value) and value >= 0 else None
        except OverflowError:
            return None
    return None


def _integer(value: object) -> int | None:
    number = _number(value)
    return int(number) if number is not None and number.is_integer() else None


def parse_lol_snapshot(value: object) -> LolSnapshot | None:
    root = _object(value)
    game_time = _number(_object(root.get("gameData")).get("gameTime"))
    if game_time is None:
        return None
    active = _object(root.get("activePlayer"))
    players = root.get("allPlayers")
    rows = [_object(p) for p in players] if isinstance(players, list) else []
    # Do not join a missing/ambiguous identity, or equate display names to Riot IDs.
    identity = ""
    selected: dict[str, object] = {}
    key = "riotId" if _text(active.get("riotId")) and any(
        _text(p.get("riotId")) for p in rows) else "summonerName"
    name = _text(active.get(key))
    matches = [p for p in rows if name and _text(p.get(key)) == name]
    if len(matches) == 1:
        selected = matches[0]
        identity = _text(selected.get("riotId")) or _text(selected.get("summonerName"))
    # Live events can use riotIdGameName while player rows carry full Riot IDs.
    # Resolve aliases only when exactly one row owns the source value; never
    # strip '#tag' or guess across accounts that share a display name.
    aliases: dict[str, set[int]] = {}
    for index, row in enumerate(rows):
        for field in ("riotId", "summonerName", "riotIdGameName"):
            alias = _text(row.get(field))
            if alias:
                aliases.setdefault(alias, set()).add(index)

    def actor(value: object) -> str:
        owners = aliases.get(_text(value), set())
        if len(owners) != 1:
            return ""
        owner = rows[next(iter(owners))]
        return _text(owner.get("riotId")) or _text(owner.get("summonerName"))
    stats = _object(active.get("championStats"))
    scores = _object(selected.get("scores"))
    raw_events = _object(root.get("events")).get("Events")
    events: list[LolEvent] = []
    if isinstance(raw_events, list):
        for raw in raw_events:
            event = _object(raw)
            event_id, at = _integer(event.get("EventID")), _number(event.get("EventTime"))
            name = _text(event.get("EventName"))
            if event_id is not None and at is not None and name:
                events.append(LolEvent(event_id, name, at, actor(event.get("KillerName")),
                    actor(event.get("VictimName")), _text(event.get("Result")),
                    _text(event.get("KillerName")), _text(event.get("VictimName"))))
    return LolSnapshot(game_time, identity, _text(selected.get("championName")),
        _number(stats.get("currentHealth")), _number(stats.get("maxHealth")),
        _number(active.get("currentGold")), _integer(scores.get("kills")),
        _integer(scores.get("deaths")), _integer(scores.get("assists")),
        tuple(sorted(events, key=lambda e: e.event_id)))


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: object, fp: object, code: int, msg: str,
                         headers: object, newurl: str) -> None:
        return None


class LolLiveClient:
    def __init__(self) -> None:
        # Riot serves a local self-signed certificate. This exception applies
        # only to this fixed loopback URL; proxies and redirects are disabled.
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        self.opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context), _NoRedirect())

    def read(self) -> LolRead:
        try:
            with self.opener.open(URL, timeout=1) as response:
                data = response.read(MAX_RESPONSE_BYTES + 1)
            if len(data) > MAX_RESPONSE_BYTES:
                return LolRead("invalid")
            snapshot = parse_lol_snapshot(json.loads(data))
            return LolRead("ready", snapshot) if snapshot is not None else LolRead("invalid")
        except HTTPError:
            return LolRead("invalid")
        except (URLError, OSError, TimeoutError):
            return LolRead("unavailable")
        except (ValueError, UnicodeError, RecursionError):
            return LolRead("invalid")
