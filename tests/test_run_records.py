import json
import tempfile
import unittest
from pathlib import Path

from boxagent.interfaces.desktop.run_records import SessionRunRecords


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

    def test_unified_runs_work_without_legacy_root_and_deduplicate_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            unified = data / "sessions/ses_current/tasks/abc123abc123"
            unified.mkdir(parents=True)
            manifest = {"session_id": "ses_current", "task_id": "abc123abc123", "started_at": 10}
            (unified / "manifest.json").write_text(json.dumps(manifest))
            records = SessionRunRecords(data / "runs")
            self.assertEqual(records.latest("ses_current"), unified.resolve())
            legacy = self._run(data / "runs", "2026-10-12", "120000-abc123abc123", "ses_current", 10)
            (legacy / "manifest.json").write_text(json.dumps(manifest))
            self.assertEqual(records.directories("ses_current"), [unified.resolve()])
            view = records.materialize_view("ses_current")
            self.assertEqual([p.resolve() for p in view.iterdir()], [unified.resolve()])
            (data / "sessions/.rollback-v1").touch()
            self.assertEqual(records.directories("ses_current"), [legacy])

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
