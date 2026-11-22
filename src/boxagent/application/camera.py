"""Opt-in frame delivery to the current conversation. Never stores pixels."""

import asyncio
import secrets
import time
from typing import Callable, Mapping, TypedDict

from boxagent.core.events import StateEvents
from boxagent.domain.perception.camera import CameraFeed, CameraFrame, CameraRuntime


class CameraStartResult(TypedDict):
    token: str
    revision: int


class CameraInteractionService:
    def __init__(self, events: StateEvents, runtime: CameraRuntime, *,
                 clock: Callable[[], float] = time.time,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self.events, self.runtime = events, runtime
        self.clock, self.monotonic = clock, monotonic
        self.token = ""
        self.feed: CameraFeed | None = None
        self.sequence = -1
        self.captured_at = -1.0
        self.last_received = 0.0
        self.last_sent = float("-inf")
        self.sending = False
        self.watchdog: asyncio.Task[None] | None = None
        self.lock = asyncio.Lock()

    async def start(self) -> CameraStartResult:
        async with self.lock:
            await self.stop()
            self.events.emit("camera.starting", camera_status="starting", camera_error="",
                             camera_frame_at=0, camera_frames_sent=0)
            try:
                self.feed = await self.runtime.open_camera()
            except BaseException:
                self.events.emit("camera.stopped", camera_status="error",
                                 camera_error="千问视频通话连接失败，请检查语音连接和 API 配置")
                raise
            self.token = secrets.token_urlsafe(24)
            self.sequence, self.captured_at = -1, -1.0
            self.last_received, self.last_sent = self.monotonic(), float("-inf")
            self.sending = False
            self.watchdog = asyncio.create_task(self._watch(), name="camera-watchdog")
            self.events.emit("camera.waiting", camera_status="waiting")
            return {"token": self.token, "revision": self.events.state.revision}

    async def send_frame(self, payload: Mapping[str, object]) -> dict[str, bool]:
        token, feed = self.token, self.feed
        if not token or payload.get("token") != token or feed is None:
            raise ValueError("Camera session expired; restart the camera")
        frame = CameraFrame.parse(payload)
        if not -1 <= self.clock() - frame.captured_at <= 3:
            raise ValueError("Stale camera frame")
        if frame.sequence <= self.sequence or frame.captured_at <= self.captured_at:
            raise ValueError("Out-of-order camera frame")
        if not self.runtime.camera_connected(feed):
            await self.stop(token, error="语音会话已切换或断开，请重新开启视频通话")
            raise ConnectionError("Camera conversation disconnected")
        self.sequence, self.captured_at = frame.sequence, frame.captured_at
        now = self.monotonic()
        self.last_received = now
        if self.sending or now - self.last_sent < 1:
            return {"sent": False}
        # Reserve before awaiting: concurrent clients cannot create a frame queue.
        self.last_sent = now
        self.sending = True
        try:
            sent = await asyncio.wait_for(feed.feed_image(frame.image), timeout=5)
        except Exception:
            await self.stop(token, error="画面发送失败，摄像头已关闭，请重新开启")
            raise
        finally:
            if token == self.token:
                self.sending = False
        if token != self.token:
            return {"sent": False}
        if sent:
            self.events.emit("camera.frame_sent", camera_status="active",
                             camera_frame_at=frame.captured_at,
                             camera_frames_sent=self.events.state.camera_frames_sent + 1)
        else:
            self.events.emit("camera.waiting", camera_status="waiting")
        return {"sent": sent}

    async def stop(self, token: str | None = None, *, error: str = "") -> dict[str, bool]:
        if token is not None and token != self.token:
            return {"stopped": False}
        self.token = ""
        feed, self.feed = self.feed, None
        task, self.watchdog = self.watchdog, None
        if task and task is not asyncio.current_task():
            task.cancel()
        self.events.emit("camera.stopped", camera_status="error" if error else "off",
                         camera_error=error, camera_frame_at=0)
        if feed is not None:
            await self.runtime.close_camera(feed)
        return {"stopped": True}

    async def _watch(self) -> None:
        try:
            while self.token:
                await asyncio.sleep(1)
                if self.feed is None or not self.runtime.camera_connected(self.feed):
                    await self.stop(error="语音连接已结束，摄像头已关闭")
                elif self.monotonic() - self.last_received > 8:
                    await self.stop(error="摄像头画面已中断，摄像头已关闭")
        except asyncio.CancelledError:
            pass

    async def close(self) -> None:
        task = self.watchdog
        await self.stop()
        if task:
            await asyncio.gather(task, return_exceptions=True)
