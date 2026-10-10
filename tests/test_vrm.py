"""验证本地形象的资源边界、恢复选择和 HTTP 白名单。"""
import json
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen
from unittest.mock import patch

from boxagent.interfaces.macos.pets.catalog import PetCatalog
from boxagent.interfaces.macos.pets.vrm import AssetServer, read_manifest


class VrmTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.directory = self.root / "local-avatar"
        self.directory.mkdir()
        self.manifest = {"type": "vrm", "version": 1, "model": "model.vrm", "actions": {"idle": "idle.vrma"}}
        for name in ("model.vrm", "idle.vrma"):
            (self.directory / name).write_bytes(b"fixture")
        self.save()

    def save(self):
        (self.directory / "pet.json").write_text(json.dumps(self.manifest))

    def test_selection_restores_and_missing_3d_asset_is_not_silently_replaced(self):
        catalog = PetCatalog(self.root)
        catalog.commit(self.directory)
        self.assertEqual(catalog.current_directory(), self.directory)
        (self.directory / "idle.vrma").unlink()
        self.assertEqual(catalog.current_directory(), self.directory)
        with self.assertRaises((ValueError, OSError)):
            read_manifest(catalog.current_directory())

    def test_first_start_uses_default_vrm_without_saving_selection(self):
        catalog = PetCatalog(self.root, default_pet=self.directory)
        self.assertEqual(catalog.current_directory(), self.directory)
        self.assertFalse((self.root / "selected.json").exists())

    def test_external_asset_rejected_without_changing_selection(self):
        catalog = PetCatalog(self.root)
        catalog.commit(self.directory)
        before = (self.root / "selected.json").read_bytes()
        (self.root / "secret.vrm").write_bytes(b"private")
        self.manifest["model"] = "../secret.vrm"
        self.save()
        with self.assertRaises(ValueError):
            catalog.commit(self.directory)
        self.assertEqual((self.root / "selected.json").read_bytes(), before)

    def test_server_only_serves_listed_resources_with_token(self):
        server = AssetServer(self.directory, read_manifest(self.directory))
        self.addCleanup(server.close)
        base = server.url.removesuffix("index.html")
        with urlopen(base + "asset/model.vrm") as response:
            self.assertEqual(response.read(), b"fixture")
        (self.directory / "secret.txt").write_text("private")
        for suffix in ("asset/secret.txt", "asset/../pet.json", "../../pet.json"):
            with self.assertRaises(HTTPError):
                urlopen(base + suffix)
        with self.assertRaises(HTTPError):
            urlopen(server.url.replace(server.token, "invalid"))

    def test_shared_pack_served_without_copy_and_rejects_escape(self):
        web = self.root / "web"
        pack = web / "motions/mate-engine"
        pack.mkdir(parents=True)
        (pack / "idle.vrma").write_bytes(b"shared-motion")
        self.manifest.update(motionPack="mate-engine", actions={"idle": "motions/idle.vrma"})
        self.save()
        with patch("boxagent.interfaces.macos.pets.vrm.WEB_ROOT", web):
            server = AssetServer(self.directory, read_manifest(self.directory))
            try:
                with urlopen(server.url.removesuffix("index.html") + "asset/motions/idle.vrma") as response:
                    self.assertEqual(response.read(), b"shared-motion")
            finally:
                server.close()
            self.manifest["actions"]["idle"] = "motions/../../private.vrma"
            self.save()
            with self.assertRaises(ValueError):
                read_manifest(self.directory)
            self.manifest["motionPack"] = "../outside"
            self.save()
            with self.assertRaises(ValueError):
                read_manifest(self.directory)

    def test_lfs_pointer_reports_missing_download(self):
        (self.directory / "model.vrm").write_text("version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 123\n")
        with self.assertRaisesRegex(ValueError, "git lfs pull"):
            read_manifest(self.directory)

    def test_bundled_default_and_saved_selection_are_portable(self):
        bundled = self.root / "bundled"
        zome = bundled / "zome"
        zome.mkdir(parents=True)
        (zome / "pet.json").write_text(json.dumps(self.manifest))
        for name in ("model.vrm", "idle.vrma"):
            (zome / name).write_bytes(b"fixture")
        catalog = PetCatalog(self.root / "cache", default_pet=zome, bundled_root=bundled)
        self.assertEqual(catalog.current_directory(), zome)
        catalog.commit(zome)
        saved = json.loads((catalog.root / "selected.json").read_text())
        self.assertEqual(saved, {"type": "vrm", "bundled": True, "id": "zome"})
        self.assertEqual(catalog.current_directory(), zome)
        self.assertIn(zome / "pet.json", catalog.vrm_manifests())
        (zome / "model.vrm").unlink()
        self.assertEqual(catalog.current_directory(), zome)
