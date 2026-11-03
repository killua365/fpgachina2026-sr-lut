import unittest
from pathlib import Path

import numpy as np

from model_s_input import load_rgb_u8
from model_s_neighborhoods import extract_direction_neighborhoods
from model_s_quantizer import quantization_parameters
from model_s_quantizer import quantize_u8


PROJECT_ROOT = Path(__file__).resolve().parents[3]
IMAGE_ROOT = PROJECT_ROOT / "data/golden" / "source_images"


class ModelSQuantizerTests(unittest.TestCase):
    def test_parameters(self):
        self.assertEqual(quantization_parameters(4), (16, 17))
        self.assertEqual(quantization_parameters(5), (32, 9))

    def test_exact_boundaries_4bit(self):
        values = np.array(
            [0, 1, 15, 16, 17, 31, 32, 254, 255],
            dtype=np.uint8,
        )

        lower, upper, remainder = quantize_u8(values, 4)

        np.testing.assert_array_equal(
            lower,
            [0, 0, 0, 1, 1, 1, 2, 15, 15],
        )
        np.testing.assert_array_equal(
            upper,
            [1, 1, 1, 2, 2, 2, 3, 16, 16],
        )
        np.testing.assert_array_equal(
            remainder,
            [0, 1, 15, 0, 1, 15, 0, 14, 15],
        )

    def test_exact_boundaries_5bit(self):
        values = np.array(
            [0, 1, 31, 32, 33, 63, 64, 254, 255],
            dtype=np.uint8,
        )

        lower, upper, remainder = quantize_u8(values, 5)

        np.testing.assert_array_equal(
            lower,
            [0, 0, 0, 1, 1, 1, 2, 7, 7],
        )
        np.testing.assert_array_equal(
            upper,
            [1, 1, 1, 2, 2, 2, 3, 8, 8],
        )
        np.testing.assert_array_equal(
            remainder,
            [0, 1, 31, 0, 1, 31, 0, 30, 31],
        )

    def test_matches_original_formulas_for_all_u8_values(self):
        values = np.arange(256, dtype=np.uint8)

        for sampling_bits in (4, 5):
            with self.subTest(sampling_bits=sampling_bits):
                q, _ = quantization_parameters(sampling_bits)
                wide_values = values.astype(np.uint16)

                lower, upper, remainder = quantize_u8(
                    values,
                    sampling_bits,
                )

                np.testing.assert_array_equal(
                    lower,
                    wide_values // q,
                )
                np.testing.assert_array_equal(
                    upper,
                    wide_values // q + 1,
                )
                np.testing.assert_array_equal(
                    remainder,
                    wide_values % q,
                )

    def test_all_images_round_trip(self):
        image_paths = sorted(
            image_path
            for directory in ("hr", "lr_x2", "lr_x4")
            for image_path in (IMAGE_ROOT / directory).glob("*.png")
        )
        self.assertEqual(len(image_paths), 15)

        for image_path in image_paths:
            image = load_rgb_u8(image_path)
            neighborhoods = extract_direction_neighborhoods(image)

            for sampling_bits in (4, 5):
                with self.subTest(
                    image=image_path.name,
                    sampling_bits=sampling_bits,
                ):
                    q, levels = quantization_parameters(sampling_bits)
                    lower, upper, remainder = quantize_u8(
                        neighborhoods,
                        sampling_bits,
                    )

                    reconstructed = lower * q + remainder

                    np.testing.assert_array_equal(
                        reconstructed,
                        neighborhoods.astype(np.uint16),
                    )
                    self.assertLess(int(lower.max()), levels - 1)
                    self.assertLess(int(upper.max()), levels)
                    self.assertLess(int(remainder.max()), q)

    def test_rejects_invalid_inputs(self):
        with self.assertRaises(TypeError):
            quantize_u8(
                np.zeros((4,), dtype=np.float32),
                4,
            )

        with self.assertRaises(ValueError):
            quantization_parameters(3)

        with self.assertRaises(TypeError):
            quantization_parameters("4")


if __name__ == "__main__":
    unittest.main()
