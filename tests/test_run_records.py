import json
import tempfile
import unittest
from pathlib import Path

from boxagent.interfaces.macos.run_records import SessionRunRecords


class SessionRunRecordsTests(unittest.TestCase):
    def test_session_view_excludes_other_sessions_and_latest_is_scoped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "runs"
            older = self._run(root, "2026-10-06", "120000-oldoldold001",
                              "ses_current", 10)
            newest = self._run(root, "2026-10-07", "120000-newnewnew002",
                               "ses_current", 20)
            other = self._run(root, "2026-10-07", "130000-otherother03",
                              "ses_other", 30)
            records = SessionRunRecords(root)

            self.assertEqual(records.directories("ses_current"), [older, newest])
            self.assertEqual(records.latest("ses_current"), newest)
            view = records.materialize_view("ses_current")
            linked = sorted(item.resolve() for item in view.iterdir())

            self.assertEqual(linked, sorted([older, newest]))
            self.assertNotIn(other, linked)

    def test_empty_new_session_gets_an_empty_view_not_global_latest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "runs"
            self._run(root, "2026-10-07", "130000-otherother03",
                      "ses_other", 30)
            records = SessionRunRecords(root)

            view = records.materialize_view("ses_new")

            self.assertTrue(view.is_dir())
            self.assertEqual(list(view.iterdir()), [])
            self.assertIsNone(records.latest("ses_new"))

    @staticmethod
    def _run(root, date, name, session_id, started_at):
        target = root / date / name
        target.mkdir(parents=True)
        (target / "manifest.json").write_text(json.dumps({
            "session_id": session_id,
            "started_at": started_at,
        }), encoding="utf-8")
        return target.resolve()


if __name__ == "__main__":
    unittest.main()
