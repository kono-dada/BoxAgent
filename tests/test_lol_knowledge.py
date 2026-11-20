import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from boxagent.application.assistant import BoxAgentApplication
from boxagent.infrastructure.games.data_dragon import DataDragonKnowledge


class LolKnowledgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_foreground_tool_finds_localized_champion_and_reuses_cached_patch(self) -> None:
        base = "cdn/16.23.1/data"
        fixtures = {
            f"{base}/zh_CN/champion.json": {"data": {"MissFortune": {"name": "赏金猎人"}}},
            f"{base}/zh_TW/champion.json": {"data": {"MissFortune": {"name": "好運姐"}}},
            f"{base}/zh_TW/champion/MissFortune.json": {"data": {"MissFortune": {
                "name": "好運姐", "spells": [{"name": "彈射", "description": "子彈會彈向後方目標。"}],
            }}},
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
            provider._download = Mock(return_value={"data": {
                "3072": {"name": "饮血剑", "description": "<mainText>生命偷取<br>护盾</mainText>"},
            }})
            result = await provider.lookup("item", "3072")
            assert result is not None
            self.assertEqual(result.details, {"description": "生命偷取 护盾"})
            self.assertTrue(result.source_url.endswith("/zh_CN/item.json"))
