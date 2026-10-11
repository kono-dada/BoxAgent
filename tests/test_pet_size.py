"""桌宠缩放的持久化恢复和多显示器边界。"""

import tempfile
import unittest
from pathlib import Path

from boxagent.interfaces.macos.pets.size import (
    load_pet_scale, resized_pet_frame, save_pet_scale, scaled_pet_size)


class PetSizeTests(unittest.TestCase):
    def test_saved_scale_survives_reload_and_bad_settings_fall_back(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "data"
            self.assertEqual(load_pet_scale(root), 1.0)
            for scale in (0.5, 1.5, 2.37, 6.0):
                save_pet_scale(root, scale)
                self.assertEqual(load_pet_scale(root), scale)
            for content in ('{', '[]', '{"scale": true}', '{"scale": -1}',
                            '{"scale": NaN}', '{"scale": "2"}',
                            '{"scale": Infinity}', '{"scale": 6.1}'):
                (root / "appearance.json").write_text(content)
                self.assertEqual(load_pet_scale(root), 1.0, content)

    def test_resize_preserves_bottom_center_away_from_edges(self):
        self.assertEqual(
            resized_pet_frame(((400, 80), (252, 182)), (504, 364),
                              ((0, 0), (1440, 900))),
            ((274, 80), (504, 364)))

    def test_resize_keeps_pet_on_display_with_negative_origin(self):
        frame = resized_pet_frame(((-150, 850), (252, 182)), (504, 364),
                                  ((-1440, 40), (1440, 900)))
        self.assertEqual(frame, ((-504, 576), (504, 364)))

    def test_small_display_caps_size_without_changing_aspect_ratio(self):
        width, height = scaled_pet_size((252, 182), 2.0, (300, 180))
        self.assertLessEqual(width, 300)
        self.assertLessEqual(height, 180)
        self.assertAlmostEqual(width / height, 252 / 182)
