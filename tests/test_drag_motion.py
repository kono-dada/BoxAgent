"""拖动阈值、速度与原点回拖的回归检查。"""
import unittest
from boxagent.interfaces.macos.pets.drag_motion import DragMotion


class DragMotionTests(unittest.TestCase):
    def test_click_jitter_does_not_move_window(self):
        motion = DragMotion.begin((100, 100), 1)
        self.assertIsNone(motion.move((102, 101), 1.02))
        self.assertFalse(motion.dragging)

    def test_returning_to_origin_does_not_freeze_drag(self):
        motion = DragMotion.begin((100, 100), 1)
        kind, offset, velocity = motion.move((120, 100), 1.1)
        self.assertEqual(kind, "drag-start")
        self.assertAlmostEqual(velocity[0], 200)
        kind, offset, velocity = motion.move((100, 100), 1.2)
        self.assertEqual(kind, "drag-move")
        self.assertEqual(offset, (0, 0))
        self.assertAlmostEqual(velocity[0], -200)

    def test_speed_has_same_units_at_different_sampling_rates(self):
        for hz in (30, 60, 120):
            motion = DragMotion.begin((0, 0), 0)
            kind, _, velocity = motion.move((1200 / hz, 0), 1 / hz)
            self.assertEqual(kind, "drag-start")
            self.assertAlmostEqual(velocity[0], 1200)

    def test_teleport_and_zero_time_are_bounded(self):
        motion = DragMotion.begin((0, 0), 0)
        _, _, velocity = motion.move((100000, -100000), 0)
        self.assertEqual(velocity, (2400, -2400))
