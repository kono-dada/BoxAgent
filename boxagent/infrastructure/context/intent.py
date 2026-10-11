"""Small decision-only process, independent of the memory writer queue."""

import asyncio
from typing import Any, Protocol


class DecisionWorker(Protocol):
    async def _request(self, operation: str, **payload: Any) -> dict[str, Any]: ...
    async def close(self) -> None: ...


class IntentReference:
    def __init__(self, worker: DecisionWorker) -> None:
        self.worker = worker
        self.prewarm: asyncio.Task[dict[str, Any]] | None = None

    async def estimate(self, query: str) -> dict[str, Any]:
        return await self.worker._request("intent", query=query)

    async def start(self) -> None:
        self.prewarm = asyncio.create_task(self.worker._request("health"))
        self.prewarm.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)

    async def close(self) -> None:
        if self.prewarm is not None:
            self.prewarm.cancel()
            await asyncio.gather(self.prewarm, return_exceptions=True)
        await self.worker.close()
