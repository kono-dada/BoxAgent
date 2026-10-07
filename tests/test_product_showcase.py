"""Contracts for the reusable product-showcase conversations."""

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCENARIO_DIR = ROOT / "examples/product-showcase"
REQUIRED_CAPABILITIES = {
    "persona",
    "multi_turn_context",
    "automatic_memory",
    "user_profile",
    "session_management",
    "cross_session_recall",
    "checkpoint_recovery",
    "runtime_routing",
    "background_task",
    "desktop_action",
    "parallel_chat",
    "proactive_notification",
    "history_ordering",
    "evidence_verification",
    "full_duplex_voice",
    "barge_in",
    "environment_context",
    "safety_approval",
    "memory_management",
    "skill_management",
    "persona_management",
    "skill_creation",
}
UNSAFE_LIVE_TERMS = {
    "删除文件", "付款", "支付", "发邮件", "发送消息", "发布", "提交表单",
    "修改密码", "创建账号", "授权", "上传文件",
}


def load_scenarios():
    return [json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(SCENARIO_DIR.glob("*.json"))]


class ProductShowcaseScenarioTests(unittest.TestCase):
    def test_scenarios_are_long_structured_and_have_assertions(self):
        scenarios = load_scenarios()
        self.assertGreaterEqual(len(scenarios), 3)
        self.assertEqual(len({item["scenario_id"] for item in scenarios}),
                         len(scenarios))
        for scenario in scenarios:
            self.assertEqual(scenario["schema_version"], 1)
            self.assertIn(scenario["execution"], {"live_safe", "scripted"})
            self.assertGreaterEqual(len(scenario["turns"]), 6)
            self.assertGreaterEqual(len(scenario["assertions"]), 3)
            self.assertTrue(set(scenario["capabilities"]))
            for turn in scenario["turns"]:
                self.assertIn(turn["role"], {"user", "assistant", "system"})
                self.assertTrue(turn["session"].strip())
                self.assertTrue(turn["text"].strip())

    def test_capability_union_covers_the_v0_product_surface(self):
        covered = {capability for scenario in load_scenarios()
                   for capability in scenario["capabilities"]}
        self.assertEqual(REQUIRED_CAPABILITIES - covered, set())

    def test_live_scenarios_only_contain_safe_demo_actions(self):
        for scenario in load_scenarios():
            if scenario["execution"] != "live_safe":
                continue
            text = "\n".join(turn["text"] for turn in scenario["turns"])
            self.assertFalse(UNSAFE_LIVE_TERMS & {
                term for term in UNSAFE_LIVE_TERMS if term in text})
            task_scenario = "desktop_action" in scenario["capabilities"]
            if task_scenario:
                self.assertIn("音乐", text)
                self.assertIn("知乎", text)
                self.assertIn("不要点赞、关注或评论", text)
                self.assertIn("Skill", text)


if __name__ == "__main__":
    unittest.main()
