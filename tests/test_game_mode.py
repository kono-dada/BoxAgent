import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from boxagent.application.assistant import BoxAgentApplication
from boxagent.application.memory import DisabledMemoryModule
from boxagent.application.turn_preparation import TurnPreparer
from boxagent.domain.conversation import ConversationService
from boxagent.infrastructure.persistence import JsonlSessionStore


class GameModeTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_lol_voice_command_switches_before_model_reply(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = BoxAgentApplication(lambda _: None, None, None,
                conversation_service=ConversationService(JsonlSessionStore(Path(directory))))
            try:
                await app.start()
                service = app.interaction_service
                service.voice = voice = AsyncMock()
                service.connection_microphone = service.voice_requested = True
                service.handle_tool = tool = AsyncMock(return_value={"status": "succeeded"})
                for phrase, enabled in (
                    ("进入 LoL 陪玩模式。", True),
                    ("进入陪玩模式，撸啊撸的。", True),
                    ("关闭英雄联盟陪玩。", False),
                ):
                    await service._conversation_event("user_final", {"transcript": phrase})
                    self.assertEqual(tool.await_args.args[0], "set_game_companion_mode")
                    self.assertIn(f'"enabled": {str(enabled).lower()}', tool.await_args.args[1])
                    self.assertIn("宿主已处理", voice.respond_to_query.await_args.args[0])
                self.assertEqual(tool.await_count, 3)
                await service._conversation_event(
                    "user_final", {"transcript": "怎么进入 LoL 陪玩模式？"}
                )
                self.assertEqual(tool.await_count, 3)
            finally:
                await app.close()

    async def test_mode_is_explicit_validated_and_does_not_enable_microphone(self) -> None:
        app = BoxAgentApplication(lambda _: None, None, None)
        try:
            self.assertFalse(app.state.game_companion_enabled)
            with self.assertRaisesRegex(ValueError, "尚未启用"):
                app.set_game_companion_mode(True)
            app.events.emit("game.companion_available", game_companion_available=True)
            self.assertEqual(
                app.set_game_companion_mode(True), {"status": "succeeded", "enabled": True}
            )
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
