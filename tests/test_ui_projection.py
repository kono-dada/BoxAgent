"""Pure native-view projection contracts."""

import unittest

from boxagent.core.states import Snapshot
from boxagent.interfaces.macos.state import mode_text, transcript_sections


class UIProjectionTests(unittest.TestCase):
    def test_voice_and_task_status_are_projected_without_overwriting_each_other(self):
        state = Snapshot(task="running", voice="ready", speaking=True,
                         task_text="正在点击", assistant_text="我还在处理")
        self.assertEqual(mode_text(state), "执行中 · 回复中")
        self.assertIn(("BoxAgent", "我还在处理"), transcript_sections(state, now=10))
        self.assertIn(("任务", "正在点击"), transcript_sections(state, now=10))

    def test_approval_takes_display_priority_over_task_progress(self):
        state = Snapshot(task="awaiting_approval", approval="允许操作浏览器？",
                         task_text="等待中")
        sections = transcript_sections(state, now=10)
        self.assertIn(("需要授权", "允许操作浏览器？"), sections)
        self.assertNotIn(("任务", "等待中"), sections)
