"""Detect running Steam game bundles and fetch Steam's public news feed."""

import html
import json
import re
import time
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import urlopen

from boxagent.domain.proactivity.game_news import Game, GameNews

_STEAM_NEWS = "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"
_VDF_FIELD = re.compile(r'"(appid|name|installdir|path)"\s+"((?:[^"\\]|\\.)*)"')


def _fields(text: str) -> list[tuple[str, str]]:
    return [(key, value.replace(r"\\", "\\").replace(r'\"', '"'))
            for key, value in _VDF_FIELD.findall(text)]


class SteamGames:
    def __init__(self, steamapps: Path) -> None:
        self.steamapps = steamapps
        self.installed: dict[Path, Game] = {}
        self.refresh_at = 0.0

    def _installed_games(self) -> dict[Path, Game]:
        libraries = {self.steamapps}
        folders = self.steamapps / "libraryfolders.vdf"
        if folders.is_file():
            libraries.update(Path(value) / "steamapps"
                             for key, value in _fields(folders.read_text(errors="replace"))
                             if key == "path" and Path(value).is_absolute())
        games: dict[Path, Game] = {}
        for library in libraries:
            for manifest in library.glob("appmanifest_*.acf"):
                try:
                    data = dict(_fields(manifest.read_text(errors="replace")))
                    directory = data["installdir"]
                    if Path(directory).name != directory or directory in {".", ".."}:
                        continue
                    app_id = int(data["appid"])
                    name = data["name"].strip()
                    if app_id > 0 and name and manifest.stem == f"appmanifest_{app_id}":
                        games[(library / "common" / directory).resolve()] = Game(app_id, name[:120])
                except (OSError, KeyError, ValueError):
                    continue
        return games

    def running(self) -> set[Game]:
        from AppKit import NSWorkspace  # type: ignore[import-untyped]

        now = time.monotonic()
        if now >= self.refresh_at:
            self.installed = self._installed_games()
            self.refresh_at = now + 15
        if not self.installed:
            return set()
        games = set()
        for app in NSWorkspace.sharedWorkspace().runningApplications():
            url = app.bundleURL()
            if url is None or not url.isFileURL():
                continue
            bundle = Path(str(url.path())).resolve()
            for folder in (bundle, *bundle.parents):
                if folder in self.installed:
                    games.add(self.installed[folder])
                    break
        return games


def _plain(value: object, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(re.sub(r"<[^>]*>", "", html.unescape(value)).split())[:limit]


def latest_steam_news(app_id: int) -> GameNews | None:
    query = urlencode({"appid": app_id, "count": 20, "maxlength": 200})
    with urlopen(f"{_STEAM_NEWS}?{query}", timeout=7) as response:
        data = json.loads(response.read(512_000))
    app = data.get("appnews") if isinstance(data, dict) else None
    if not isinstance(app, dict) or app.get("appid") != app_id:
        raise ValueError("Unexpected Steam news response")
    items = app.get("newsitems")
    if not isinstance(items, list):
        raise ValueError("Steam news items missing")
    now = time.time()
    recent: list[tuple[bool, GameNews]] = []
    for item in items:
        if not isinstance(item, dict) or item.get("appid", app_id) != app_id:
            continue
        date = item.get("date")
        if type(date) is not int or not 0 <= now - date <= 30 * 86400:
            continue
        url = item.get("url")
        if not isinstance(url, str) or len(url) > 2048:
            continue
        try:
            parsed = urlsplit(url)
        except ValueError:
            continue
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            continue
        title = _plain(item.get("title"), 240)
        if not title:
            continue
        source = _plain(item.get("feedlabel"), 80) or "Steam"
        official = item.get("feedname") == "steam_community_announcements"
        recent.append(
            (official, GameNews(title, source, quote(url, safe=":/?#[]@!$&'()*+,;=%"), date))
        )
    return max(recent, key=lambda entry: (entry[0], entry[1].published_at))[1] if recent else None



def recent_steam_discussion(app_id: int) -> GameNews | None:
    """Select a recently active public Steam discussion with a concrete topic."""
    url = f"https://steamcommunity.com/app/{app_id}/discussions/0/"
    with urlopen(f"{url}?l=english", timeout=6) as response:
        document = response.read(512_000).decode("utf-8", errors="replace")
    now = time.time()
    candidates: list[tuple[bool, int, GameNews]] = []
    for row in re.split(r'<div [^>]*class="forum_topic(?: [^"]*)?"', document)[1:]:
        link = re.search(r'href="(https://steamcommunity\.com/app/'
                         + str(app_id) + r'/discussions/0/[0-9]+/)"', row)
        title = re.search(r'class="forum_topic_name[^"]*"[^>]*>(.*?)</div>', row, re.S)
        date = re.search(r'class="forum_topic_lastpost"[^>]*data-timestamp="([0-9]{1,12})"', row)
        replies = re.search(r'class="forum_topic_reply_count"[^>]*>.*?>\s*([\d,]+)\s*</div>',
                            row, re.S)
        if not link or not title or not date or not replies:
            continue
        active_at = int(date[1])
        if not 0 <= now - active_at <= 7 * 86400 or "forum_topic_pinned" in row:
            continue
        name = _plain(title[1], 140)
        if len(name) < 8:
            continue
        tooltip = re.search(r'data-tooltip-forum="([^"]*)"', row)
        excerpt = ""
        if tooltip:
            # The first tooltip block previews the post. Later blocks contain author metadata.
            excerpt = _plain(tooltip[1].split("&lt;/div&gt;", 1)[0], 250)
        subject = f"{name} — {excerpt}" if excerpt else name
        concrete = bool(re.search(
            r"\b(?:lost|stuck|missing|broken|annoying|frustrating|bug|wish|too hard)\b",
            name, re.I))
        candidates.append((concrete, int(replies[1].replace(",", "")), GameNews(
            subject, "Steam 社区讨论", link[1], active_at, community_topic=True)))
    if not candidates:
        return None
    return max(candidates, key=lambda entry: (entry[0], min(entry[1], 8), entry[2].published_at))[2]


def steam_game_topic(app_id: int) -> GameNews | None:
    try:
        topic = recent_steam_discussion(app_id)
    except (OSError, ValueError):
        topic = None
    return topic or latest_steam_news(app_id)
