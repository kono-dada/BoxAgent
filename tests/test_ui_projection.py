"""Pure native-view projection contracts."""

import unittest

from boxagent.core.states import Snapshot
from boxagent.interfaces.macos.state import (mode_text, recent_turn_events,
                                             transcript_sections)


class UIProjectionTests(unittest.TestCase):
    def test_voice_and_task_status_are_projected_without_overwriting_each_other(self):
        state = Snapshot(task="running", voice="ready", speaking=True,
                         task_text="正在点击", assistant_text="我还在处理")
        self.assertEqual(mode_text(state), "执行中 · 回复中")
        self.assertIn(("伙伴", "我还在处理"), transcript_sections(state, now=10))
        self.assertIn(("任务", "正在点击"), transcript_sections(state, now=10))

    def test_approval_takes_display_priority_over_task_progress(self):
        state = Snapshot(task="awaiting_approval", approval="允许操作浏览器？",
                         task_text="等待中")
        sections = transcript_sections(state, now=10)
        self.assertIn(("需要授权", "允许操作浏览器？"), sections)
        self.assertNotIn(("任务", "等待中"), sections)

    def test_history_renders_all_turns_in_event_order_and_hides_stale_task_footer(self):
        history = [
            {"sequence": 1, "type": "interaction.started",
             "interaction_id": "task", "occurred_at": 10},
            {"sequence": 2, "type": "message.final", "role": "user",
             "content": "播放音乐", "interaction_id": "task"},
            {"sequence": 3, "type": "message.final", "role": "assistant",
             "content": "已经开始播放。", "interaction_id": "task"},
            {"sequence": 4, "type": "interaction.finalized",
             "interaction_id": "task", "task_id": "task-1",
             "status": "succeeded", "occurred_at": 52},
            {"sequence": 5, "type": "interaction.started",
             "interaction_id": "chat", "occurred_at": 53},
            {"sequence": 6, "type": "message.final", "role": "user",
             "content": "不错啊，你挺厉害的", "interaction_id": "chat"},
            {"sequence": 7, "type": "message.final", "role": "assistant",
             "content": "谢谢夸奖！", "interaction_id": "chat"},
        ]
        state = Snapshot(
            task="succeeded", task_id="task-1", task_started_at=10,
            task_ended_at=52, task_text="已经开始播放。",
            user_text="不错啊，你挺厉害的", assistant_text="谢谢夸奖！")

        sections = transcript_sections(state, history=history, now=60)

        self.assertEqual(sections, [
            ("你", "播放音乐"),
            ("伙伴", "已经开始播放。"),
            ("状态", "执行已结束 · 用时 42 秒"),
            ("你", "不错啊，你挺厉害的"),
            ("伙伴", "谢谢夸奖！"),
        ])

    def test_streaming_reply_and_active_task_are_appended_after_history(self):
        history = [
            {"sequence": 1, "type": "message.final", "role": "user",
             "content": "打开音乐", "interaction_id": "task"},
        ]
        state = Snapshot(
            task="running", task_id="task-1", task_started_at=10,
            task_activity_at=10, task_text="正在点击播放按钮",
            user_text="打开音乐", assistant_text="我去看看")

        sections = transcript_sections(state, history=history, now=12)

        self.assertEqual(sections, [
            ("你", "打开音乐"),
            ("伙伴", "我去看看"),
            ("任务", "正在点击播放按钮"),
            ("状态", "已运行 2 秒"),
        ])

    def test_pending_text_is_projected_before_engine_accepts_submission(self):
        state = Snapshot(user_text="上一条消息", assistant_text="上一条回复")

        sections = transcript_sections(
            state, pending_user_text="刚按回车的消息", now=10)

        self.assertIn(("你", "刚按回车的消息"), sections)
        self.assertNotIn(("你", "上一条消息"), sections)

    def test_only_latest_thirty_complete_turns_are_projected(self):
        history = []
        for index in range(35):
            interaction_id = f"int-{index}"
            history.extend([
                {"sequence": index * 2 + 1, "type": "interaction.started",
                 "interaction_id": interaction_id},
                {"sequence": index * 2 + 2, "type": "message.final",
                 "interaction_id": interaction_id, "role": "user",
                 "content": f"turn-{index}"},
            ])

        visible = recent_turn_events(history)
        contents = [event.get("content") for event in visible
                    if event.get("type") == "message.final"]

        self.assertEqual(len(contents), 30)
        self.assertEqual(contents[0], "turn-5")
        self.assertEqual(contents[-1], "turn-34")
