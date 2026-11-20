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
    async def test_poller_starts_stops_and_drops_busy_events(self) -> None:
        announce = AsyncMock(return_value=False)
        publish = Mock()
        monitor = LolCompanion(lambda: LolRead("ready", game()), LolCompanionPolicy(),
                               announce, publish, interval=.001)
        await monitor.toggle()
        async with asyncio.timeout(1):
            while not announce.await_count:
                await asyncio.sleep(.001)
        await monitor.close()
        self.assertFalse(monitor.enabled)
        self.assertIsNone(monitor.task)
        self.assertEqual(announce.await_count, 1)
        publish.assert_called_with("off", "LOL 陪玩已关闭")

    async def test_unavailable_and_invalid_never_emit_speech(self) -> None:
        announce = AsyncMock()
        read = Mock(return_value=LolRead("unavailable"))
        monitor = LolCompanion(read, LolCompanionPolicy(), announce, Mock())
        monitor.enabled = True
        await monitor.poll()
        read.return_value = LolRead("invalid")
        await monitor.poll()
        announce.assert_not_awaited()

    async def test_stop_during_read_discards_late_data_and_can_restart(self) -> None:
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()

        def read() -> LolRead:
            entered.set()
            release.wait(2)
            finished.set()
            return LolRead("ready", game())

        announce = AsyncMock(return_value=True)
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
            announce.assert_not_awaited()
            publish.assert_not_called()
            await monitor.toggle()
            async with asyncio.timeout(1):
                while not announce.await_count:
                    await asyncio.sleep(.001)
        finally:
            release.set()
            await monitor.close()

    async def test_reference_expires_on_disconnect_timeout_and_disable(self) -> None:
        monitor = LolCompanion(lambda: LolRead("ready", game()), LolCompanionPolicy(),
                               AsyncMock(return_value=False), Mock())
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

    async def test_engine_toggle_and_application_shutdown_own_monitor(self) -> None:
        app = BoxAgentApplication(lambda event: None, Mock(), None)
        monitor = LolCompanion(lambda: LolRead("unavailable"), LolCompanionPolicy(),
                               AsyncMock(), lambda status, text: app.emit(
                                   "lol.companion", lol_companion_enabled=status != "off"))
        app.lol_companion = monitor
        server = EngineServer.__new__(EngineServer)
        server.application = app
        self.assertEqual(await server.dispatch("toggle_lol_companion", {}), {"enabled": True})
        self.assertTrue(app.state.lol_companion_enabled)
        await app.close()
        self.assertFalse(app.state.lol_companion_enabled)
        self.assertIsNone(monitor.task)

    async def test_interaction_does_not_reconnect_or_interrupt_user_task(self) -> None:
        events = StateEvents(lambda event: None)
        interaction = InteractionService(events, None, None)
        self.assertFalse(await interaction.announce_lol("facts", "instruction"))
        voice = Mock(announce_lol=AsyncMock(return_value=True))
        interaction.voice = voice
        interaction.task = asyncio.create_task(asyncio.sleep(60))
        try:
            self.assertTrue(await interaction.announce_lol("facts", "instruction"))
            events.state.task = "running"
            self.assertFalse(await interaction.announce_lol("facts", "instruction"))
            self.assertEqual(voice.announce_lol.await_count, 1)
        finally:
            interaction.task.cancel()
            await asyncio.gather(interaction.task, return_exceptions=True)

    async def test_realtime_uses_expiring_reference_and_blocks_tools(self) -> None:
        from boxagent.infrastructure.runtimes.qwen.realtime import QwenRealtimeSession
        voice = QwenRealtimeSession.__new__(QwenRealtimeSession)
        voice.delivery_lock = asyncio.Lock()
        voice._delivery_busy = Mock(return_value=False)
        voice.expire_reference = AsyncMock()
        voice.install_reference = AsyncMock()
        voice._create_response = AsyncMock()
        self.assertTrue(await voice.announce_lol("facts", "be brief"))
        voice.install_reference.assert_awaited_once()
        self.assertEqual(voice.install_reference.call_args.args[1], "lol_companion")
        voice._create_response.assert_awaited_with({"kind": "lol_companion"})
        voice.response_id = "lol-response"
        voice.response_context = {"lol-response": {"kind": "lol_companion"}}
        voice.cancelled_responses = set()
        voice.trace = Mock()
        self.assertFalse(voice._dispatch_tool_call({"call_id": "unsafe", "name": "run_task"}))
        voice._delivery_busy.return_value = True
        self.assertFalse(await voice.announce_lol("facts", "be brief"))
        self.assertEqual(voice._create_response.await_count, 1)

    async def test_game_reference_is_deleted_before_user_response_and_late_input_wins(self) -> None:
        from boxagent.infrastructure.runtimes.qwen.realtime import QwenRealtimeSession
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
        self.assertTrue(await voice.announce_lol("current health 200", "be brief"))
        self.assertEqual(voice.reference_item, "game-reference")
        self.assertEqual(sent[0]["item"]["role"], "user")
        self.assertIn("系统自动触发", sent[0]["item"]["content"][0]["text"])
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
        self.assertFalse(await voice.announce_lol("old health 200", "be brief"))
        self.assertFalse(any(row["type"] == "response.create" for row in sent))
        await voice.respond_to_query("", query_id="new-user")
        self.assertIsNone(voice.reference_item)
