"""用真实 WebP 像素验证下载、离线恢复和失败回滚边界。"""

import io
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from boxagent.config import DEFAULT_PET
from boxagent.pets.catalog import PetCatalog, SOURCE, asset_url, decode_image


def atlas(version=2):
    stream = io.BytesIO()
    Image.new("RGBA", (1536, 2288 if version == 2 else 1872), (90, 130, 180, 255)).save(stream, "WEBP")
    return stream.getvalue()


class PetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.calls = []
        self.version = 2
        self.image = atlas()
        self.offline = False
        self.catalog = PetCatalog(self.temp.name, self.request)

    def request(self, url, limit):
        self.calls.append(url)
        if self.offline:
            raise OSError("断网")
        pet = dict(id="test-pet", displayName="测试伙伴", spriteVersionNumber=self.version,
                   ownerName="作者", spritesheetUrl=f"{SOURCE}/assets/pets/v/123/test-pet/spritesheet.webp")
        if "/share-data" in url:
            return json.dumps({"pet": pet}).encode()
        if "/api/pets?" in url:
            return json.dumps({"pets": [pet], "total": 1}).encode()
        return self.image

    def test_download_requires_commit_and_restores_without_network(self):
        self.assertEqual(self.catalog.current_directory(), DEFAULT_PET)
        self.assertFalse(self.calls)
        directory = self.catalog.prepare("test-pet")
        self.assertEqual(self.catalog.current_directory(), DEFAULT_PET)
        self.catalog.commit(directory)
        self.offline = True
        self.calls.clear()
        fresh = PetCatalog(self.temp.name, self.request)
        self.assertEqual(fresh.current_directory(), directory)
        self.assertEqual(fresh.prepare("test-pet"), directory)
        result = fresh.list(installed=True)
        self.assertEqual(result["total"], 1)
        self.assertIsNotNone(result["pets"][0]["picture"])
        self.assertFalse(self.calls)
        self.assertIsNone(result["pets"][0]["license"])

    def test_failed_download_and_corrupt_cache_preserve_selection(self):
        directory = self.catalog.prepare("test-pet")
        self.catalog.commit(directory)
        before = (Path(self.temp.name) / "selected.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "另一个"):
            self.catalog.prepare("other-pet")
        self.assertEqual((Path(self.temp.name) / "selected.json").read_bytes(), before)
        (directory / "spritesheet.webp").write_bytes(b"corrupted")
        self.assertEqual(self.catalog.current_directory(), DEFAULT_PET)
        self.offline = True
        with self.assertRaises(OSError):
            self.catalog.prepare("test-pet")
        self.assertEqual((Path(self.temp.name) / "selected.json").read_bytes(), before)

    def test_v1_and_v2_must_match_and_fully_decode(self):
        for version in (1, 2):
            self.assertEqual(decode_image(atlas(version), version).width, 1536)
        with self.assertRaises(ValueError):
            decode_image(atlas(1), 2)
        with self.assertRaises(OSError):
            decode_image(self.image[:40], 2)
        self.version = 1
        with self.assertRaisesRegex(ValueError, "版本"):
            self.catalog.prepare("test-pet")
        self.assertFalse((Path(self.temp.name) / "selected.json").exists())

    def test_untrusted_paths_and_hosts_are_rejected(self):
        for identity in ("../secret", "A", "pet%2fsecret", "", "a" * 161):
            with self.assertRaises(ValueError):
                self.catalog.prepare(identity)
        for url in ("https://example.com/a.webp", "http://127.0.0.1/a.webp",
                    f"{SOURCE}/assets/pets/test-pet/../../secret"):
            with self.assertRaises(ValueError):
                asset_url(url, "test-pet", "spritesheet.webp")
        self.assertFalse(self.calls)
        with tempfile.TemporaryDirectory() as outside:
            (Path(self.temp.name) / "test-pet").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "符号链接"):
                self.catalog.prepare("test-pet")
            self.assertFalse(list(Path(outside).iterdir()))

    def test_offline_list_filters_and_clamps_page(self):
        self.catalog.prepare("test-pet")
        self.offline = True
        result = self.catalog.list(search="作者", page=9)
        self.assertTrue(result["error"])
        self.assertEqual(result["page"], 1)
        self.assertEqual(result["total"], 1)
        self.assertEqual(self.catalog.list(search="不存在", installed=True)["total"], 0)
        self.catalog.commit(DEFAULT_PET)
        self.assertEqual(self.catalog.current_directory(), DEFAULT_PET)

    def test_adapter_detects_empty_rows_and_rejects_invisible_pet(self):
        from boxagent.appearance.codex_pets import CodexPetsAppearance
        directory = self.catalog.prepare("test-pet")
        image = Image.new("RGBA", (1536, 2288))
        image.paste((40, 100, 170, 255), (0, 0, 192, 208))
        image.save(directory / "spritesheet.webp", "WEBP")
        prepared = CodexPetsAppearance.prepare(directory)
        self.assertNotIn(0, prepared[3])
        self.assertIn(8, prepared[3])
        self.assertEqual(len(prepared[4]), 16)
        Image.new("RGBA", (1536, 2288)).save(directory / "spritesheet.webp", "WEBP")
        with self.assertRaisesRegex(ValueError, "待机行"):
            CodexPetsAppearance.prepare(directory)
