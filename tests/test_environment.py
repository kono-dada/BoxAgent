"""Trusted environment context capture and projection."""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from boxagent.agent.harness.request import HarnessInput
from boxagent.agent.harness.request_builder import RuntimeRequestBuilder
from boxagent.domain.environment import EnvironmentContext
from boxagent.infrastructure.environment import LocalSystemEnvironmentProvider


class EnvironmentContextTests(unittest.TestCase):
    def test_local_capture_is_timezone_aware_and_privacy_bounded(self):
        fixed = datetime(2026, 10, 6, 15, 30,
                         tzinfo=timezone(timedelta(hours=8)))
        provider = LocalSystemEnvironmentProvider(clock=lambda: fixed)
        with patch.dict("os.environ", {"TZ": "Asia/Shanghai"}, clear=True):
            context = provider.capture()

        self.assertEqual(context.captured_at, "2026-10-06T15:30:00+08:00")
        self.assertEqual(context.timezone, "Asia/Shanghai")
        self.assertEqual(context.weekday, "星期二")
        self.assertTrue({"hostname", "username", "location", "ip", "locale",
                         "os_name", "device_class", "architecture"}.isdisjoint(
            context.payload()))

    def test_environment_is_dynamic_evidence_not_stable_instructions_or_query(self):
        environment = EnvironmentContext(
            captured_at="2026-10-06T15:30:00+08:00",
            timezone="Asia/Shanghai", weekday="星期二")
        request = RuntimeRequestBuilder().prepare(HarnessInput(
            goal="现在几点", environment=environment))

        self.assertEqual(request.query, "现在几点")
        self.assertNotIn("2026-10-06", request.developer_instructions)
        self.assertIn("[BoxAgent可信运行环境]", request.evidence_context)
        self.assertIn("2026-10-06T15:30:00+08:00", request.evidence_context)
        self.assertIn("Asia/Shanghai", request.evidence_context)
        self.assertIn("星期二", request.evidence_context)


if __name__ == "__main__":
    unittest.main()
