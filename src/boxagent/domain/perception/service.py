"""Own the enable/disable lifecycle of the configured perception adapter."""

import asyncio


class PerceptionService:
    def __init__(self, events, observer=None):
        self.events = events
        self.observer = observer
        self.toggle_lock = asyncio.Lock()

    async def toggle(self):
        async with self.toggle_lock:
            if not self.observer:
                return
            if self.observer.task and not self.observer.task.done():
                await self.observer.stop()
                self.events.emit("context.paused", context_enabled=False,
                                 context_status="屏幕总结已关闭", context_text="")
            else:
                self.events.emit("context.enabled", context_enabled=True)
                self.observer.task = asyncio.create_task(self.observer.run())

    async def close(self):
        if self.observer:
            await self.observer.stop()
