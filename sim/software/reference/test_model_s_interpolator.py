import unittest
from pathlib import Path

import numpy as np

from model_s_address import vertex_lut_address
from model_s_input import load_rgb_u8
from model_s_interpolator import LUT_SPECS
from model_s_interpolator import interpolate_image_native_numerators
from model_s_interpolator import interpolate_lut_numerators
from model_s_interpolator import load_model_s_lut
from model_s_neighborhoods import extract_direction_neighborhoods
from model_s_quantizer import quantization_parameters
from model_s_quantizer import quantize_u8
from model_s_simplex import select_simplex_scalar


PROJECT_ROOT = Path(__file__).resolve().parents[3]
LUT_ROOT = PROJECT_ROOT / "data/lut" / "original_npy"
IMAGE_ROOT = PROJECT_ROOT / "data/golden" / "source_images"


def load_configured_lut(scale, sampling_bits):
    filename = LUT_SPECS[(scale, sampling_bits)]["filename"]
    return load_model_s_lut(
        LUT_ROOT / filename,
        scale,
        sampling_bits,
    )


def scalar_numerator(lut, lower, remainders, scale, sampling_bits):
    masks, weights = select_simplex_scalar(
        int(remainders[0]),
        int(remainders[1]),
        int(remainders[2]),
        int(remainders[3]),
        sampling_bits,
    )

    addresses = np.array(
        [
            int(
                vertex_lut_address(
                    lower,
                    int(mask),
                    sampling_bits,
                )
            )
            for mask in masks
        ],
        dtype=np.uint32,
    )

    values = lut[addresses].astype(np.int32)

    numerator = np.sum(
        values * weights[:, np.newaxis].astype(np.int32),
        axis=0,
        dtype=np.int32,
    )

    return numerator.reshape(scale, scale)


class ModelSInterpolatorTests(unittest.TestCase):
    def test_lut_integrity_shape_and_dtype(self):
        expected_flat_shapes = {
            (2, 4): (83521, 4),
            (4, 4): (83521, 16),
            (4, 5): (6561, 16),
        }

        for configuration, expected_shape in expected_flat_shapes.items():
            with self.subTest(configuration=configuration):
                scale, sampling_bits = configuration
                lut = load_configured_lut(scale, sampling_bits)

                self.assertEqual(lut.shape, expected_shape)
                self.assertEqual(lut.dtype, np.int8)
                self.assertTrue(lut.flags.c_contiguous)

    def test_fixed_x4_5bit_fingerprint(self):
        image = load_rgb_u8(
            IMAGE_ROOT / "lr_x4" / "woman.png"
        )
        neighborhoods = extract_direction_neighborhoods(image)
        lower, _, remainders = quantize_u8(neighborhoods, 5)

        selected_lower = lower[0, 0, 0, 0]
        selected_remainders = remainders[0, 0, 0, 0]
        lut = load_configured_lut(4, 5)

        numerator, masks, weights, addresses = (
            interpolate_lut_numerators(
                lut,
                selected_lower,
                selected_remainders,
                4,
                5,
                return_trace=True,
            )
        )

        np.testing.assert_array_equal(
            masks,
            [0, 1, 3, 7, 15],
        )
        np.testing.assert_array_equal(
            weights,
            [6, 0, 25, 0, 1],
        )
        np.testing.assert_array_equal(
            addresses,
            [810, 811, 820, 901, 1630],
        )
        np.testing.assert_array_equal(
            numerator.reshape(-1),
            [
                510, 663, -190, -211,
                602, 544, 276, 1,
                349, 682, -528, 451,
                280, 184, 8, 450,
            ],
        )

    def test_vectorized_matches_scalar(self):
        rng = np.random.default_rng(20260805)

        for scale, sampling_bits in ((2, 4), (4, 4), (4, 5)):
            with self.subTest(
                scale=scale,
                sampling_bits=sampling_bits,
            ):
                q, levels = quantization_parameters(sampling_bits)
                lut = load_configured_lut(scale, sampling_bits)

                lower = rng.integers(
                    0,
                    levels - 1,
                    size=(200, 4),
                    dtype=np.uint16,
                )
                remainders = rng.integers(
                    0,
                    q,
                    size=(200, 4),
                    dtype=np.uint16,
                )

                actual = interpolate_lut_numerators(
                    lut,
                    lower,
                    remainders,
                    scale,
                    sampling_bits,
                )

                expected = np.stack(
                    [
                        scalar_numerator(
                            lut,
                            lower[index],
                            remainders[index],
                            scale,
                            sampling_bits,
                        )
                        for index in range(lower.shape[0])
                    ],
                    axis=0,
                )

                np.testing.assert_array_equal(actual, expected)

    def test_zero_remainder_reads_base_vertex(self):
        lower = np.array([1, 1, 0, 0], dtype=np.uint16)
        remainders = np.zeros(4, dtype=np.uint16)

        for scale, sampling_bits in ((2, 4), (4, 4), (4, 5)):
            with self.subTest(
                scale=scale,
                sampling_bits=sampling_bits,
            ):
                q, _ = quantization_parameters(sampling_bits)
                lut = load_configured_lut(scale, sampling_bits)

                actual = interpolate_lut_numerators(
                    lut,
                    lower,
                    remainders,
                    scale,
                    sampling_bits,
                )

                base_address = int(
                    vertex_lut_address(
                        lower,
                        0,
                        sampling_bits,
                    )
                )
                expected = (
                    lut[base_address].astype(np.int32) * q
                ).reshape(scale, scale)

                np.testing.assert_array_equal(actual, expected)

    def test_real_image_native_shapes(self):
        configurations = (
            (IMAGE_ROOT / "lr_x2" / "woman_gt.png", 2, 4),
            (IMAGE_ROOT / "lr_x4" / "woman.png", 4, 4),
            (IMAGE_ROOT / "lr_x4" / "woman.png", 4, 5),
        )

        for image_path, scale, sampling_bits in configurations:
            with self.subTest(
                image=image_path.name,
                scale=scale,
                sampling_bits=sampling_bits,
            ):
                image = load_rgb_u8(image_path)
                lut = load_configured_lut(scale, sampling_bits)

                numerator = interpolate_image_native_numerators(
                    image,
                    lut,
                    scale,
                    sampling_bits,
                )

                self.assertEqual(
                    numerator.shape,
                    (
                        4,
                        image.shape[0],
                        image.shape[1],
                        3,
                        scale,
                        scale,
                    ),
                )
                self.assertEqual(numerator.dtype, np.int32)

    def test_rejects_invalid_inputs(self):
        lut = load_configured_lut(4, 5)
        lower = np.zeros((2, 4), dtype=np.uint16)
        remainders = np.zeros((2, 4), dtype=np.uint16)

        with self.assertRaises(TypeError):
            interpolate_lut_numerators(
                lut.astype(np.int16),
                lower,
                remainders,
                4,
                5,
            )

        with self.assertRaises(ValueError):
            interpolate_lut_numerators(
                lut,
                lower,
                remainders[:, :3],
                4,
                5,
            )

        with self.assertRaises(ValueError):
            load_model_s_lut(
                LUT_ROOT / LUT_SPECS[(4, 5)]["filename"],
                3,
                5,
            )


if __name__ == "__main__":
    unittest.main()