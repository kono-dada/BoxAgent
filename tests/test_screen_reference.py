import time
from unittest.mock import patch

from boxagent.bootstrap.engine import current_screen_reference
from boxagent.core.states import Snapshot


def test_screen_reference_requires_fresh_enabled_same_window():
    state = Snapshot(context_enabled=True, context_text="当前是计算器",
                     context_app="Calculator", context_window_id=12, context_at=time.time())
    with patch("boxagent.infrastructure.perception.focused_window", return_value={"app": "Calculator", "id": 12}):
        assert "计算器" in current_screen_reference(state)
        state.context_enabled = False
        assert not current_screen_reference(state)
        state.context_enabled = True
        state.context_at -= 46
        assert not current_screen_reference(state)
    state.context_at = time.time()
    with patch("boxagent.infrastructure.perception.focused_window", return_value={"app": "Calculator", "id": 13}):
        assert not current_screen_reference(state)
