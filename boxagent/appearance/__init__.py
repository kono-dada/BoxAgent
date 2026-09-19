"""形象替换点：原生宿主只依赖此接口，不认识精灵表。"""

from typing import Any, Protocol

from ..domain import Snapshot


class Appearance(Protocol):
    size: tuple[float, float]
    view: Any

    def present(self, snapshot: Snapshot, now: float, pointer: tuple[float, float] | None = None) -> None:
        """更新形象；不能调用语音或任务执行器。"""
        ...
