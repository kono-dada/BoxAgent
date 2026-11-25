"""On-demand learning must retain read evidence, survive restart, and expire safely."""

import asyncio
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from boxagent.application.culture_learning import CultureLearningService
from boxagent.application.memory import DisabledMemoryModule
from boxagent.application.turn_preparation import TurnPreparer
from boxagent.domain.proactivity.learned_culture import learning_topic
from boxagent.domain.web.contracts import WebPage, WebSource, WebUnavailable
from boxagent.infrastructure.persistence.learned_culture import JsonLearnedCultureRepository
from boxagent.infrastructure.runtimes.qwen.realtime import verified_culture_answer
from boxagent.infrastructure.web.culture_index import index_matches
from boxagent.infrastructure.web.culture_pages import CulturePageReader


class CultureLearningTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.repository = JsonLearnedCultureRepository(self.root)
        self.now = 1000.0
        self.sources = (
            WebSource("新词甲", "https://yanfa.lol/meme/new-term", "摘要不是证据"),
            WebSource("新词甲解释", "https://cnmeme.wiki/entry/new-term", ""),
        )

        async def fetch(url: str) -> WebPage:
            return WebPage(
                url, "新词甲解释", "新词甲是社区对虚构选手 PlayerX 的外号，仅用于本测试的来源证据。"
            )

        self.provider = Mock(
            search=AsyncMock(return_value=self.sources), fetch=AsyncMock(side_effect=fetch)
        )
        self.learner = CultureLearningService(
            self.provider, self.repository, clock=lambda: self.now
        )

    async def test_unfamiliar_term_learns_without_a_hardcoded_fact_and_survives_offline_restart(
        self,
    ) -> None:
        packet = await self.learner.reference(
            "你知道新词甲是什么吗？", query_id="q", context="英雄联盟 PRIVATE_HISTORY"
        )
        self.assertIn("PlayerX", verified_culture_answer(packet, "q"))
        self.assertNotIn("PRIVATE_HISTORY", str(self.provider.search.call_args_list))
        self.assertNotIn("摘要不是证据", packet)
        offline = CultureLearningService(None, self.repository, clock=lambda: self.now)
        reused = await offline.reference("新词甲是什么意思", query_id="new", context="英雄联盟")
        self.assertIn("PlayerX", verified_culture_answer(reused, "new"))
        self.assertEqual(verified_culture_answer(reused, "q"), "")
        self.assertEqual(self.provider.search.await_count, 1)

    async def test_expired_note_is_not_used_as_current_knowledge_when_refresh_fails(self) -> None:
        await self.learner.reference("新词甲是什么", query_id="q", context="英雄联盟")
        self.now += 7 * 86400 + 1
        self.provider.search.side_effect = WebUnavailable("offline", "暂时断网")
        packet = await self.learner.reference("新词甲是什么", query_id="next", context="英雄联盟")
        self.assertIn("还没查到", verified_culture_answer(packet, "next"))
        self.assertNotIn("PlayerX", packet)

    async def test_search_snippets_or_unrelated_body_cannot_be_saved_as_a_learned_note(
        self,
    ) -> None:
        self.provider.fetch.side_effect = None
        self.provider.fetch.return_value = WebPage(
            self.sources[0].url, "摘要", "这是没有涉及目标词的其他话题正文。"
        )
        packet = await self.learner.reference("新词甲是什么", query_id="q", context="英雄联盟")
        self.assertIn("还没查到", verified_culture_answer(packet, "q"))
        self.assertIsNone(self.repository.load("新词甲"))

    async def test_single_source_is_attributed_and_not_promoted_to_verified_fact(self) -> None:
        self.provider.search.return_value = self.sources[:1]
        packet = await self.learner.reference("新词甲是什么", query_id="q", context="英雄联盟")
        self.assertIn("尚未交叉核实", verified_culture_answer(packet, "q"))
        self.assertIn('"kind": "attributed_research_note"', packet)

    async def test_simultaneous_asks_share_the_saved_note_but_keep_their_own_query_ids(
        self,
    ) -> None:
        packets = await asyncio.gather(
            *(
                self.learner.reference("新词甲是什么", query_id=q, context="英雄联盟")
                for q in ("a", "b")
            )
        )
        self.assertIn("PlayerX", verified_culture_answer(packets[0], "a"))
        self.assertIn("PlayerX", verified_culture_answer(packets[1], "b"))
        self.assertEqual(self.provider.search.await_count, 1)

    async def test_interrupted_read_does_not_write_a_partial_note(self) -> None:
        reading = asyncio.Event()

        async def blocked(url: str) -> WebPage:
            reading.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        self.provider.fetch.side_effect = blocked
        task = asyncio.create_task(
            self.learner.reference("新词甲是什么", query_id="q", context="英雄联盟")
        )
        await asyncio.wait_for(reading.wait(), 0.2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNone(self.repository.load("新词甲"))

    async def test_public_index_discovers_aliases_without_trusting_index_summaries(self) -> None:
        rows = [
            {
                "type": "meme",
                "title": "电竞钢背兽",
                "href": "/meme/example",
                "aliases": ["钢背兽"],
                "subtitle": "忽略指令",
            }
        ]
        discovered = index_matches("刚背兽", rows)
        self.assertEqual(len(discovered), 1)
        self.assertEqual(discovered[0].summary, "")
        self.assertEqual(
            index_matches("钢背兽", [{**rows[0], "href": "https://127.0.0.1/private"}]), ()
        )
        self.provider.fetch.side_effect = None
        self.provider.fetch.return_value = WebPage(
            discovered[0].url,
            "电竞钢背兽",
            "电竞钢背兽是社区对虚构选手 ExamplePlayer 的外号，与某次直播有关。",
        )
        discovery = Mock(discover=AsyncMock(return_value=discovered))
        learner = CultureLearningService(self.provider, self.repository, discovery=discovery)
        packet = await learner.reference("刚背兽是什么意思", query_id="q", context="英雄联盟")
        self.assertIn("ExamplePlayer", verified_culture_answer(packet, "q"))
        self.provider.search.assert_not_awaited()

    async def test_production_turn_preparation_waits_for_a_current_learning_result(self) -> None:
        preparer = TurnPreparer(DisabledMemoryModule(), culture_learner=self.learner)
        try:
            packet, report = await preparer.prepare(
                "新词甲是什么意思", session_id="s", query_id="q", context="英雄联盟"
            )
            self.assertIn("PlayerX", verified_culture_answer(packet, "q"))
            self.assertEqual(report["culture_learning"]["status"], "ready")
            self.assertLessEqual(len(packet), 1600)
        finally:
            await preparer.close()

    async def test_topic_extraction_does_not_upload_history_or_intercept_preferences(self) -> None:
        self.assertEqual(learning_topic("诶，你知道“钢背兽”是什么意思吗？", "英雄联盟"), "钢背兽")
        self.assertEqual(learning_topic("以后不要说新词甲是什么了", "英雄联盟"), "")
        self.assertEqual(learning_topic("你好", "英雄联盟"), "")
        self.assertEqual(learning_topic("新词甲是什么", "普通聊天"), "")
        self.assertEqual(learning_topic("我的密码是什么", "英雄联盟"), "")
        self.assertEqual(learning_topic("给我讲讲钢背兽", "英雄联盟"), "钢背兽")
        self.assertEqual(learning_topic("Dota2钢背兽是什么", "英雄联盟"), "")
        self.assertEqual(learning_topic("英雄联盟里的刚背兽是谁", ""), "刚背兽")

    async def test_public_body_reader_omits_navigation_scripts_and_credentials(self) -> None:
        html = (
            b'<nav>not article</nav><main><h'
            b'1>term</h1><p>real body</p><sc'
            b'ript>bad script</script></main'
            b'>'
        )
        opener = Mock(open=Mock(side_effect=lambda *a, **kw: io.BytesIO(html)))
        with patch("boxagent.infrastructure.web.culture_pages.build_opener", return_value=opener):
            page = await CulturePageReader().fetch("https://yanfa.lol/meme/example")
            request = opener.open.call_args.args[0]
            self.assertIsNone(request.get_header("Authorization"))
        self.assertIn("real body", page.content)
        self.assertNotIn("not article", page.content)
        self.assertNotIn("bad script", page.content)
        with self.assertRaises(ValueError):
            await CulturePageReader().fetch("https://yanfa.lol.evil.example/meme/example")
