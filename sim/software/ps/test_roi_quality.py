import unittest

import numpy as np

from roi_magnifier.quality import calculate_psnr_y
from roi_magnifier.quality import calculate_psnr_rgb
from roi_magnifier.quality import calculate_ssim_y
from roi_magnifier.quality import calculate_ssim_rgb
from roi_magnifier.quality import compare_against_input
from roi_magnifier.quality import compare_against_hr
from roi_magnifier.quality import crop_hr_roi
from roi_magnifier.quality import nearest_x4


class RoiQualityTests(unittest.TestCase):
    def setUp(self):
        yy, xx = np.indices((20, 24), dtype=np.uint8)
        self.lr = np.stack((xx * 5, yy * 7, xx * 3 + yy * 2), axis=-1)
        self.hr = np.repeat(np.repeat(self.lr, 4, axis=0), 4, axis=1)

    def test_nearest_output_uses_dynamic_x4_dimensions(self):
        roi = self.lr[2:9, 3:11]
        output = nearest_x4(roi)
        self.assertEqual(output.shape, (28, 32, 3))
        self.assertEqual(output.dtype, np.uint8)
        np.testing.assert_array_equal(output, np.repeat(np.repeat(roi, 4, axis=0), 4, axis=1))

    def test_hr_crop_maps_lr_coordinates_by_four(self):
        crop = crop_hr_roi(self.hr, 3, 2, 8, 7)
        expected = np.repeat(np.repeat(self.lr[2:9, 3:11], 4, axis=0), 4, axis=1)
        np.testing.assert_array_equal(crop, expected)

    def test_identical_images_have_perfect_metrics(self):
        self.assertEqual(calculate_psnr_y(self.hr, self.hr), float("inf"))
        self.assertAlmostEqual(calculate_ssim_y(self.hr, self.hr), 1.0, places=12)
        self.assertEqual(calculate_psnr_rgb(self.hr, self.hr), float("inf"))
        self.assertAlmostEqual(calculate_ssim_rgb(self.hr, self.hr), 1.0, places=12)
        for scale in (2, 4):
            quality = compare_against_input(self.hr, self.hr, scale=scale)
            self.assertEqual(quality["reference_type"], f"input_nearest_x{scale}")
            self.assertEqual(quality["fpga_vs_input"]["psnr_y_db"], float("inf"))
            self.assertAlmostEqual(quality["fpga_vs_input"]["ssim_y"], 1.0, places=12)
            self.assertEqual(quality["fpga_vs_input"]["psnr_rgb_db"], float("inf"))
            self.assertAlmostEqual(quality["fpga_vs_input"]["ssim_rgb"], 1.0, places=12)
            self.assertIn("not an objective", quality["contract"]["interpretation"])

    def test_rgb_metrics_capture_colour_only_change(self):
        reference = np.full((20, 24, 3), 128, dtype=np.uint8)
        output = reference.copy()
        output[..., 0] = 180
        self.assertGreater(calculate_psnr_rgb(reference, output), 0.0)
        self.assertLess(calculate_ssim_rgb(reference, output), 1.0)

    def test_tiny_roi_reports_unavailable_formal_metrics(self):
        tiny = np.zeros((4, 4, 3), dtype=np.uint8)
        quality = compare_against_hr(tiny, tiny, tiny)
        self.assertEqual(
            set(quality),
            {"reference_type", "contract", "nearest_vs_hr", "fpga_vs_hr", "fpga_minus_nearest"},
        )
        self.assertEqual(quality["reference_type"], "hr_ground_truth")
        for comparison in ("nearest_vs_hr", "fpga_vs_hr", "fpga_minus_nearest"):
            self.assertIsNone(quality[comparison]["psnr_y_db"])
            self.assertIsNone(quality[comparison]["ssim_y"])
            self.assertIsNone(quality[comparison]["psnr_rgb_db"])
            self.assertIsNone(quality[comparison]["ssim_rgb"])

    def test_comparison_reports_fpga_minus_nearest_delta(self):
        fpga = self.hr.copy()
        nearest = self.hr.copy()
        nearest[10:30, 10:30] = np.clip(
            nearest[10:30, 10:30].astype(np.int16) + 10,
            0,
            255,
        ).astype(np.uint8)
        quality = compare_against_hr(self.hr, fpga, nearest)
        self.assertGreater(quality["fpga_minus_nearest"]["psnr_y_db"], 0.0)
        self.assertGreater(quality["fpga_minus_nearest"]["ssim_y"], 0.0)
        self.assertGreater(quality["fpga_minus_nearest"]["psnr_rgb_db"], 0.0)
        self.assertGreater(quality["fpga_minus_nearest"]["ssim_rgb"], 0.0)

    def test_identical_comparisons_have_zero_deltas(self):
        quality = compare_against_hr(self.hr, self.hr, self.hr)
        self.assertEqual(quality["fpga_minus_nearest"]["psnr_y_db"], 0.0)
        self.assertEqual(quality["fpga_minus_nearest"]["ssim_y"], 0.0)
        self.assertEqual(quality["fpga_minus_nearest"]["psnr_rgb_db"], 0.0)
        self.assertEqual(quality["fpga_minus_nearest"]["ssim_rgb"], 0.0)

    def test_ssim_optimization_preserves_fixed_contract_values(self):
        random = np.random.default_rng(3317)
        hr = random.integers(0, 256, (148, 164, 3), dtype=np.uint8)
        fpga = random.integers(0, 256, (148, 164, 3), dtype=np.uint8)
        nearest = random.integers(0, 256, (148, 164, 3), dtype=np.uint8)
        quality = compare_against_hr(hr, fpga, nearest)
        self.assertAlmostEqual(
            quality["fpga_vs_hr"]["ssim_y"],
            0.016747748726791058,
            places=12,
        )
        self.assertAlmostEqual(
            quality["nearest_vs_hr"]["ssim_y"],
            0.016840324614569368,
            places=12,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
