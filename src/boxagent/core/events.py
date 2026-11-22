"""State-changing event publication with one revision owner."""

import time

from boxagent.core.states import Snapshot


class StateEvents:
    def __init__(self, publish):
        self.state = Snapshot()
        self.publish = publish

    def emit(self, kind: str, **changes):
        for key, value in changes.items():
            if not hasattr(self.state, key):
                raise ValueError(f"未知状态字段：{key}")
            setattr(self.state, key, value)
        self.state.revision += 1
        self.publish({"type": kind, "occurred_at": time.time(),
                      "state": self.state.payload()})
