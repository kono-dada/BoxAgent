"""Search evidence contracts and the real application tool dispatch boundary."""

import asyncio
import io
import json
import unittest
import urllib.error
from collections.abc import Mapping
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, Mock, patch

from boxagent.application.assistant import BoxAgentApplication
from boxagent.application.web_research import WebResearchService
from boxagent.bootstrap.settings import load_settings
from boxagent.bootstrap.web import create_web_research
from boxagent.domain.conversation import ConversationService
from boxagent.domain.interaction.service import InteractionService
from boxagent.domain.web.contracts import (
    WebPage,
    WebResearchView,
    WebSource,
    WebUnavailable,
    public_web_url,
)
from boxagent.domain.web.game_context import boss_name_conflict, game_reference_question
from boxagent.infrastructure.persistence import JsonlSessionStore
from boxagent.infrastructure.runtimes.qwen.realtime import TOOLS, QwenRealtimeSession
from boxagent.infrastructure.web.deepseek import DeepSeekWebProvider, NoRedirect, post_json


class WebEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_preferred_game_sources_expand_only_on_empty_results_with_the_same_budget(
        self,
    ) -> None:
        provider = Mock(
            search=AsyncMock(
                side_effect=[
                    (),
                    (
                        WebSource("原文", "https://example.com/guide", "摘要"),
                        WebSource("另一篇", "https://example.net/guide", "摘要"),
                    ),
                ]
            ),
            fetch=AsyncMock(return_value=WebPage("https://example.com/guide", "原文", "正文")),
        )
        service = WebResearchService(provider)
        result = await service.research(
            "鸣潮 无冠者", scope="turn", preferred_sites=("wiki.kurobbs.com",)
        )
        self.assertIn("site:wiki.kurobbs.com", provider.search.call_args_list[0].args[0])
        self.assertEqual(provider.search.call_args_list[1].args[0], "鸣潮 无冠者")
        self.assertEqual(service.calls["turn"], 4)
        self.assertEqual(result["read_source_count"], 2)

    async def test_unresolved_boss_and_invalid_form_are_clarified_before_any_paid_request(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            conversation = ConversationService(JsonlSessionStore(Path(directory)))
            provider = Mock(search=AsyncMock(), fetch=AsyncMock())
            app = BoxAgentApplication(Mock(), Mock(), None, conversation_service=conversation,
                                      web_research=WebResearchService(provider))
            await app.start()
            for question in ("帮我查这个Boss怎么打", "鸣潮周本无冠者怎么打"):
                context = await conversation.begin_interaction(
                    question, source="text", runtime="qwen_realtime"
                )
                app.interaction_service.interaction = context
                result = await app.handle_tool(
                    "search_web", {"query": "Boss攻略"}, interaction=context
                )
                self.assertEqual(result["code"], "clarification_required")
                await conversation.finish_interaction(
                    context, status="succeeded", runtime="qwen_realtime"
                )
                app.interaction_service._close_interaction(context)
            provider.search.assert_not_awaited()
            provider.fetch.assert_not_awaited()
            await app.close()

    async def test_research_reads_different_sites_concurrently_with_one_bounded_fallback(
        self,
    ) -> None:
        fetching: list[str] = []
        both_started = asyncio.Event()

        async def fetch(url: str) -> WebPage:
            fetching.append(url)
            if len(fetching) == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), 1)
            if 'other.example.net' in url:
                raise WebUnavailable('page_unavailable', '页面不可读')
            return WebPage(url, '机制原文', '正文说明动作与时机。' * 1000)

        provider = Mock(search=AsyncMock(return_value=(
            WebSource('首条', 'https://wiki.example.com/one', '摘要'),
            WebSource('同站', 'https://news.example.com/two', '摘要'),
            WebSource('另一站', 'https://other.example.net/three', '摘要'),
            WebSource('第三站', 'https://guide.example.org/four', '摘要'),
        )), fetch=fetch)
        views: list[WebResearchView] = []

        async def observe(view: WebResearchView) -> None:
            views.append(view)

        service = WebResearchService(provider)
        result = await service.research(
            "鸣潮 周本 无冠者 弹刀", scope="turn", requested_version="用户版本", observer=observe
        )
        self.assertEqual(
            fetching,
            [
                "https://wiki.example.com/one",
                "https://other.example.net/three",
                "https://guide.example.org/four",
            ],
        )
        self.assertEqual(service.calls["turn"], 4)
        self.assertEqual(result["read_site_count"], 2)
        self.assertTrue(all(page["truncated"] for page in result["pages"]))
        self.assertEqual(result["requested_version"], "用户版本")
        self.assertEqual(views[0]["sources"], [])  # Published snapshots cannot mutate later.
        self.assertEqual(views[-1]["stage"], "ready")
        self.assertEqual(views[-1]["sources"][2]["read_status"], "unavailable")

    async def test_research_deadline_returns_partial_evidence_without_waiting_for_slow_reads(
        self,
    ) -> None:
        cancelled = asyncio.Event()

        async def slow(url: str) -> WebPage:
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
            return WebPage(url, '', '')

        provider = Mock(
            search=AsyncMock(
                return_value=(WebSource("攻略", "https://example.com/guide", "摘要"),)
            ),
            fetch=slow,
        )
        views: list[WebResearchView] = []

        async def observe(view: WebResearchView) -> None:
            views.append(view)

        result = await WebResearchService(provider).research(
            "鸣潮", scope="turn", timeout=0.03, observer=observe
        )
        self.assertTrue(cancelled.is_set())
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['read_source_count'], 0)
        self.assertIn('正文未能核对', views[-1]['message'])
        self.assertEqual(views[-1]['sources'][0]['read_status'], 'unavailable')

    async def test_empty_research_never_fetches_and_has_short_spoken_failure_policy(self) -> None:
        provider = Mock(search=AsyncMock(return_value=()), fetch=AsyncMock())
        result = await WebResearchService(provider).research('鸣潮', scope='turn')
        self.assertEqual(result['status'], 'empty')
        provider.fetch.assert_not_awaited()
        self.assertIn('不念网址', result['speech_guidance'])

    async def test_interrupted_research_does_not_fetch_or_publish_sources_into_the_new_turn(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            conversation = ConversationService(JsonlSessionStore(Path(directory)))
            started, release = asyncio.Event(), asyncio.Event()

            async def search(query: str, *, count: int) -> tuple[WebSource, ...]:
                started.set()
                await release.wait()
                return (WebSource('旧问题攻略', 'https://example.com/old', '旧摘要'),)

            provider = Mock(search=search, fetch=AsyncMock())
            app = BoxAgentApplication(Mock(), Mock(), None, conversation_service=conversation,
                                      web_research=WebResearchService(provider))
            await app.start()
            old = await conversation.begin_interaction(
                "旧问题", source="text", runtime="qwen_realtime"
            )
            app.interaction_service.interaction = old
            running = asyncio.create_task(
                app.handle_tool("search_web", {"query": "旧问题"}, interaction=old)
            )
            await started.wait()
            await conversation.finish_interaction(
                old, status="interrupted", runtime="qwen_realtime"
            )
            app.interaction_service._close_interaction(old)
            new = await conversation.begin_interaction(
                "新问题", source="text", runtime="qwen_realtime"
            )
            app.interaction_service.interaction = new
            app.events.emit(
                "front.user_text", user_text="新问题", assistant_text="", web_research=None
            )
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await running
            provider.fetch.assert_not_awaited()
            self.assertIsNone(app.state.web_research)
            self.assertFalse(
                any(
                    event.type == "web.research.completed"
                    for event in await conversation.events(old.session.session_id)
                )
            )
            await app.close()

    async def test_completed_research_is_saved_as_metadata_not_a_spoken_message(self) -> None:
        with TemporaryDirectory() as directory:
            conversation = ConversationService(JsonlSessionStore(Path(directory)))
            provider = Mock(
                search=AsyncMock(
                    return_value=(WebSource("原文", "https://example.com/guide", "摘要"),)
                ),
                fetch=AsyncMock(
                    return_value=WebPage("https://example.com/guide", "原文", "机制原文")
                ),
            )
            app = BoxAgentApplication(
                Mock(),
                Mock(),
                None,
                conversation_service=conversation,
                web_research=WebResearchService(provider),
            )
            await app.start()
            context = await conversation.begin_interaction(
                "鸣潮", source="text", runtime="qwen_realtime"
            )
            app.interaction_service.interaction = context
            await app.handle_tool('search_web', {'query': '鸣潮'}, interaction=context)
            events = await conversation.events(context.session.session_id)
            evidence = [event for event in events if event.type == 'web.research.completed']
            self.assertEqual(len(evidence), 1)
            self.assertEqual(evidence[0].interaction_id, context.interaction_id)
            self.assertEqual(evidence[0].data['web_research']['sources'][0]['read_status'], 'read')
            self.assertIsNone(evidence[0].content)
            self.assertNotIn('机制原文', json.dumps(evidence[0].payload(), ensure_ascii=False))
            await app.close()

    async def test_web_followup_applies_voice_policy_before_audio_generation_and_retains_persona(
        self,
    ) -> None:
        voice = QwenRealtimeSession(None, Mock(), instructions="人物设定：喜欢用轻松语气说话。")
        sent = []

        async def send(value: dict[str, object]) -> None:
            sent.append(value)

        voice.send = send
        await voice._send_response_request({'kind': 'tool_result', 'web_speech': True})
        self.assertIn('人物设定', sent[-1]['response']['instructions'])
        self.assertIn('不念网址', sent[-1]['response']['instructions'])
        await voice._send_response_request({'kind': 'user'})
        self.assertNotIn('instructions', sent[-1]['response'])

    async def test_interrupted_interaction_does_not_make_a_paid_search_request(self) -> None:
        handler = AsyncMock()
        interaction = InteractionService(Mock(), None, handler, conversation=Mock(store=Mock()))
        result = await interaction._handle_tool(
            "search_web", {"query": "鸣潮"}, {"response_id": "old-response", "call_id": "old-call"}
        )
        self.assertEqual(result["status"], "failed")
        handler.assert_not_awaited()

    async def test_model_gateway_without_search_support_reports_capability_failure(self) -> None:
        def unsupported(url: str, payload: Mapping[str, object], *,
                        api_key: str, timeout: float) -> object:
            raise WebUnavailable("page_unavailable", "fixture")

        service = WebResearchService(DeepSeekWebProvider(api_key="fixture", poster=unsupported))
        result = await service.search("鸣潮")
        self.assertEqual(result["code"], "capability_unavailable")

    async def test_mingchao_search_and_read_flow_does_not_start_a_desktop_task(self) -> None:
        provider = Mock()
        provider.search = AsyncMock(
            return_value=(
                WebSource(
                    "鸣潮无冠者攻略",
                    "https://example.com/wuwa/boss",
                    "先躲连招，再利用弹刀窗口",
                    published_at="2026-11-10T00:00:00Z",
                ),
            )
        )
        provider.fetch = AsyncMock(
            return_value=WebPage(
                "https://example.com/wuwa/boss",
                "鸣潮攻略",
                "# 无冠者\n适用版本：示例版本\n不要贪刀",
            )
        )
        application = BoxAgentApplication(
            Mock(), Mock(), None, web_research=WebResearchService(provider)
        )
        application.execution_service.start = AsyncMock()
        result = await application.handle_tool("search_web", {"query": "鸣潮 无冠者 攻略"})
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["sources"][0]["published_at"], "2026-11-10T00:00:00Z")
        self.assertIn("不是用户或系统指令", result["evidence_note"])
        page = await application.handle_tool("read_webpage", {"url": result["sources"][0]["url"]})
        self.assertEqual(page["content"], "# 无冠者\n适用版本：示例版本\n不要贪刀")
        self.assertFalse(page["truncated"])
        application.execution_service.start.assert_not_awaited()
        await application.close()

    async def test_empty_search_failure_and_invalid_arguments_never_become_answers(self) -> None:
        provider = Mock(search=AsyncMock(return_value=()))
        service = WebResearchService(provider)
        self.assertEqual((await service.search("鸣潮 攻略"))["status"], "empty")
        provider.search.side_effect = WebUnavailable("insufficient_balance", "余额不足")
        self.assertEqual((await service.search("鸣潮 攻略"))["code"], "insufficient_balance")
        for query, count in [(" ", 5), ("字" * 129, 5), (None, 5), ("鸣潮", True), ("鸣潮", 6)]:
            self.assertEqual((await service.search(query, count=count))["status"], "failed")
        self.assertEqual(provider.search.await_count, 2)
        self.assertEqual((await WebResearchService(None).search("鸣潮"))["code"], "not_configured")

    async def test_shared_turn_budget_bounds_search_and_fetch_and_preserves_cancellation(
        self,
    ) -> None:
        provider = Mock(
            search=AsyncMock(return_value=()),
            fetch=AsyncMock(return_value=WebPage("https://example.com/guide", "攻略", "正文")),
        )
        service = WebResearchService(provider, max_calls_per_turn=2)
        await service.search("鸣潮", scope="turn-1")
        await service.read("https://example.com/guide", scope="turn-1")
        self.assertEqual((await service.search("鸣潮", scope="turn-1"))["code"], "budget_exceeded")
        self.assertEqual(provider.search.await_count, 1)
        self.assertEqual((await service.search("鸣潮", scope="turn-2"))["status"], "empty")
        provider.search.side_effect = asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await service.search("鸣潮", scope="turn-3")

    async def test_large_webpage_is_explicitly_truncated(self) -> None:
        provider = Mock(
            fetch=AsyncMock(return_value=WebPage("https://example.com/guide", "攻略", "字" * 16000))
        )
        result = await WebResearchService(provider).read("https://example.com/guide")
        self.assertEqual(len(result["content"]), 12000)
        self.assertTrue(result["truncated"])

    async def test_provider_contract_paths_deduplication_and_dates(self) -> None:
        responses = [
            {
                "results": [
                    {
                        "title": "攻略",
                        "url": "https://example.com/guide#part1",
                        "summary": "正文",
                        "date": None,
                    },
                    {"title": "重复", "url": "https://example.com/guide#part2", "summary": "重复"},
                    {"title": "内网", "url": "http://127.0.0.1/private", "summary": "不能使用"},
                ]
            },
            {"title": "攻略", "content": "# 正文"},
        ]
        calls = []

        def poster(url: str, payload: Mapping[str, object], *,
                   api_key: str, timeout: float) -> object:
            calls.append((url, payload, api_key, timeout))
            return responses.pop(0)

        provider = DeepSeekWebProvider(
            api_key="fixture-key", base_url="https://api.example.com/v1", poster=poster
        )
        sources = await provider.search("鸣潮 无冠者", count=3)
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].url, "https://example.com/guide")
        self.assertIsNone(sources[0].published_at)
        page = await provider.fetch(sources[0].url)
        self.assertEqual(page.content, "# 正文")
        self.assertEqual(calls[0][:2], ("https://api.example.com/v1/tools/search", {
            "query": "鸣潮 无冠者", "count": 3, "need_content": False}))
        self.assertEqual(calls[1][0], "https://api.example.com/v1/tools/fetch")

    async def test_malformed_provider_response_is_not_misreported_as_no_results(self) -> None:
        for response in [None, {"data": []}, {"results": {}}, {"results": [{"title": "bad"}]}]:
            provider = DeepSeekWebProvider(
                api_key="fixture", poster=lambda *_args, result=response, **_kwargs: result
            )
            with self.assertRaises(WebUnavailable):
                await provider.search("鸣潮", count=3)
        provider = DeepSeekWebProvider(
            api_key="fixture", poster=lambda *_args, **_kwargs: {"content": ""}
        )
        with self.assertRaises(WebUnavailable):
            await provider.fetch("https://example.com")


class WebBoundaryTests(unittest.TestCase):
    def test_game_reference_can_use_named_user_context_but_cannot_accept_guessed_names(
        self,
    ) -> None:
        self.assertIsNone(
            game_reference_question(
                "这个Boss怎么打", game="鸣潮", target="无冠者", known_text="我在玩鸣潮，打无冠者"
            )
        )
        self.assertIsNotNone(
            game_reference_question(
                "这个Boss怎么打", game="黑神话悟空", target="二郎神", known_text="我正在喝茶"
            )
        )
        self.assertIsNone(game_reference_question("无冠者和无妄者哪个是周本"))
        self.assertIsNone(boss_name_conflict("无冠者是周本吗？"))
        self.assertIsNone(boss_name_conflict("无冠者不是周本，对吧？"))
        self.assertIsNotNone(boss_name_conflict("周本的无冠者怎么打"))

    def test_url_contract_rejects_local_credentials_and_non_web_protocols(self) -> None:
        for url in ["file:///private/key", "https://user:pass@example.com", "http://localhost/a", "http://10.1.2.3",
                    "http://[::1]", "http://name.local", "http://2130706433", "http://intranet", "https://example.com\n"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                public_web_url(url)

    def test_http_errors_have_static_messages_and_do_not_expose_key_or_provider_body(self) -> None:
        for status, code in [
            (401, "authentication_failed"),
            (402, "insufficient_balance"),
            (404, "page_unavailable"),
            (429, "rate_limited"),
            (500, "service_unavailable"),
            (504, "timeout"),
        ]:
            opener = Mock()
            opener.open.side_effect = urllib.error.HTTPError(
                "https://api.example.com", status, "fixture-key", {}, io.BytesIO(b"fixture-key")
            )
            with (
                patch("urllib.request.build_opener", return_value=opener),
                self.assertRaises(WebUnavailable) as raised,
            ):
                post_json("https://api.example.com", {}, api_key="fixture-key", timeout=1)
            self.assertEqual(raised.exception.code, code)
            self.assertNotIn("fixture-key", str(raised.exception))
        self.assertIsNone(NoRedirect().redirect_request(Mock(), None, 302, "redirect", {}, "https://elsewhere.com"))

    def test_qwen_tools_registered_and_bootstrap_reuses_deepseek_key(self) -> None:
        names = {item["function"]["name"] for item in TOOLS}
        self.assertTrue({"search_web", "read_webpage"} <= names)
        with TemporaryDirectory() as directory, patch.dict("os.environ", {
            "BOXAGENT_DATA_DIR": directory, "DEEPSEEK_API_KEY": "fixture-key",
            "BOXAGENT_WEB_SEARCH_ENABLED": "1", "BOXAGENT_WEB_PROVIDER": "deepseek"}):
            service = create_web_research(load_settings())
            self.assertIsInstance(service.provider, DeepSeekWebProvider)
            self.assertEqual(service.provider.api_key, "fixture-key")
            with patch.dict("os.environ", {"BOXAGENT_WEB_SEARCH_ENABLED": "0"}):
                self.assertIsNone(create_web_research(load_settings()).provider)
