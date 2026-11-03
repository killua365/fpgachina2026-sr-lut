import json
import os
import stat
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

from hdmi_display.frames import FramePair, FrameToken
from hdmi_display.image_export import export_image


class ImageExportTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'posix', 'Checks Linux collector permissions')
    def test_complete_png_export_is_readable_under_private_umask(self):
        with tempfile.TemporaryDirectory() as folder:
            source = np.full((4, 4, 3), 99, dtype=np.uint8)
            pair = FramePair(FrameToken(1, 0), source, np.repeat(np.repeat(source, 2, 0), 2, 1), 2, 0)
            old_mask = os.umask(0o077)
            try:
                report = export_image(pair, Path(folder)/'saved')
            finally:
                os.umask(old_mask)
            self.assertEqual(stat.S_IMODE(report.parent.stat().st_mode), 0o755)
            for path in report.parent.iterdir():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
            np.testing.assert_array_equal(np.asarray(Image.open(report.parent/'sr.png')), pair.output_rgb)

    def test_png_is_exact_and_no_reference_does_not_claim_quality(self):
        with tempfile.TemporaryDirectory() as folder:
            for scale in (2, 4):
                source = np.arange(7 * 9 * 3, dtype=np.uint8).reshape(7, 9, 3)
                output = np.repeat(np.repeat(source, scale, 0), scale, 1)
                pair = FramePair(FrameToken(1, 0), source, output, scale, 0)
                report_path = export_image(pair, Path(folder) / f"result{scale}")
                report = json.loads(report_path.read_text())
                self.assertIsNone(report["quality"])
                self.assertTrue(report["png_readback_exact"])
                with Image.open(report_path.parent / "sr.png") as decoded:
                    np.testing.assert_array_equal(np.asarray(decoded), output)

    def test_true_reference_is_required_and_metrics_use_native_result(self):
        with tempfile.TemporaryDirectory() as folder:
            source = np.full((32, 32, 3), 95, dtype=np.uint8)
            output = np.full((128, 128, 3), 100, dtype=np.uint8)
            pair = FramePair(FrameToken(1, 0), source, output, 4, 0)
            destination = Path(folder) / "result"
            with self.assertRaisesRegex(ValueError, "origin"):
                export_image(pair, destination, reference_rgb=output)
            report_path = export_image(pair, destination, reference_rgb=output,
                                       source_identity={"reference_origin": "test fixture"})
            quality = json.loads(report_path.read_text())["quality"]
            self.assertEqual(quality["sr"]["psnr_rgb_db"], "infinity")
            self.assertEqual(quality["sr"]["ssim_rgb"], 1.0)
            self.assertLess(quality["nearest"]["ssim_rgb"], 1.0)


if __name__ == "__main__":
    unittest.main()
