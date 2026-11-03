import itertools
import unittest
from pathlib import Path

import numpy as np

from model_s_input import load_rgb_u8
from model_s_neighborhoods import extract_direction_neighborhoods
from model_s_quantizer import quantize_u8
from model_s_simplex import select_simplex_paths
from model_s_simplex import select_simplex_scalar


PROJECT_ROOT = Path(__file__).resolve().parents[3]
IMAGE_ROOT = PROJECT_ROOT / "data/golden" / "source_images"

STRICT_PATHS = {
    (0, 1, 2, 3): (8, 12, 14),
    (0, 1, 3, 2): (8, 12, 13),
    (0, 2, 1, 3): (8, 10, 14),
    (0, 2, 3, 1): (8, 10, 11),
    (0, 3, 1, 2): (8, 9, 13),
    (0, 3, 2, 1): (8, 9, 11),
    (1, 0, 2, 3): (4, 12, 14),
    (1, 0, 3, 2): (4, 12, 13),
    (1, 2, 0, 3): (4, 6, 14),
    (1, 2, 3, 0): (4, 6, 7),
    (1, 3, 0, 2): (4, 5, 13),
    (1, 3, 2, 0): (4, 5, 7),
    (2, 0, 1, 3): (2, 10, 14),
    (2, 0, 3, 1): (2, 10, 11),
    (2, 1, 0, 3): (2, 6, 14),
    (2, 1, 3, 0): (2, 6, 7),
    (2, 3, 0, 1): (2, 10, 11),
    (2, 3, 1, 0): (2, 3, 7),
    (3, 0, 1, 2): (1, 9, 13),
    (3, 0, 2, 1): (1, 9, 11),
    (3, 1, 0, 2): (1, 5, 13),
    (3, 1, 2, 0): (1, 5, 7),
    (3, 2, 0, 1): (1, 3, 11),
    (3, 2, 1, 0): (1, 3, 7),
}

DIMENSION_FROM_BIT = {
    8: 0,
    4: 1,
    2: 2,
    1: 3,
}


def weights_from_masks(masks, remainders, q):
    order = []

    for previous, following in zip(masks[:-1], masks[1:]):
        added_bit = int(previous) ^ int(following)
        order.append(DIMENSION_FROM_BIT[added_bit])

    ordered_values = [remainders[index] for index in order]

    return np.array(
        [
            q - ordered_values[0],
            ordered_values[0] - ordered_values[1],
            ordered_values[1] - ordered_values[2],
            ordered_values[2] - ordered_values[3],
            ordered_values[3],
        ],
        dtype=np.int16,
    )


class ModelSSimplexTests(unittest.TestCase):
    def assert_path_structure(self, masks, weights, q):
        self.assertTrue(np.all(weights.sum(axis=-1) == q))
        self.assertTrue(np.all(masks[..., 0] == 0))
        self.assertTrue(np.all(masks[..., -1] == 15))

        previous = masks[..., :-1]
        following = masks[..., 1:]

        self.assertTrue(np.all((previous & following) == previous))

        xor_values = previous ^ following
        popcount = np.array(
            [bin(value).count("1") for value in range(16)],
            dtype=np.uint8,
        )
        self.assertTrue(np.all(popcount[xor_values] == 1))

    def test_all_24_original_strict_paths(self):
        self.assertEqual(len(STRICT_PATHS), 24)
        descending_values = (13, 9, 5, 1)

        for order, middle_masks in STRICT_PATHS.items():
            with self.subTest(order=order):
                remainders = [0, 0, 0, 0]

                for rank, dimension in enumerate(order):
                    remainders[dimension] = descending_values[rank]

                expected_masks = np.array(
                    [0, *middle_masks, 15],
                    dtype=np.uint8,
                )
                expected_weights = weights_from_masks(
                    expected_masks,
                    remainders,
                    16,
                )

                masks, weights = select_simplex_scalar(
                    remainders[0],
                    remainders[1],
                    remainders[2],
                    remainders[3],
                    4,
                )

                np.testing.assert_array_equal(masks, expected_masks)
                np.testing.assert_array_equal(weights, expected_weights)

    def test_original_tie_cases(self):
        cases = (
            ([0, 0, 0, 0], 4, [0, 1, 3, 7, 15], [16, 0, 0, 0, 0]),
            ([5, 5, 5, 5], 4, [0, 1, 3, 7, 15], [11, 0, 0, 0, 5]),
            ([5, 5, 1, 1], 4, [0, 4, 12, 13, 15], [11, 0, 4, 0, 1]),
            ([5, 1, 5, 1], 4, [0, 2, 10, 11, 15], [11, 0, 4, 0, 1]),
            ([1, 1, 26, 26], 5, [0, 1, 3, 7, 15], [6, 0, 25, 0, 1]),
        )

        for remainders, bits, expected_masks, expected_weights in cases:
            with self.subTest(
                remainders=remainders,
                sampling_bits=bits,
            ):
                masks, weights = select_simplex_scalar(
                    remainders[0],
                    remainders[1],
                    remainders[2],
                    remainders[3],
                    bits,
                )

                np.testing.assert_array_equal(masks, expected_masks)
                np.testing.assert_array_equal(weights, expected_weights)

    def test_exhaustive_4bit_source_fingerprint(self):
        remainders = np.indices(
            (16, 16, 16, 16),
            dtype=np.uint8,
        ).reshape(4, -1).transpose(1, 0)

        masks, weights = select_simplex_paths(remainders, 4)

        self.assertEqual(remainders.shape[0], 65536)
        self.assert_path_structure(masks, weights, 16)
        self.assertEqual(int(weights.min()), -13)
        self.assertEqual(int(weights.max()), 16)
        self.assertEqual(
            int(np.any(weights < 0, axis=-1).sum()),
            1820,
        )

    def test_exhaustive_5bit_source_fingerprint(self):
        remainders = np.indices(
            (32, 32, 32, 32),
            dtype=np.uint8,
        ).reshape(4, -1).transpose(1, 0)

        masks, weights = select_simplex_paths(remainders, 5)

        self.assertEqual(remainders.shape[0], 1048576)
        self.assert_path_structure(masks, weights, 32)
        self.assertEqual(int(weights.min()), -29)
        self.assertEqual(int(weights.max()), 32)
        self.assertEqual(
            int(np.any(weights < 0, axis=-1).sum()),
            35960,
        )

    def test_real_image_integration(self):
        configurations = (
            (IMAGE_ROOT / "lr_x2" / "woman_gt.png", 4),
            (IMAGE_ROOT / "lr_x4" / "woman.png", 4),
            (IMAGE_ROOT / "lr_x4" / "woman.png", 5),
        )

        for image_path, sampling_bits in configurations:
            with self.subTest(
                image=image_path.name,
                sampling_bits=sampling_bits,
            ):
                image = load_rgb_u8(image_path)
                neighborhoods = extract_direction_neighborhoods(image)
                _, _, remainders = quantize_u8(
                    neighborhoods,
                    sampling_bits,
                )

                masks, weights = select_simplex_paths(
                    remainders,
                    sampling_bits,
                )

                self.assertEqual(
                    masks.shape,
                    remainders.shape[:-1] + (5,),
                )
                self.assertEqual(weights.shape, masks.shape)
                self.assert_path_structure(
                    masks,
                    weights,
                    1 << sampling_bits,
                )

    def test_rejects_invalid_inputs(self):
        with self.assertRaises(TypeError):
            select_simplex_paths(
                np.zeros((2, 4), dtype=np.float32),
                4,
            )

        with self.assertRaises(ValueError):
            select_simplex_paths(
                np.array([[0, 0, 0, 16]], dtype=np.int16),
                4,
            )

        with self.assertRaises(ValueError):
            select_simplex_paths(
                np.zeros((2, 3), dtype=np.uint8),
                4,
            )


if __name__ == "__main__":
    unittest.main()