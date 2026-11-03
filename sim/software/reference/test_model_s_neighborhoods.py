import unittest
from pathlib import Path

import numpy as np

from model_s_input import load_rgb_u8
from model_s_neighborhoods import extract_direction_neighborhoods


PROJECT_ROOT = Path(__file__).resolve().parents[3]
IMAGE_ROOT = PROJECT_ROOT / "data/golden" / "source_images"


def make_synthetic_rgb():
    base = np.arange(12, dtype=np.uint8).reshape(3, 4)
    return np.stack((base, base + 20, base + 40), axis=2)


def extract_source_style_neighborhoods(image):
    directions = []

    for rotation in range(4):
        rotated = np.rot90(image, rotation)
        height, width, _ = rotated.shape

        padded = np.pad(
            rotated,
            ((0, 1), (0, 1), (0, 0)),
            mode="reflect",
        )

        block = np.stack(
            (
                padded[0:height, 0:width],
                padded[0:height, 1 : width + 1],
                padded[1 : height + 1, 0:width],
                padded[1 : height + 1, 1 : width + 1],
            ),
            axis=-1,
        )

        aligned = np.rot90(
            block,
            -rotation,
            axes=(0, 1),
        )
        directions.append(aligned)

    return np.ascontiguousarray(np.stack(directions, axis=0))


class ModelSNeighborhoodTests(unittest.TestCase):
    def test_shape_dtype_and_center(self):
        image = make_synthetic_rgb()
        neighborhoods = extract_direction_neighborhoods(image)

        self.assertEqual(neighborhoods.shape, (4, 3, 4, 3, 4))
        self.assertEqual(neighborhoods.dtype, np.uint8)
        self.assertTrue(neighborhoods.flags.c_contiguous)

        for direction in range(4):
            np.testing.assert_array_equal(
                neighborhoods[direction, :, :, :, 0],
                image,
            )

    def test_known_center_directions(self):
        image = make_synthetic_rgb()
        neighborhoods = extract_direction_neighborhoods(image)

        expected_red = (
            [5, 6, 9, 10],
            [5, 9, 4, 8],
            [5, 4, 1, 0],
            [5, 1, 6, 2],
        )

        for direction, expected in enumerate(expected_red):
            np.testing.assert_array_equal(
                neighborhoods[direction, 1, 1, 0],
                expected,
            )

    def test_reflect_at_top_left(self):
        image = make_synthetic_rgb()
        neighborhoods = extract_direction_neighborhoods(image)

        expected_red = (
            [0, 1, 4, 5],
            [0, 4, 1, 5],
            [0, 1, 4, 5],
            [0, 4, 1, 5],
        )

        for direction, expected in enumerate(expected_red):
            np.testing.assert_array_equal(
                neighborhoods[direction, 0, 0, 0],
                expected,
            )

    def test_matches_original_rotation_method(self):
        image_paths = sorted(
            image_path
            for directory in ("hr", "lr_x2", "lr_x4")
            for image_path in (IMAGE_ROOT / directory).glob("*.png")
        )
        self.assertEqual(len(image_paths), 15)

        for image_path in image_paths:
            with self.subTest(image=image_path.name):
                image = load_rgb_u8(image_path)

                direct = extract_direction_neighborhoods(image)
                source_style = extract_source_style_neighborhoods(image)

                np.testing.assert_array_equal(
                    direct,
                    source_style,
                )


if __name__ == "__main__":
    unittest.main()
