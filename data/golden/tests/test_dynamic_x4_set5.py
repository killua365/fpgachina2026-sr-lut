import hashlib
import json
from pathlib import Path
import sys
import unittest

import numpy as np
from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[3]
REFERENCE_ROOT = PROJECT_ROOT / "src/reference"
if str(REFERENCE_ROOT) not in sys.path:
    sys.path.insert(0, str(REFERENCE_ROOT))

from model_s_interpolator import load_model_s_lut
from model_s_output import run_model_s


class DynamicX4Set5Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vector_root = PROJECT_ROOT / "data/golden"
        manifest_path = cls.vector_root / "dynamic_x4_set5_manifest.json"
        cls.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        cls.lut = load_model_s_lut(
            PROJECT_ROOT
            / "data/lut"
            / "original_npy"
            / "model_s_x4_5bit_int8.npy",
            4,
            5,
        )

    @staticmethod
    def sha256(path):
        return hashlib.sha256(path.read_bytes()).hexdigest().upper()

    @staticmethod
    def load_rgb(path):
        with Image.open(path) as image:
            return np.asarray(image.convert("RGB"), dtype=np.uint8)

    def test_manifest_contract(self):
        bounds = self.manifest["supported_input"]
        self.assertEqual(self.manifest["scale"], 4)
        self.assertEqual(self.manifest["sampling_bits"], 5)
        self.assertEqual(
            (
                bounds["minimum_width"],
                bounds["maximum_width"],
                bounds["minimum_height"],
                bounds["maximum_height"],
            ),
            (32, 320, 32, 320),
        )
        self.assertEqual(len(self.manifest["images"]), 5)

    def test_asset_identity_and_shapes(self):
        for item in self.manifest["images"]:
            for role in ("input", "golden", "hr"):
                path = self.vector_root / item[role]
                self.assertTrue(path.is_file(), f"missing {role}: {path}")
                self.assertEqual(self.sha256(path), item[f"{role}_sha256"])
            input_rgb = self.load_rgb(self.vector_root / item["input"])
            golden_rgb = self.load_rgb(self.vector_root / item["golden"])
            self.assertEqual(list(input_rgb.shape), item["input_shape_hwc"])
            self.assertEqual(list(golden_rgb.shape), item["output_shape_hwc"])

    def test_reference_outputs_are_pixel_exact(self):
        for item in self.manifest["images"]:
            input_rgb = self.load_rgb(self.vector_root / item["input"])
            golden_rgb = self.load_rgb(self.vector_root / item["golden"])
            actual = run_model_s(input_rgb, self.lut, 4, 5)
            np.testing.assert_array_equal(actual, golden_rgb, err_msg=item["name"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
