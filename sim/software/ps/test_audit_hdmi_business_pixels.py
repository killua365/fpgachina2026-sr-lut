from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

from audit_hdmi_business_pixels import audit, compare_png, safe_path


class DecodedPixelAuditTests(unittest.TestCase):
    def test_decoded_difference_cannot_be_hidden_by_summary_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            expected = np.zeros((33, 35, 3), np.uint8)
            output = expected.copy()
            output[17, 19, 1] = 1
            path = Path(tmp) / 'output.png'
            Image.fromarray(output).save(path)
            self.assertEqual(compare_png(path, expected), 1)
            with self.assertRaises(ValueError):
                compare_png(path, expected[:32])

    def test_evidence_cannot_reference_outside_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            for value in ('../outside.png', '/outside.png', 'C:\\outside.png', ''):
                with self.assertRaises((ValueError, FileNotFoundError)):
                    safe_path(Path(tmp), value)

    def test_missing_package_never_certifies_reported_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = audit(Path(tmp), Path(tmp), 'a' * 64,
                           {'checks': [{'passed': True}]}, {'checks': [{'exact_rgb_match': True}]})
            self.assertFalse(result['passed'])
            self.assertFalse(result['checks']['image_pixels_recomputed'])
            self.assertFalse(result['formal_release'])


if __name__ == '__main__':
    unittest.main()
