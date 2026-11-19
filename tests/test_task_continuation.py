import json
import unittest
from unittest.mock import AsyncMock, Mock

from boxagent.infrastructure.context.intent import IntentReference
from boxagent.infrastructure.context.task_continuation import TaskContinuationReviewer


class TaskContinuationTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_explicit_complete_boolean_verdict_allows_dispatch(self) -> None:
        verdicts = [
            ({"authorized": True, "committed": True, "needs_more_input": False}, 1.0),
            ({"authorized": False, "committed": True, "needs_more_input": False}, 0.0),
            ({"authorized": True, "committed": False, "needs_more_input": False}, 0.0),
            ({"authorized": True, "committed": True, "needs_more_input": True}, 0.0),
            ({"authorized": "true", "committed": True, "needs_more_input": False}, 0.0),
            ({"authorized": True, "committed": True}, 0.0),
        ]
        for verdict, expected in verdicts:
            with self.subTest(verdict=verdict):
                post = Mock(return_value={"choices": [{"message": {"content": json.dumps(verdict)}}]})
                reviewer = TaskContinuationReviewer(api_key="test", base_url="https://api.deepseek.com/v1",
                                                    model="deepseek-flash", post=post)
                result = await reviewer("回个很可爱", "我这就去回", context="待补充任务")
                self.assertEqual(result["p_delegation"], expected)
                payload = post.call_args.args[1]
                self.assertIn("回个很可爱", payload["messages"][1]["content"])
                self.assertEqual(post.call_args.args[0], "https://api.deepseek.com/chat/completions")

    async def test_pending_review_is_scoped_and_keeps_existing_worker_for_other_turns(self) -> None:
        worker, reviewer = AsyncMock(), AsyncMock()
        reviewer.return_value = {"p_delegation": 1.0}
        worker._request.return_value = {"p_delegation": .01}
        intent = IntentReference(worker, pending_reviewer=reviewer)
        self.assertEqual(await intent.review_delegation("回答", "开工", context="<boxagent_pending_task>数据"),
                         {"p_delegation": 1.0})
        worker._request.assert_not_called()
        self.assertEqual(await intent.review_delegation("进度？", "正在运行", context="历史"),
                         {"p_delegation": .01})
        reviewer.assert_awaited_once()
        await intent.close()
        worker.close.assert_awaited_once()
