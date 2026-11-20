"""Production parser, policy, Engine command and real voice delivery boundaries."""

import asyncio
import json
import threading
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock, Mock, patch

from boxagent.application.assistant import BoxAgentApplication
from boxagent.application.lol_companion import LolCompanion
from boxagent.core.events import StateEvents
from boxagent.domain.interaction.service import InteractionService
from boxagent.domain.proactivity.lol import LolCompanionPolicy, LolEvent, LolRead, LolSnapshot
from boxagent.infrastructure.games.lol import (
    MAX_RESPONSE_BYTES, URL, LolLiveClient, parse_lol_snapshot,
)
from boxagent.interfaces.engine.server import EngineServer


def game(at: float = 100, **changes: object) -> LolSnapshot:
    snapshot = LolSnapshot(at, "player", "熔岩巨兽", 800, 1000, 500, 1, 0, 2, ())
    return replace(snapshot, **changes)


def payload() -> dict:
    return {"gameData": {"gameTime": 100}, "activePlayer": {
        "riotId": "玩家#CN1", "summonerName": "player", "currentGold": 1200,
        "championStats": {"currentHealth": 200, "maxHealth": 1000}},
        "allPlayers": [{"riotId": "玩家#CN1", "summonerName": "player",
                        "championName": "熔岩巨兽", "scores": {"kills": 1}}],
        "events": {"Events": [{"EventID": 1, "EventTime": 99,
                    "EventName": "ChampionKill", "KillerName": "player"}]}}


class LolBoundaryTests(unittest.TestCase):
    def test_live_short_names_resolve_to_canonical_player_for_kills_and_deaths(self) -> None:
        value = payload()
        value["activePlayer"]["riotIdGameName"] = "玩家"
        value["allPlayers"][0]["riotIdGameName"] = "玩家"
        initial = parse_lol_snapshot(value)
        assert initial is not None
        policy = LolCompanionPolicy()
        policy.observe(initial, 0)
        value["gameData"]["gameTime"] = 130
        value["events"]["Events"].append({"EventID": 2, "EventTime": 129,
            "EventName": "ChampionKill", "KillerName": "玩家"})
        kill = parse_lol_snapshot(value)
        assert kill is not None
        moment = policy.observe(kill, 30)
        assert moment is not None
        self.assertEqual(moment.name, "本人击杀")
        self.assertEqual(kill.events[-1].raw_killer, "玩家")
        policy.delivered(moment, 30)
        value["gameData"]["gameTime"] = 132
        value["events"]["Events"].append({"EventID": 3, "EventTime": 131,
            "EventName": "ChampionKill", "VictimName": "玩家"})
        death = parse_lol_snapshot(value)
        assert death is not None
        moment = policy.observe(death, 32)
        assert moment is not None
        self.assertEqual(moment.name, "本人阵亡")

    def test_ambiguous_short_name_is_not_attributed_to_self(self) -> None:
        value = payload()
        value["allPlayers"][0]["riotIdGameName"] = "同名"
        value["allPlayers"].append({"riotId": "同名#OTHER", "riotIdGameName": "同名"})
        value["events"]["Events"][0]["KillerName"] = "同名"
        snapshot = parse_lol_snapshot(value)
        assert snapshot is not None
        self.assertTrue(snapshot.player)
        self.assertEqual(snapshot.events[0].killer, "")
        self.assertEqual(snapshot.events[0].raw_killer, "同名")

    def test_parser_preserves_unknowns_and_does_not_guess_identity(self) -> None:
        value = payload()
        snapshot = parse_lol_snapshot(value)
        assert snapshot is not None
        self.assertEqual((snapshot.player, snapshot.champion, snapshot.gold),
                         ("玩家#CN1", "熔岩巨兽", 1200))
        self.assertIsNone(snapshot.deaths)
        value["allPlayers"].append(value["allPlayers"][0].copy())
        ambiguous = parse_lol_snapshot(value)
        assert ambiguous is not None
        self.assertEqual(ambiguous.player, "")
        self.assertIsNone(ambiguous.kills)
        # A conflicting Riot ID must not silently fall back to a display name.
        value = payload()
        value["activePlayer"]["riotId"] = "different#CN1"
        conflict = parse_lol_snapshot(value)
        assert conflict is not None
        self.assertEqual(conflict.player, "")

    def test_malformed_boundary_and_nonfinite_facts(self) -> None:
        for value in (None, [], {}, {"gameData": {"gameTime": True}},
                      {"gameData": {"gameTime": float("nan")}}):
            self.assertIsNone(parse_lol_snapshot(value))
        value = payload()
        value["activePlayer"]["currentGold"] = float("inf")
        value["events"]["Events"].append({"EventID": True, "EventTime": -1})
        parsed = parse_lol_snapshot(value)
        assert parsed is not None
        self.assertIsNone(parsed.gold)
        self.assertEqual(len(parsed.events), 1)

    def test_local_reader_bounds_response_and_reports_expected_failures(self) -> None:
        client = LolLiveClient()
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch.object(client.opener, "open", return_value=response) as opened:
            response.read.return_value = json.dumps(payload()).encode()
            self.assertEqual(client.read().status, "ready")
            opened.assert_called_with(URL, timeout=1)
            response.read.assert_called_with(MAX_RESPONSE_BYTES + 1)
            response.read.return_value = b"x" * (MAX_RESPONSE_BYTES + 1)
            self.assertEqual(client.read().status, "invalid")
            response.read.return_value = b"not json"
            self.assertEqual(client.read().status, "invalid")
        with patch.object(client.opener, "open", side_effect=ConnectionRefusedError):
            self.assertEqual(client.read().status, "unavailable")

    def test_join_mid_game_does_not_replay_old_events(self) -> None:
        policy = LolCompanionPolicy()
        old = LolEvent(1, "ChampionKill", 99, killer="player")
        moment = policy.observe(game(events=(old,)), 0)
        assert moment is not None
        self.assertEqual(moment.name, "进入对局")
        policy.delivered(moment, 0)
        self.assertIsNone(policy.observe(game(130, events=(old,)), 30))

    def test_event_priority_cooldown_and_no_backlog(self) -> None:
        policy = LolCompanionPolicy()
        policy.observe(game(), 0)
        death = LolEvent(2, "ChampionKill", 130, victim="player")
        tower = LolEvent(1, "TurretKilled", 130)
        moment = policy.observe(game(130, health=0, events=(tower, death)), 30)
        assert moment is not None
        self.assertEqual(moment.name, "本人阵亡")
        policy.delivered(moment, 30)
        self.assertIsNone(policy.observe(game(132, events=(tower, death,
            LolEvent(3, "ChampionKill", 132, killer="player"))), 32))
        self.assertIsNone(policy.observe(game(160, events=(tower, death,
            LolEvent(3, "ChampionKill", 132, killer="player"))), 60))

    def test_three_total_kills_is_not_a_multikill(self) -> None:
        value = payload()
        value["gameData"]["gameTime"] = 130
        value["allPlayers"][0]["scores"]["kills"] = 3
        value["events"]["Events"] = [
            {"EventID": 2, "EventTime": 129, "EventName": "ChampionKill", "KillerName": "player"},
            {"EventID": 3, "EventTime": 129, "EventName": "Multikill", "KillerName": "other", "KillStreak": 3},
        ]
        policy = LolCompanionPolicy()
        policy.observe(game(), 0)
        snapshot = parse_lol_snapshot(value)
        assert snapshot is not None
        moment = policy.observe(snapshot, 30)
        assert moment is not None
        self.assertEqual(moment.name, "本人击杀")
        self.assertNotIn("三杀", moment.line)

        value["events"]["Events"].append(
            {"EventID": 4, "EventTime": 129, "EventName": "Multikill", "KillerName": "player", "KillStreak": 3})
        snapshot = parse_lol_snapshot(value)
        assert snapshot is not None
        moment = policy.observe(snapshot, 30)
        assert moment is not None
        self.assertEqual(moment.name, "三杀")
        self.assertIn("三杀", moment.line)

    def test_inhibitor_and_match_end_have_separate_announcements(self) -> None:
        policy = LolCompanionPolicy()
        policy.observe(game(), 0)
        inhibitor = LolEvent(1, "InhibKilled", 129)
        moment = policy.observe(game(130, events=(inhibitor,)), 30)
        assert moment is not None
        self.assertIn("高地水晶", moment.line)
        self.assertFalse(policy.ended)
        self.assertNotIn("打完", moment.line)
        policy.delivered(moment, 30)
        end = LolEvent(2, "GameEnd", 131, result="Win")
        moment = policy.observe(game(131, events=(inhibitor, end)), 31)
        assert moment is not None
        self.assertEqual(moment.name, "对局结束")
        self.assertTrue(policy.ended)
        self.assertIn("赢", moment.line)

    def test_low_health_crossing_unknown_and_match_reset(self) -> None:
        policy = LolCompanionPolicy()
        policy.observe(game(), 0)
        low = policy.observe(game(130, health=200), 30)
        assert low is not None
        self.assertEqual(low.name, "低血量")
        policy.delivered(low, 30)
        self.assertIsNone(policy.observe(game(160, health=100), 60))
        policy.observe(game(170, health=800), 70)
        self.assertIsNone(policy.observe(game(180, health=200), 80))
        self.assertIsNone(policy.observe(game(190, health=None, max_health=None), 90))
        opening = policy.observe(game(1), 91)
        assert opening is not None
        self.assertEqual(opening.name, "进入对局")

    def test_old_events_and_missing_identity_do_not_create_personal_claims(self) -> None:
        policy = LolCompanionPolicy()
        policy.observe(game(), 0)
        self.assertIsNone(policy.observe(game(200, player="", events=(
            LolEvent(1, "ChampionKill", 199, killer="player"),
            LolEvent(2, "GameEnd", 150, result="Win"))), 100))


class LolIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_poller_starts_stops_and_drops_declined_comments(self) -> None:
        announce = Mock(return_value=False)
        publish = Mock()
        monitor = LolCompanion(lambda: LolRead("ready", game()), LolCompanionPolicy(),
                               announce, publish, interval=.001)
        await monitor.toggle()
        async with asyncio.timeout(1):
            while not announce.call_count:
                await asyncio.sleep(.001)
        await monitor.close()
        self.assertFalse(monitor.enabled)
        self.assertIsNone(monitor.task)
        self.assertEqual(announce.call_count, 1)
        publish.assert_called_with("off", "LOL 陪玩已关闭")

    async def test_unavailable_and_invalid_never_emit_speech(self) -> None:
        announce = Mock()
        read = Mock(return_value=LolRead("unavailable"))
        monitor = LolCompanion(read, LolCompanionPolicy(), announce, Mock())
        monitor.enabled = True
        await monitor.poll()
        read.return_value = LolRead("invalid")
        await monitor.poll()
        announce.assert_not_called()

    async def test_stop_during_read_discards_late_data_and_can_restart(self) -> None:
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()

        def read() -> LolRead:
            entered.set()
            release.wait(2)
            finished.set()
            return LolRead("ready", game())

        announce = Mock(return_value=True)
        publish = Mock()
        monitor = LolCompanion(read, LolCompanionPolicy(), announce, publish, interval=.001)
        await monitor.toggle()
        try:
            async with asyncio.timeout(1):
                while not entered.is_set():
                    await asyncio.sleep(.001)
            await monitor.close()
            publish.reset_mock()
            release.set()
            async with asyncio.timeout(1):
                while not finished.is_set():
                    await asyncio.sleep(.001)
            self.assertEqual(monitor.reference(), "")
            announce.assert_not_called()
            publish.assert_not_called()
            await monitor.toggle()
            async with asyncio.timeout(1):
                while not announce.call_count:
                    await asyncio.sleep(.001)
        finally:
            release.set()
            await monitor.close()

    async def test_reference_expires_on_disconnect_timeout_and_disable(self) -> None:
        monitor = LolCompanion(lambda: LolRead("ready", game()), LolCompanionPolicy(),
                               Mock(return_value=False), Mock())
        monitor.enabled = True
        await monitor.poll()
        self.assertIn('"gold":500', monitor.reference())
        with patch("boxagent.application.lol_companion.time.monotonic",
                   return_value=monitor.received_at + 7):
            self.assertEqual(monitor.reference(), "")
        monitor.read = lambda: LolRead("unavailable")
        await monitor.poll()
        self.assertEqual(monitor.reference(), "")
        await monitor.close()

    async def test_comment_window_and_cooldown_are_bound_to_playback(self) -> None:
        from boxagent.application.lol_companion import COMMENT_WINDOW_SECONDS

        policy = LolCompanionPolicy()
        announce = Mock(return_value=True)
        read = Mock(return_value=LolRead("ready", game()))
        monitor = LolCompanion(read, policy, announce, Mock())
        monitor.enabled = True
        with patch("boxagent.application.lol_companion.time") as clock:
            clock.monotonic.return_value = 100.0
            await monitor.poll()
            _, current, _ = announce.call_args.args
            self.assertTrue(current())
            self.assertEqual(policy.last_spoken, float("-inf"))
            clock.monotonic.return_value += COMMENT_WINDOW_SECONDS + .001
            self.assertFalse(current())
            read.return_value = LolRead("ready", game(2))
            await monitor.poll()
            _, current, started = announce.call_args.args
            self.assertTrue(current())
            clock.monotonic.return_value = 104.0
            started()
            self.assertEqual(policy.last_spoken, 104.0)
            self.assertFalse(current())
        await monitor.close()

    async def test_engine_toggle_and_application_shutdown_own_monitor(self) -> None:
        app = BoxAgentApplication(lambda event: None, Mock(), None)
        monitor = LolCompanion(lambda: LolRead("unavailable"), LolCompanionPolicy(),
                               Mock(), lambda status, text: app.emit(
                                   "lol.companion", lol_companion_enabled=status != "off"))
        app.lol_companion = monitor
        server = EngineServer.__new__(EngineServer)
        server.application = app
        self.assertEqual(await server.dispatch("toggle_lol_companion", {}), {"enabled": True})
        self.assertTrue(app.state.lol_companion_enabled)
        await app.close()
        self.assertFalse(app.state.lol_companion_enabled)
        self.assertIsNone(monitor.task)

    async def test_chat_and_voice_companion_actions_share_mode_and_microphone(self) -> None:
        voice = AsyncMock()
        voice.ready = asyncio.Event()
        closed = asyncio.Event()

        async def run() -> None:
            voice.ready.set()
            await closed.wait()

        voice.run.side_effect = run
        voice.stop.side_effect = closed.set
        factory = Mock(return_value=voice)
        app = BoxAgentApplication(lambda event: None, Mock(), factory)
        monitor = LolCompanion(lambda: LolRead("unavailable"), LolCompanionPolicy(),
                               Mock(), lambda status, text: app.emit(
                                   "lol.companion", lol_companion_enabled=status != "off"))
        app.lol_companion = monitor
        app.emit("game.companion_available", game_companion_available=True)
        server = EngineServer.__new__(EngineServer)
        server.application = app
        try:
            await server.dispatch("set_companion_session", {"game": "lol", "enabled": True})
            self.assertTrue(monitor.enabled)
            self.assertTrue(app.interaction_service.connection_microphone)
            self.assertEqual(factory.call_count, 1)
            await server.dispatch("set_companion_session", {"game": "wuwa", "enabled": True})
            self.assertFalse(monitor.enabled)
            self.assertTrue(app.state.game_companion_enabled)
            self.assertEqual(factory.call_count, 1)
            await server.dispatch("set_companion_session", {"game": "wuwa", "enabled": False})
            self.assertFalse(app.state.game_companion_enabled)
            self.assertTrue(app.interaction_service.is_connected)
            self.assertEqual(await app.handle_tool(
                "set_game_companion_mode", '{"game":"lol","enabled":true}'),
                {"status": "succeeded", "game": "lol", "enabled": True})
            self.assertTrue(app.state.lol_companion_enabled)
            self.assertFalse(app.state.game_companion_enabled)
            self.assertEqual(factory.call_count, 1)
            invalid = await app.handle_tool("set_game_companion_mode", '{"game":"other","enabled":false}')
            self.assertEqual(invalid["status"], "failed")
            self.assertTrue(app.state.lol_companion_enabled)
            self.assertEqual(await app.handle_tool("set_game_companion_mode", '{"enabled":false}'),
                             {"status": "succeeded", "game": "lol", "enabled": False})
            self.assertFalse(app.state.lol_companion_enabled)
        finally:
            await app.close()

    async def test_dock_mute_keeps_notification_voice_and_releases_the_microphone(self) -> None:
        request = []

        def factory(*args: object, **kwargs: object) -> AsyncMock:
            request.append(kwargs["microphone"])
            voice = AsyncMock()
            voice.ready = asyncio.Event()
            closed = asyncio.Event()

            async def run() -> None:
                voice.ready.set()
                await closed.wait()

            voice.run.side_effect = run
            voice.stop.side_effect = closed.set
            return voice

        app = BoxAgentApplication(lambda event: None, Mock(), factory)
        app.lol_companion = LolCompanion(lambda: LolRead("unavailable"), LolCompanionPolicy(),
                                        Mock(), lambda status, text: app.emit(
                                            "lol.companion", lol_companion_enabled=status != "off"))
        server = EngineServer.__new__(EngineServer)
        server.application = app
        try:
            await server.dispatch("set_companion_session", {"game": "lol", "enabled": True})
            await server.dispatch("set_companion_audio", {"control": "speech", "enabled": False})
            self.assertEqual(request, [True])
            self.assertTrue(app.state.companion_speech_muted)
            await server.dispatch("set_companion_audio", {"control": "microphone", "enabled": False})
            self.assertEqual(request, [True, False])
            self.assertEqual(app.state.voice, "off")
            self.assertTrue(app.interaction_service.is_connected)
            self.assertTrue(await app.interaction_service.deliver_notification({"summary": "收到消息"}))
            await server.dispatch("set_companion_session", {"game": "lol", "enabled": False})
            self.assertFalse(app.interaction_service.is_connected)
            self.assertFalse(app.state.companion_speech_muted)
        finally:
            await app.close()

    async def test_interaction_does_not_reconnect_or_interrupt_user_task(self) -> None:
        events = StateEvents(lambda event: None)
        interaction = InteractionService(events, None, None)
        line = "高地水晶被摧毁了。"
        current = lambda: True
        started = Mock()
        self.assertFalse(interaction.schedule_lol(line, current, started))
        voice = Mock(schedule_lol=Mock(return_value=True))
        interaction.voice = voice
        interaction.task = asyncio.create_task(asyncio.sleep(60))
        try:
            events.state.user_speaking = True
            self.assertTrue(interaction.schedule_lol(line, current, started))
            events.state.task = "running"
            self.assertFalse(interaction.schedule_lol(line, current, started))
            self.assertEqual(voice.schedule_lol.call_count, 1)
            self.assertFalse(voice.schedule_lol.call_args.kwargs["still_current"]())
        finally:
            interaction.task.cancel()
            await asyncio.gather(interaction.task, return_exceptions=True)

    async def test_realtime_uses_expiring_reference_and_blocks_tools(self) -> None:
        from boxagent.infrastructure.runtimes.qwen.realtime import LolSpeech, QwenRealtimeSession
        voice = QwenRealtimeSession.__new__(QwenRealtimeSession)
        voice.delivery_lock = asyncio.Lock()
        voice._delivery_busy = Mock(return_value=False)
        voice.expire_reference = AsyncMock()
        voice.install_reference = AsyncMock()
        voice._create_response = AsyncMock()
        line = "高地水晶被摧毁了。"
        speech = LolSpeech(line, lambda: True, Mock())
        self.assertTrue(await voice._deliver_lol(speech))
        voice.install_reference.assert_awaited_once()
        self.assertEqual(voice.install_reference.call_args.args[1], "lol_companion")
        voice._create_response.assert_awaited_with({"kind": "lol_companion", "lol_line": line, "lol_speech": speech})
        voice.response_id = "lol-response"
        voice.response_context = {"lol-response": {"kind": "lol_companion"}}
        voice.cancelled_responses = set()
        voice.trace = Mock()
        self.assertFalse(voice._dispatch_tool_call({"call_id": "unsafe", "name": "run_task"}))
        voice._delivery_busy.return_value = True
        self.assertFalse(await voice._deliver_lol(speech))
        self.assertEqual(voice._create_response.await_count, 1)

    async def test_mute_cuts_current_game_audio_without_cutting_user_and_wechat(self) -> None:
        from boxagent.infrastructure.runtimes.qwen.realtime import QwenRealtimeSession

        for kind, finished in (("lol_companion", False), ("game_highlight", True), ("game_news", False)):
            with self.subTest(kind=kind, finished=finished):
                voice = QwenRealtimeSession(AsyncMock(), Mock())
                voice.audio = audio = Mock(has_pending_playback=True)
                voice.send = AsyncMock()
                voice.response_origins.append({"kind": kind, "notification_id": "game-line"})
                await voice.receive({"type": "response.created", "response": {"id": "game"}})
                await voice.receive({"type": "response.audio.delta", "response_id": "game", "delta": "pcm"})
                voice.playback_changed(True)
                if finished:
                    await voice._finalize_response("game", source="provider")
                await voice.set_game_speech_muted(True)
                audio.clear.assert_called_once()
                if finished:
                    voice.send.assert_not_awaited()
                else:
                    voice.send.assert_awaited_once_with({"type": "response.cancel"})
                await voice.receive({"type": "response.audio.delta", "response_id": "game", "delta": "late"})
                audio.append.assert_called_once_with("pcm")
                for other in ("user", "notification"):
                    voice.response_origins.append({"kind": other, "notification_id": "wechat"})
                    await voice.receive({"type": "response.created", "response": {"id": other}})
                    await voice.set_game_speech_muted(True)
                    await voice.receive({"type": "response.audio.delta", "response_id": other, "delta": other})
                    audio.append.assert_called_with(other)
                audio.clear.assert_called_once()

    async def test_mute_drops_queued_comment_even_if_unmuted_before_provider_accepts_it(self) -> None:
        from boxagent.infrastructure.runtimes.qwen.realtime import LolSpeech, QwenRealtimeSession

        voice = QwenRealtimeSession(AsyncMock(), Mock())
        voice.send = AsyncMock()
        voice.audio = audio = Mock(has_pending_playback=False)
        voice.pending_lol = LolSpeech("下一条", lambda: True, Mock())
        voice.response_origins.append({"kind": "lol_companion"})
        await voice.set_game_speech_muted(True)
        self.assertIsNone(voice.pending_lol)
        await voice.set_game_speech_muted(False)
        await voice.receive({"type": "response.created", "response": {"id": "queued-game"}})
        await voice.receive({"type": "response.audio.delta", "response_id": "queued-game", "delta": "late"})
        audio.append.assert_not_called()
        voice.send.assert_awaited_once_with({"type": "response.cancel"})

    async def test_game_reference_is_deleted_before_user_response_and_late_input_wins(self) -> None:
        from boxagent.infrastructure.runtimes.qwen.realtime import LolSpeech, QwenRealtimeSession
        voice = QwenRealtimeSession(AsyncMock(), Mock(), host_turn_control=True)
        sent: list[dict] = []
        interrupt = False

        class Socket:
            async def send(self, raw: str) -> None:
                row = json.loads(raw)
                sent.append(row)
                if row["type"] == "conversation.item.create":
                    if interrupt:
                        voice.begin_query("new-user")
                    await voice.receive({"type": "conversation.item.created", "item": {
                        **row["item"], "id": "game-reference"}})
                elif row["type"] == "conversation.item.delete":
                    await voice.receive({"type": "conversation.item.deleted",
                                         "item_id": row["item_id"]})
                elif row["type"] == "response.create":
                    await voice.receive({"type": "response.created", "response": {
                        "id": "response-" + str(len(sent))}})

        voice.ws = Socket()
        line = "高地水晶被摧毁了。"
        self.assertTrue(await voice._deliver_lol(LolSpeech(line, lambda: True, Mock())))
        self.assertEqual(voice.reference_item, "game-reference")
        self.assertEqual(sent[0]["item"]["role"], "user")
        self.assertIn("系统自动触发", sent[0]["item"]["content"][0]["text"])
        self.assertTrue(sent[1]["response"]["instructions"].endswith(json.dumps(line, ensure_ascii=False)))
        await voice.respond_to_query("", query_id="user-question")
        self.assertIsNone(voice.reference_item)
        self.assertEqual([row["type"] for row in sent][-2:],
                         ["conversation.item.delete", "response.create"])

        # A real input arriving while the provider acknowledges the game
        # reference must prevent an unsolicited response after the handshake.
        fresh = QwenRealtimeSession(AsyncMock(), Mock(), host_turn_control=True)
        voice = fresh
        voice.ws = Socket()
        sent.clear()
        interrupt = True
        self.assertFalse(await voice._deliver_lol(LolSpeech("血量有点低了，留意一下。", lambda: True, Mock())))
        self.assertFalse(any(row["type"] == "response.create" for row in sent))
        await voice.respond_to_query("", query_id="new-user")
        self.assertIsNone(voice.reference_item)
