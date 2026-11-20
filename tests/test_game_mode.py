import tempfile
import unittest
from pathlib import Path

from boxagent.application.assistant import BoxAgentApplication
from boxagent.application.memory import DisabledMemoryModule
from boxagent.application.turn_preparation import TurnPreparer
from boxagent.domain.conversation import ConversationService
from boxagent.infrastructure.persistence import JsonlSessionStore


class GameModeTests(unittest.IsolatedAsyncioTestCase):
    async def test_mode_is_explicit_validated_and_does_not_enable_microphone(self) -> None:
        app = BoxAgentApplication(lambda _: None, None, None)
        try:
            self.assertFalse(app.state.game_companion_enabled)
            with self.assertRaisesRegex(ValueError, "尚未启用"):
                app.set_game_companion_mode(True)
            app.events.emit("game.companion_available", game_companion_available=True)
            self.assertEqual(app.set_game_companion_mode(True), {"status": "succeeded", "enabled": True})
            self.assertTrue(app.state.game_companion_enabled)
            self.assertFalse(app.interaction_service.voice_requested)
            self.assertIsNone(app.voice)
            self.assertEqual(await app.handle_tool("set_game_companion_mode", '{"enabled":false}'),
                             {"status": "succeeded", "enabled": False})
            self.assertFalse(app.state.game_companion_enabled)
            self.assertTrue(app.interaction_service.game_speech_paused)
            invalid = await app.handle_tool("set_game_companion_mode", '{"enabled":"true"}')
            self.assertEqual(invalid["status"], "failed")
            self.assertFalse(app.state.game_companion_enabled)
        finally:
            await app.close()

    async def test_each_voice_turn_gets_mode_context_and_disabling_removes_it(self) -> None:
        delivered: list[str] = []

        class Voice:
            async def respond_to_query(self, packet: str, *, query_id: str) -> None:
                delivered.append(packet)

        with tempfile.TemporaryDirectory() as directory:
            preparer = TurnPreparer(DisabledMemoryModule())
            app = BoxAgentApplication(lambda _: None, None, None,
                turn_preparer=preparer,
                conversation_service=ConversationService(JsonlSessionStore(Path(directory))))
            try:
                await app.start()
                app.events.emit("game.companion_available", game_companion_available=True)
                app.set_game_companion_mode(True)
                service = app.interaction_service
                service.voice = Voice()
                for query in ("好吓人", "这波帅吧"):
                    await service._conversation_event("user_final", {"transcript": query})
                    self.assertIn("<boxagent_companion_mode>", delivered[-1])
                    self.assertIn("游戏搭子", delivered[-1])
                    self.assertIn("微信", delivered[-1])
                app.set_game_companion_mode(False)
                await service._conversation_event("user_final", {"transcript": "聊聊别的"})
                self.assertNotIn("<boxagent_companion_mode>", delivered[-1])
            finally:
                await app.close()
                await preparer.close()
