"""Real Engine supervision with an isolated application; no runtime/model calls."""
import asyncio
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
from boxagent.interfaces.desktop.engine_bridge import EngineBridge
from boxagent.interfaces.engine.supervisor import EngineSupervisor


def test_bridge_restarts_isolated_engine_and_shutdown_closes_process(tmp_path: Path):
    with tempfile.TemporaryDirectory(prefix="ba-engine-", dir="/tmp") as directory:
        asyncio.run(check_bridge(Path(directory)))


async def check_bridge(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    server_code = '''
import asyncio, runpy, sys
from pathlib import Path
from boxagent.interfaces.engine.server import EngineServer
FakeApplication = runpy.run_path("tests/test_engine.py")["FakeApplication"]
async def main():
    server = EngineServer(Path(sys.argv[1]), FakeApplication)
    await server.start()
    await server.stop_requested.wait()
    await server.close()
asyncio.run(main())
'''
    async def start(supervisor):
        supervisor.process = await asyncio.create_subprocess_exec(
            sys.executable, "-c", server_code, str(supervisor.socket_path), cwd=root)
        return supervisor.process

    with patch.object(EngineSupervisor, "start", start):
        bridge = EngineBridge(root=root, data_dir=tmp_path, log_dir=tmp_path / "logs", task_provider="codex", watch=False)
        try:
            await asyncio.wait_for(asyncio.wrap_future(bridge.startup), 10)
            first = bridge.engine_pid
            assert first
            result = await asyncio.wrap_future(bridge.submit(bridge.request("submit_text", {"goal": "fixture"})))
            assert result["status"] == "accepted"
            await asyncio.wrap_future(bridge.submit(bridge._restart("fixture")))
            assert bridge.engine_pid != first
            process = bridge.supervisor.process
            await asyncio.to_thread(bridge.close)
            assert process.returncode is not None
            assert not bridge.thread.is_alive()
            assert not bridge.socket_path.exists()
        finally:
            if bridge.thread.is_alive():
                await asyncio.to_thread(bridge.close)
