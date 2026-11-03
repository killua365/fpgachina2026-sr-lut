import unittest
from pathlib import Path

import numpy as np

from model_s_input import load_rgb_u8
from model_s_input import reflect_pad_one
from model_s_input import validate_rgb_u8


PROJECT_ROOT = Path(__file__).resolve().parents[3]
IMAGE_ROOT = PROJECT_ROOT / "data/golden" / "source_images"


class ModelSInputTests(unittest.TestCase):
    def test_synthetic_reflect_values(self):
        base = np.arange(12, dtype=np.uint8).reshape(3, 4)
        image = np.stack((base, base + 20, base + 40), axis=2)

        padded = reflect_pad_one(image)

        expected_red = np.array(
            [
                [5, 4, 5, 6, 7, 6],
                [1, 0, 1, 2, 3, 2],
                [5, 4, 5, 6, 7, 6],
                [9, 8, 9, 10, 11, 10],
                [5, 4, 5, 6, 7, 6],
            ],
            dtype=np.uint8,
        )

        np.testing.assert_array_equal(padded[:, :, 0], expected_red)
        np.testing.assert_array_equal(padded[0, 0], [5, 25, 45])
        self.assertTrue(padded.flags.c_contiguous)

    def test_all_copied_images(self):
        image_paths = sorted(
            image_path
            for directory in ("hr", "lr_x2", "lr_x4")
            for image_path in (IMAGE_ROOT / directory).glob("*.png")
        )
        self.assertEqual(len(image_paths), 15)

        for image_path in image_paths:
            with self.subTest(image=image_path.name):
                image = load_rgb_u8(image_path)
                padded = reflect_pad_one(image)

                self.assertEqual(image.dtype, np.uint8)
                self.assertEqual(image.shape[2], 3)
                self.assertEqual(
                    padded.shape,
                    (image.shape[0] + 2, image.shape[1] + 2, 3),
                )
                np.testing.assert_array_equal(
                    padded[1:-1, 1:-1],
                    image,
                )

    def test_rejects_non_rgb_array(self):
        image = np.zeros((4, 4), dtype=np.uint8)

        with self.assertRaises(ValueError):
            validate_rgb_u8(image)

    def test_rejects_wrong_dtype(self):
        image = np.zeros((4, 4, 3), dtype=np.float32)

        with self.assertRaises(TypeError):
            validate_rgb_u8(image)

    def test_rejects_too_small_image(self):
        image = np.zeros((1, 4, 3), dtype=np.uint8)

        with self.assertRaises(ValueError):
            validate_rgb_u8(image)


if __name__ == "__main__":
    unittest.main()
