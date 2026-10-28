"""Real camera application/socket for UI tests; excludes all cloud services."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from boxagent.application.assistant import BoxAgentApplication
from boxagent.application.camera import CameraInteractionService
from boxagent.interfaces.engine.server import EngineServer


class FixtureVoice:
    """Deterministic transport for UI tests; never claims to run a real VLM."""
    def __init__(self, _handle_tool, emit, **_options):
        self.emit = emit
        self.ready, self.done = asyncio.Event(), asyncio.Event()
        self.frames_received = 0

    async def run(self):
        self.ready.set()
        self.emit('voice.ready', voice='ready')
        await self.done.wait()

    async def feed_image(self, image):
        assert image.startswith('/9j/')
        self.frames_received += 1
        return self.frames_received > 1

    async def submit_text(self, text):
        self.emit('front.assistant_text', assistant_text='测试通道回复：' + text)

    async def stop(self):
        self.done.set()


def application(publish):
    app = BoxAgentApplication(publish, None, FixtureVoice)
    app.camera = CameraInteractionService(app.events, app.interaction_service)
    return app


asyncio.run(EngineServer(sys.argv[1], application).run())
