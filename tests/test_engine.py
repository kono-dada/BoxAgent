"""Engine IPC boundary and lifecycle tests."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from boxagent.core.states import Snapshot
from boxagent.interfaces.engine.client import EngineClient, EngineDisconnected
from boxagent.interfaces.engine.server import EngineServer
from boxagent.interfaces.engine.supervisor import EngineSupervisor


class FakeApplication:
    def __init__(self, publish):
        self.publish = publish
        self.state = Snapshot()
        self.last_result = {"status": "idle"}
        self.closed = False

    async def start(self):
        pass

    async def toggle_context(self):
        self.state.context_enabled = not self.state.context_enabled
        self.state.revision += 1
        self.publish({"type": "context.updated", "state": self.state.payload()})

    async def toggle_voice(self):
        self.state.voice = "ready" if self.state.voice == "off" else "off"
        return None

    async def submit_text(self, goal):
        return {"status": "accepted", "goal": goal}

    async def list_sessions(self, *, include_archived=False):
        return [{"session_id": "ses_1", "archived": include_archived}]

    async def create_session(self, title="新会话"):
        return {"session_id": "ses_new", "title": title}

    async def activate_session(self, session_id):
        return {"session_id": session_id}

    async def archive_session(self, session_id):
        return {"session_id": session_id, "archived": True}

    async def session_events(self, session_id, *, after_sequence=0, limit=None):
        return [{"session_id": session_id, "sequence": after_sequence + 1,
                 "limit": limit}]

    async def cancel_task(self):
        self.last_result = {"status": "cancelled"}

    async def answer_approval(self, allowed):
        self.state.approval = "" if allowed else "denied"

    async def remember_memory(self, content, *, source="explicit"):
        return {"status": "succeeded", "content": content, "source": source}

    async def recall_memory(self, query, *, top_k=5):
        return {"query": query, "top_k": top_k}

    async def forget_memory(self, memory_ids):
        return {"status": "succeeded", "memory_ids": memory_ids}

    async def memory_snapshot(self, **arguments):
        return {"arguments": arguments, "nodes": [], "edges": []}

    async def delete_memory_node(self, memory_id):
        return {"status": "succeeded", "memory_id": memory_id}

    async def list_skills(self):
        return [{"skill_id": "desktop-assistant", "enabled": True}]

    async def create_skill(self, **params):
        return {**params, "source": "user", "enabled": True}

    async def update_skill(self, **params):
        return {**params, "source": "user", "enabled": True}

    async def set_skill_enabled(self, skill_id, enabled):
        return {"skill_id": skill_id, "enabled": enabled}

    async def delete_skill(self, skill_id):
        return {"status": "succeeded", "skill_id": skill_id}

    async def list_notifications(self, *, pending_only=True):
        return [{"notification_id": "ntf_1", "pending": pending_only}]

    async def acknowledge_notification(self, notification_id, channel,
                                       receipt="presented"):
        return {"notification_id": notification_id, "channel": channel,
                "receipt": receipt, "status": "delivered"}

    async def close(self):
        self.closed = True


class EngineBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.socket_path = Path(self.temporary.name) / "engine.sock"
        self.application = None

        def factory(publish):
            self.application = FakeApplication(publish)
            return self.application

        self.server = EngineServer(self.socket_path, factory)
        await self.server.start()
        self.events = []
        self.client = EngineClient(self.socket_path, self.events.append)
        await self.client.connect()
        await asyncio.sleep(0)

    async def asyncTearDown(self):
        await self.client.close()
        await self.server.close()
        self.temporary.cleanup()

    async def test_commands_cross_process_contract_and_events_return_state(self):
        self.assertFalse(self.application.state.context_enabled)
        result = await self.client.request("submit_text", {"goal": "打开音乐"})
        self.assertEqual(result, {"status": "accepted", "goal": "打开音乐"})
        await self.client.request("toggle_context")
        await asyncio.sleep(0)
        state = [event for event in self.events if event.get("type") == "context.updated"][-1]
        self.assertTrue(state["state"]["context_enabled"])

    async def test_session_commands_cross_process_contract(self):
        created = await self.client.request("create_session", {"title": "游戏"})
        sessions = await self.client.request("list_sessions")
        events = await self.client.request("session_events", {
            "session_id": created["session_id"], "after_sequence": 3, "limit": 5})
        self.assertEqual(created["title"], "游戏")
        self.assertEqual(sessions[0]["session_id"], "ses_1")
        self.assertEqual(events[0]["sequence"], 4)

    async def test_skill_commands_cross_process_contract(self):
        created = await self.client.request("create_skill", {
            "skill_id": "music-helper", "name": "Music Helper",
            "description": "控制音乐", "instructions": "播放音乐。"})
        disabled = await self.client.request("set_skill_enabled", {
            "skill_id": "music-helper", "enabled": False})
        listed = await self.client.request("list_skills")
        deleted = await self.client.request(
            "delete_skill", {"skill_id": "music-helper"})
        self.assertEqual(created["source"], "user")
        self.assertFalse(disabled["enabled"])
        self.assertEqual(listed[0]["skill_id"], "desktop-assistant")
        self.assertEqual(deleted["status"], "succeeded")

    async def test_notification_commands_cross_process_contract(self):
        listed = await self.client.request(
            "list_notifications", {"pending_only": True})
        acknowledged = await self.client.request("acknowledge_notification", {
            "notification_id": "ntf_1", "channel": "system",
            "receipt": "submitted"})
        self.assertTrue(listed[0]["pending"])
        self.assertEqual(acknowledged["status"], "delivered")

    async def test_unknown_command_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "未知 Engine 方法"):
            await self.client.request("erase_everything")

    async def test_disconnect_fails_new_requests(self):
        await self.client.close()
        with self.assertRaises(EngineDisconnected):
            await self.client.request("health")

    async def test_second_server_cannot_replace_live_engine_socket(self):
        other = EngineServer(self.socket_path, lambda publish: FakeApplication(publish))
        with self.assertRaisesRegex(RuntimeError, "另一个 BoxAgent Engine"):
            await other.start()
        self.assertEqual((await self.client.request("health"))["status"], "ready")

    @patch("boxagent.interfaces.engine.supervisor.asyncio.create_subprocess_exec",
           new_callable=AsyncMock)
    async def test_supervisor_starts_the_public_engine_entrypoint(self, spawn):
        process = Mock(returncode=0)
        spawn.return_value = process
        supervisor = EngineSupervisor(
            root=Path(self.temporary.name), socket_path=self.socket_path,
            log_path=Path(self.temporary.name) / "engine.log",
            task_provider="deepseek", context_size=960)

        await supervisor.start()

        command = spawn.await_args.args
        self.assertEqual(command[1:3], ("-m", "boxagent.entrypoints.engine"))
        self.assertIn("--task-provider", command)
        self.assertEqual(command[command.index("--context-interval") + 1], "0")
        supervisor.log_stream.close()
