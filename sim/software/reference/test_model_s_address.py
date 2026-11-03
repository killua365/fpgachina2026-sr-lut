import unittest
from pathlib import Path

import numpy as np

from model_s_address import all_vertex_lut_addresses
from model_s_address import base_lut_address
from model_s_address import flatten_lut_coordinates
from model_s_address import lut_address_parameters
from model_s_address import vertex_lut_address
from model_s_input import load_rgb_u8
from model_s_neighborhoods import extract_direction_neighborhoods
from model_s_quantizer import quantize_u8


PROJECT_ROOT = Path(__file__).resolve().parents[3]
IMAGE_ROOT = PROJECT_ROOT / "data/golden" / "source_images"


class ModelSAddressTests(unittest.TestCase):
    def test_parameters(self):
        self.assertEqual(
            lut_address_parameters(4),
            (17, 83521, (4913, 289, 17, 1), 17),
        )
        self.assertEqual(
            lut_address_parameters(5),
            (9, 6561, (729, 81, 9, 1), 13),
        )

    def test_known_coordinates_and_endpoints(self):
        coordinates_17 = np.array(
            [
                [0, 0, 0, 0],
                [0, 0, 0, 1],
                [0, 0, 1, 0],
                [0, 1, 0, 0],
                [1, 0, 0, 0],
                [16, 16, 16, 16],
            ],
            dtype=np.uint16,
        )

        np.testing.assert_array_equal(
            flatten_lut_coordinates(coordinates_17, 17),
            [0, 1, 17, 289, 4913, 83520],
        )

        coordinates_9 = np.array(
            [
                [0, 0, 0, 0],
                [0, 0, 0, 1],
                [0, 0, 1, 0],
                [0, 1, 0, 0],
                [1, 0, 0, 0],
                [8, 8, 8, 8],
            ],
            dtype=np.uint16,
        )

        np.testing.assert_array_equal(
            flatten_lut_coordinates(coordinates_9, 9),
            [0, 1, 9, 81, 729, 6560],
        )

    def test_matches_numpy_flattening(self):
        rng = np.random.default_rng(20260805)

        for levels in (17, 9):
            coordinates = rng.integers(
                0,
                levels,
                size=(10000, 4),
                dtype=np.uint16,
            )

            expected = np.ravel_multi_index(
                tuple(coordinates[:, index] for index in range(4)),
                (levels, levels, levels, levels),
            )

            actual = flatten_lut_coordinates(
                coordinates,
                levels,
            )

            np.testing.assert_array_equal(actual, expected)

    def test_all_images_base_addresses(self):
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
                levels, _, _, _ = lut_address_parameters(sampling_bits)
                lower, _, _ = quantize_u8(
                    neighborhoods,
                    sampling_bits,
                )
                actual = base_lut_address(
                    lower,
                    sampling_bits,
                )

                for direction in range(4):
                    for channel in range(3):
                        coordinates = lower[
                            direction,
                            :,
                            :,
                            channel,
                            :,
                        ]

                        expected = np.ravel_multi_index(
                            tuple(
                                coordinates[..., index]
                                for index in range(4)
                            ),
                            (levels, levels, levels, levels),
                        )

                        np.testing.assert_array_equal(
                            actual[direction, :, :, channel],
                            expected,
                        )

    def test_vertex_mask_bit_order(self):
        lower = np.array([1, 1, 0, 0], dtype=np.uint16)

        addresses = all_vertex_lut_addresses(lower, 5)

        np.testing.assert_array_equal(
            addresses,
            [
                810, 811, 819, 820,
                891, 892, 900, 901,
                1539, 1540, 1548, 1549,
                1620, 1621, 1629, 1630,
            ],
        )

        self.assertEqual(
            int(vertex_lut_address(lower, 8, 5)),
            1539,
        )
        self.assertEqual(
            int(vertex_lut_address(lower, 15, 5)),
            1630,
        )

    def test_rejects_invalid_inputs(self):
        with self.assertRaises(ValueError):
            base_lut_address(
                np.array([16, 0, 0, 0], dtype=np.uint16),
                4,
            )

        with self.assertRaises(ValueError):
            flatten_lut_coordinates(
                np.array([9, 0, 0, 0], dtype=np.uint16),
                9,
            )

        with self.assertRaises(ValueError):
            vertex_lut_address(
                np.array([0, 0, 0, 0], dtype=np.uint16),
                16,
                5,
            )

        with self.assertRaises(TypeError):
            flatten_lut_coordinates(
                np.zeros((4,), dtype=np.float32),
                9,
            )


if __name__ == "__main__":
    unittest.main()
