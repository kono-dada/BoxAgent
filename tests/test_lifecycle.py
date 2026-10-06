"""Application lifecycle resource ownership contracts."""

import unittest
from unittest.mock import AsyncMock, Mock

from boxagent.application.assistant import BoxAgentApplication
from boxagent.agent.runtime.models import RuntimeRestoreContext


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_conversation_and_runtime_start_once_and_close_is_idempotent(self):
        conversation = Mock(start=AsyncMock(return_value=None))
        runtime = Mock(start=AsyncMock(), close=AsyncMock())
        application = BoxAgentApplication(
            Mock(), Mock(), None, memory_backend=None,
            conversation_service=conversation,
            runtime_resources=(runtime,))
        await application.start()
        await application.start()
        conversation.start.assert_awaited_once()
        runtime.start.assert_awaited_once()

        application.perception_service.close = AsyncMock()
        application.execution_service.close = AsyncMock()
        application.interaction_service.close = AsyncMock()
        application.memory_service.close = AsyncMock()
        await application.close()
        await application.close()
        application.perception_service.close.assert_awaited_once()
        application.execution_service.close.assert_awaited_once()
        application.interaction_service.close.assert_awaited_once()
        application.memory_service.close.assert_awaited_once()
        runtime.close.assert_awaited_once()

    async def test_voice_connection_receives_current_session_native_history(self):
        conversation = Mock()
        conversation.store = object()
        conversation.start = AsyncMock(return_value=None)
        conversation.active_session = AsyncMock(return_value=Mock(session_id="ses_1"))
        conversation.runtime_context = AsyncMock(return_value=(None, ("message",)))
        created = {}

        class Voice:
            async def run(self):
                return None

            async def stop(self):
                return None

        def factory(_handle, _emit, **kwargs):
            created.update(kwargs)
            return Voice()

        application = BoxAgentApplication(
            Mock(), Mock(), factory, conversation_service=conversation,
            voice_history_builder=lambda *, checkpoint, turns: RuntimeRestoreContext(
                checkpoint="", messages=("NATIVE:" + turns[0],)))
        await application.toggle_voice()
        await application.voice_task

        self.assertEqual(created["conversation_history"], ("NATIVE:message",))
        self.assertIn("conversation_event", created)

    async def test_skill_mutation_refreshes_live_runtime_projection(self):
        record = Mock(payload=Mock(return_value={
            "skill_id": "music-helper", "enabled": True}))
        skills = Mock(create=Mock(return_value=record))
        runtime = Mock(sync_skills=AsyncMock())
        application = BoxAgentApplication(
            Mock(), Mock(), None, memory_backend=None,
            skill_service=skills, runtime_resources=(runtime,))

        result = await application.create_skill(
            skill_id="music-helper", name="Music Helper",
            description="控制音乐", instructions="播放音乐。")

        self.assertEqual(result["skill_id"], "music-helper")
        skills.create.assert_called_once()
        runtime.sync_skills.assert_awaited_once()
