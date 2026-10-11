"""与图形框架无关的拖动采样；以点每秒表达速度。"""

from dataclasses import dataclass


@dataclass
class DragMotion:
    start: tuple[float, float]
    previous: tuple[float, float]
    at: float
    dragging: bool = False

    @classmethod
    def begin(cls, point, now):
        return cls(tuple(point), tuple(point), now)

    def move(self, point, now):
        dx, dy = point[0] - self.start[0], point[1] - self.start[1]
        if not self.dragging and abs(dx) + abs(dy) <= 4:
            return None
        dt = max(1 / 120, now - self.at)
        velocity = tuple(max(-2400, min(2400, (value - old) / dt))
                         for value, old in zip(point, self.previous))
        kind = "drag-move" if self.dragging else "drag-start"
        self.dragging = True
        self.previous, self.at = tuple(point), now
        return kind, (dx, dy), velocity
