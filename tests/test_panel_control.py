"""Native panel boundary: ownership, failure safety and queued UI dispatch."""
import concurrent.futures
import json
import queue
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from boxagent.interfaces.macos.panel_control import PanelControl


class PanelControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.soul = self.root / 'SOUL.md'
        self.soul.write_text('名字：旧名字\n\n保持温柔。\n')
        self.control = PanelControl.__new__(PanelControl)
        self.control.pending = queue.SimpleQueue()
        self.control.preview = None
        self.control.preview_directory = None
        self.control.owner = SimpleNamespace(
            editable_soul_file=self.soul, soul_file=self.soul,
            reloadPersona=Mock(), _openDirectory=Mock(), data_dir=self.root,
            pet_catalog=SimpleNamespace(vrm_manifests=lambda: []))

    def tearDown(self):
        self.temp.cleanup()

    def test_invalid_persona_name_does_not_replace_existing_file(self):
        original = self.soul.read_bytes()
        with self.assertRaises(ValueError):
            self.control.dispatch('desktop_save_persona', {'name':' ', 'content':'overwrite'})
        self.assertEqual(self.soul.read_bytes(), original)
        self.control.owner.reloadPersona.assert_not_called()

    def test_persona_save_uses_configured_target_and_applies_name(self):
        result = self.control.dispatch('desktop_save_persona', {'name':'小芽','content':'名字：旧名字\n\n保持温柔。'})
        self.assertEqual(result['name'], '小芽')
        self.assertIn('保持温柔', self.soul.read_text())
        self.control.owner.reloadPersona.assert_called_once()

    def test_avatar_path_outside_catalog_is_rejected_without_factory_call(self):
        self.control.owner.useLocalVrm_ = Mock()
        with self.assertRaises(ValueError):
            self.control.dispatch('desktop_select_avatar', {'id':'/tmp/untrusted'})
        self.control.owner.useLocalVrm_.assert_not_called()

    def test_open_run_requires_matching_session_manifest(self):
        directory=self.root/'runs'/'2026-10-12'/'110000-task-one'
        directory.mkdir(parents=True)
        (directory/'manifest.json').write_text(json.dumps({'session_id':'ses_one','started_at':1}))
        with self.assertRaises(ValueError):
            self.control.dispatch('desktop_open_run', {'session_id':'ses_two','id':str(directory)})
        self.control.owner._openDirectory.assert_not_called()
        self.control.dispatch('desktop_open_run', {'session_id':'ses_one','id':str(directory)})
        self.control.owner._openDirectory.assert_called_once_with(directory)

    def test_canceled_transport_does_not_execute_late_mutation(self):
        future=concurrent.futures.Future()
        future.cancel()
        self.control.pending.put(('desktop_save_persona',{'name':'late'},future))
        self.control.pump()
        self.assertIn('旧名字',self.soul.read_text())

    def test_unknown_method_reports_error_to_waiting_transport(self):
        future=concurrent.futures.Future()
        self.control.pending.put(('arbitrary_command',{},future))
        self.control.pump()
        with self.assertRaisesRegex(ValueError,'未知桌面控制'):
            future.result()
