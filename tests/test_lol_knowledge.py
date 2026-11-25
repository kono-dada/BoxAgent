import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from boxagent.application.assistant import BoxAgentApplication
from boxagent.infrastructure.games.data_dragon import DataDragonKnowledge


class LolKnowledgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_aliases_runes_summoners_and_community_use_the_foreground_tool(self) -> None:
        base = "cdn/16.23.1/data/zh_CN"
        fixtures = {
            f"{base}/champion.json": {"data": {"Malphite": {"name": "熔岩巨兽", "key": "54"}}},
            f"{base}/champion/Malphite.json": {
                "data": {
                    "Malphite": {
                        "name": "熔岩巨兽",
                        "spells": [{"name": "地震碎片", "description": "偷取移动速度。"}],
                    }
                }
            },
            f"{base}/runesReforged.json": [
                {
                    "slots": [
                        {
                            "runes": [
                                {
                                    "id": 8437,
                                    "name": "不灭之握",
                                    "longDesc": "<b>周期性强化攻击。</b>",
                                }
                            ]
                        }
                    ]
                }
            ],
            f"{base}/summoner.json": {
                "data": {
                    "SummonerFlash": {
                        "name": "闪现",
                        "description": "向目标方向传送一小段距离。",
                        "cooldownBurn": "300",
                    }
                }
            },
        }
        with tempfile.TemporaryDirectory() as cache:
            provider = DataDragonKnowledge(Path(cache), version="16.23.1")
            provider._download = Mock(side_effect=fixtures.__getitem__)
            provider._download_community = Mock(
                return_value={
                    "name": "熔岩巨兽",
                    "passive": {"name": "花岗岩护盾", "description": "受到护盾保护。"},
                }
            )
            app = BoxAgentApplication(lambda _: None, None, None, lol_knowledge=provider)
            try:
                for kind, name, expected in (
                    ("champion", "石头人", "熔岩巨兽"),
                    ("rune", "不灭之握", "不灭之握"),
                    ("summoner", "闪现", "闪现"),
                    ("champion_detail", "墨菲特", "熔岩巨兽"),
                    ("basic", "补刀", "经济与等级"),
                ):
                    result = await app.handle_tool(
                        "lookup_lol_knowledge", {"kind": kind, "name": name}
                    )
                    self.assertEqual(result["status"], "succeeded")
                    self.assertEqual(result["name"], expected)
                    if kind == "champion_detail":
                        self.assertEqual(result["source_name"], "CommunityDragon")
                        self.assertIn("/16.23/", str(result["source_url"]))
                provider._download_community = Mock(side_effect=OSError("offline"))
                offline = await provider.lookup("champion_detail", "石头人")
                self.assertIsNotNone(offline)
                provider._download_community.assert_not_called()
            finally:
                await app.close()

    async def test_cached_version_is_available_offline_and_basics_need_no_network(self) -> None:
        with tempfile.TemporaryDirectory() as cache:
            provider = DataDragonKnowledge(Path(cache))
            provider._download = Mock(return_value=["16.23.1"])
            self.assertEqual(provider._catalog_version(), "16.23.1")
            fresh = DataDragonKnowledge(Path(cache))
            fresh._download = Mock(side_effect=OSError("offline"))
            self.assertEqual(fresh._catalog_version(), "16.23.1")
            self.assertIsNotNone(await fresh.lookup("basic", "位置分工"))
            self.assertIsNone(await fresh.lookup("basic", "帮我自动操作游戏"))
            self.assertEqual(fresh._download.call_count, 1)

    async def test_duplicate_mode_names_choose_classic_but_exact_ids_keep_their_mode(self) -> None:
        with tempfile.TemporaryDirectory() as cache:
            provider = DataDragonKnowledge(Path(cache), version="16.23.1")
            provider._download = Mock(
                return_value={
                    "data": {
                        "SummonerCherryFlash": {
                            "name": "闪现",
                            "description": "竞技场闪现",
                            "modes": ["CHERRY"],
                        },
                        "SummonerFlash": {
                            "name": "闪现",
                            "description": "峡谷闪现",
                            "modes": ["CLASSIC"],
                        },
                    }
                }
            )
            spell = await provider.lookup("summoner", "闪现")
            assert spell is not None
            self.assertEqual(spell.details["description"], "峡谷闪现")
            other = await provider.lookup("summoner", "SummonerCherryFlash")
            assert other is not None
            self.assertEqual(other.details["description"], "竞技场闪现")
            provider._download = Mock(
                return_value={
                    "data": {
                        "223031": {
                            "name": "无尽之刃",
                            "description": "竞技场无尽",
                            "maps": {"11": False},
                        },
                        "3031": {
                            "name": "无尽之刃",
                            "description": "峡谷无尽",
                            "maps": {"11": True},
                        },
                    }
                }
            )
            item = await provider.lookup("item", "无尽之刃")
            assert item is not None
            self.assertEqual(item.details["description"], "峡谷无尽")

    async def test_foreground_tool_finds_localized_champion_and_reuses_cached_patch(self) -> None:
        base = "cdn/16.23.1/data"
        fixtures = {
            f"{base}/zh_CN/champion.json": {"data": {"MissFortune": {"name": "赏金猎人"}}},
            f"{base}/zh_TW/champion.json": {"data": {"MissFortune": {"name": "好運姐"}}},
            f"{base}/zh_TW/champion/MissFortune.json": {
                "data": {
                    "MissFortune": {
                        "name": "好運姐",
                        "spells": [{"name": "彈射", "description": "子彈會彈向後方目標。"}],
                    }
                }
            },
        }
        with tempfile.TemporaryDirectory() as cache:
            provider = DataDragonKnowledge(Path(cache), version="16.23.1")
            provider._download = Mock(side_effect=fixtures.__getitem__)
            app = BoxAgentApplication(lambda _: None, None, None, lol_knowledge=provider)
            request = {"kind": "champion", "name": "好運姐"}
            try:
                result = await app.handle_tool("lookup_lol_knowledge", request)
                self.assertEqual(result["catalog_version"], "16.23.1")
                self.assertEqual(result["details"]["Q"], "彈射：子彈會彈向後方目標。")
                link = app.state.web_research
                assert link is not None
                self.assertEqual(link["sources"][0]["url"], result["source_url"])
                provider._download = Mock(side_effect=OSError("offline"))
                self.assertEqual(await app.handle_tool("lookup_lol_knowledge", request), result)
                provider._download.assert_not_called()
            finally:
                await app.close()

    async def test_item_lookup_keeps_item_ids_and_strips_display_markup(self) -> None:
        with tempfile.TemporaryDirectory() as cache:
            provider = DataDragonKnowledge(Path(cache), version="16.23.1")
            provider._download = Mock(
                return_value={
                    "data": {
                        "3072": {
                            "name": "饮血剑",
                            "description": "<mainText>生命偷取<br>护盾</mainText>",
                        },
                    }
                }
            )
            result = await provider.lookup("item", "3072")
            assert result is not None
            self.assertEqual(result.details, {"description": "生命偷取 护盾"})
            self.assertTrue(result.source_url.endswith("/zh_CN/item.json"))
