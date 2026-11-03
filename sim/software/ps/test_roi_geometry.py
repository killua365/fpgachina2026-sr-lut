import unittest

from roi_magnifier.geometry import plan_roi


class RoiGeometryTests(unittest.TestCase):
    def test_internal_roi_has_one_pixel_context(self):
        plan = plan_roi(320, 240, 50, 40, 60, 30)
        self.assertEqual(plan.process_window.as_dict(), {
            "x": 49, "y": 39, "width": 62, "height": 32
        })
        self.assertEqual(plan.output_crop.as_dict(), {
            "x": 4, "y": 4, "width": 240, "height": 120
        })

    def test_small_roi_expands_to_minimum_window(self):
        plan = plan_roi(100, 80, 50, 40, 1, 1)
        self.assertEqual((plan.process_window.width, plan.process_window.height), (32, 32))
        self.assertGreaterEqual(plan.roi.x, plan.process_window.x)
        self.assertLess(plan.roi.x, plan.process_window.right)

    def test_top_left_uses_true_image_boundary(self):
        plan = plan_roi(128, 128, 0, 0, 20, 10)
        self.assertEqual((plan.process_window.x, plan.process_window.y), (0, 0))
        self.assertEqual((plan.output_crop.x, plan.output_crop.y), (0, 0))

    def test_bottom_right_uses_true_image_boundary(self):
        plan = plan_roi(128, 128, 108, 118, 20, 10)
        self.assertEqual((plan.process_window.right, plan.process_window.bottom), (128, 128))

    def test_full_320_frame_is_allowed(self):
        plan = plan_roi(320, 320, 0, 0, 320, 320)
        self.assertEqual(plan.process_window, plan.roi)
        self.assertEqual((plan.output_width, plan.output_height), (1280, 1280))

    def test_internal_479_pixel_roi_is_too_wide(self):
        with self.assertRaisesRegex(ValueError, "horizontal"):
            plan_roi(500, 100, 10, 10, 479, 20)

    def test_479_pixel_roi_at_source_edge_is_allowed(self):
        plan = plan_roi(500, 100, 0, 10, 479, 20)
        self.assertEqual(plan.process_window.width, 480)

    def test_source_minimum_is_enforced(self):
        with self.assertRaisesRegex(ValueError, "at least"):
            plan_roi(31, 64, 0, 0, 10, 10)

    def test_roi_must_be_inside_source(self):
        with self.assertRaisesRegex(ValueError, "inside"):
            plan_roi(64, 64, 60, 10, 5, 5)

    def test_boolean_coordinates_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "integer"):
            plan_roi(64, 64, True, 0, 1, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
