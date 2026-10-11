"""验证纯 VRM 目录、旧选择迁移和失败时保留当前配置。"""

import json
from pathlib import Path
import tempfile
import unittest

from boxagent.interfaces.macos.pets.catalog import PetCatalog


class VrmCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.bundled = self.root / "models"
        self.cache = self.root / "cache"
        self.default = self.make_model(self.bundled / "zome")
        self.catalog = PetCatalog(self.cache, default_pet=self.default, bundled_root=self.bundled)

    def make_model(self, directory):
        directory.mkdir(parents=True)
        (directory / "model.vrm").write_bytes(directory.name.encode())
        (directory / "pet.json").write_text(json.dumps({
            "type": "vrm", "version": 1, "displayName": directory.name,
            "model": "model.vrm", "actions": {}}))
        return directory

    def select_raw(self, value):
        (self.cache / "selected.json").write_text(json.dumps(value))

    def test_legacy_builtin_and_downloaded_choices_use_default_vrm(self):
        old = self.cache / "old-pet"
        old.mkdir()
        (old / "pet.json").write_text(json.dumps({"spritesheetPath": "sprite.webp"}))
        for saved in ({"builtin": True}, {"id": "old-pet"},
                      {"type": "vrm", "id": "old-pet"}):
            with self.subTest(saved=saved):
                self.select_raw(saved)
                self.assertEqual(self.catalog.current_directory(), self.default)
        self.assertEqual(self.catalog.vrm_manifests(), [self.default / "pet.json"])

    def test_local_vrm_selection_restores_and_missing_asset_is_reportable(self):
        local = self.make_model(self.cache / "local-test")
        self.catalog.commit(local)
        fresh = PetCatalog(self.cache, default_pet=self.default, bundled_root=self.bundled)
        self.assertEqual(fresh.current_directory(), local)
        (local / "model.vrm").unlink()
        self.assertEqual(fresh.current_directory(), local)
        (local / "pet.json").unlink()
        self.assertEqual(fresh.current_directory(), local)

    def test_non_vrm_or_invalid_asset_cannot_replace_current_selection(self):
        self.catalog.commit(self.default)
        before = (self.cache / "selected.json").read_bytes()
        local = self.make_model(self.cache / "local-bad")
        (local / "model.vrm").unlink()
        with self.assertRaises(ValueError):
            self.catalog.commit(local)
        (local / "pet.json").write_text(json.dumps({"spritesheetPath": "sprite.webp"}))
        with self.assertRaises(ValueError):
            self.catalog.commit(local)
        self.assertEqual((self.cache / "selected.json").read_bytes(), before)

    def test_corrupt_metadata_and_invalid_ids_do_not_enter_menu_or_restore(self):
        local = self.cache / "invalid"
        local.mkdir()
        (local / "pet.json").write_text("{broken")
        for saved in ({"type": "vrm", "id": "../secret"},
                      {"type": "vrm", "id": "invalid"}, [], None):
            self.select_raw(saved)
            self.assertEqual(self.catalog.current_directory(), self.default)
        self.assertEqual(self.catalog.vrm_manifests(), [self.default / "pet.json"])

    def test_symlink_and_outside_model_cannot_be_selected(self):
        outside = self.make_model(self.root / "outside")
        linked = self.cache / "linked"
        linked.symlink_to(outside, target_is_directory=True)
        for path in (outside, linked):
            with self.assertRaises(ValueError):
                self.catalog.commit(path)
        self.select_raw({"type": "vrm", "id": "linked"})
        self.assertEqual(self.catalog.current_directory(), self.default)
        self.assertNotIn(linked / "pet.json", self.catalog.vrm_manifests())

    def test_duplicate_model_and_configuration_prefer_bundled_copy(self):
        local = self.make_model(self.cache / "local-copy")
        (local / "model.vrm").write_bytes((self.default / "model.vrm").read_bytes())
        self.select_raw({"type": "vrm", "id": local.name})
        self.assertEqual(self.catalog.vrm_manifests(), [self.default / "pet.json"])
        self.assertEqual(self.catalog.current_directory(), self.default)
        self.catalog.commit(local)
        self.assertEqual(json.loads((self.cache / "selected.json").read_text()),
                         {"type": "vrm", "bundled": True, "id": "zome"})

    def test_same_name_different_model_or_configuration_is_kept(self):
        local = self.make_model(self.cache / "local-variant")
        (local / "model.vrm").write_bytes((self.default / "model.vrm").read_bytes())
        manifest = json.loads((local / "pet.json").read_text())
        manifest.update(displayName="zome", hiddenMeshes=["accessory"])
        (local / "pet.json").write_text(json.dumps(manifest))
        self.assertEqual(len(self.catalog.vrm_manifests()), 2)
        manifest.pop("hiddenMeshes")
        (local / "pet.json").write_text(json.dumps(manifest))
        (local / "model.vrm").write_bytes(b"different model")
        self.assertEqual(len(self.catalog.vrm_manifests()), 2)

    def test_digest_cache_refreshes_when_model_changes(self):
        local = self.make_model(self.cache / "local-changing")
        (local / "model.vrm").write_bytes((self.default / "model.vrm").read_bytes())
        self.assertEqual(len(self.catalog.vrm_manifests()), 1)
        (local / "model.vrm").write_bytes(b"changed")
        self.assertEqual(len(self.catalog.vrm_manifests()), 2)
