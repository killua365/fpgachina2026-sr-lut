import unittest
from pathlib import Path

import numpy as np

from model_s_fusion import fuse_direction_numerators
from model_s_fusion import infer_image_blocks
from model_s_fusion import restore_direction_blocks
from model_s_fusion import round_divide_ties_to_even
from model_s_input import load_rgb_u8
from model_s_interpolator import LUT_SPECS
from model_s_interpolator import interpolate_image_native_numerators
from model_s_interpolator import load_model_s_lut


PROJECT_ROOT = Path(__file__).resolve().parents[3]
LUT_ROOT = PROJECT_ROOT / "data/lut" / "original_npy"
IMAGE_ROOT = (
    PROJECT_ROOT
    / "data/golden"
    / "source_images"
)


def load_configured_lut(scale, sampling_bits):
    filename = LUT_SPECS[
        (scale, sampling_bits)
    ]["filename"]

    return load_model_s_lut(
        LUT_ROOT / filename,
        scale,
        sampling_bits,
    )


def source_style_restored_numerators(
    image,
    lut,
    scale,
    sampling_bits,
):
    directions = []

    for rotation in range(4):
        rotated = np.ascontiguousarray(
            np.rot90(image, rotation)
        )

        native = interpolate_image_native_numerators(
            rotated,
            lut,
            scale,
            sampling_bits,
        )[0]

        aligned_grid = np.rot90(
            native,
            -rotation,
            axes=(0, 1),
        )

        restored_block = np.rot90(
            aligned_grid,
            -rotation,
            axes=(-2, -1),
        )

        directions.append(restored_block)

    return np.ascontiguousarray(
        np.stack(directions, axis=0)
    )


class ModelSFusionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.image = load_rgb_u8(
            IMAGE_ROOT / "lr_x4" / "woman.png"
        )

        cls.lut = load_configured_lut(4, 5)

        (
            cls.output,
            cls.native,
            cls.restored,
            cls.fused,
            cls.rounded,
        ) = infer_image_blocks(
            cls.image,
            cls.lut,
            4,
            5,
            return_trace=True,
        )

    def test_restore_known_subpixel_rotations(self):
        native = np.empty(
            (4, 1, 1, 3, 2, 2),
            dtype=np.int32,
        )

        for direction in range(4):
            block = (
                np.arange(4, dtype=np.int32).reshape(2, 2)
                + direction * 10
            )
            native[direction, 0, 0] = block

        restored = restore_direction_blocks(native)

        expected = (
            [[0, 1], [2, 3]],
            [[12, 10], [13, 11]],
            [[23, 22], [21, 20]],
            [[31, 33], [30, 32]],
        )

        for direction in range(4):
            np.testing.assert_array_equal(
                restored[direction, 0, 0, 0],
                expected[direction],
            )

    def test_round_divide_ties_to_even_signed(self):
        values = np.array(
            [
                -41, -40, -39,
                -25, -24, -23,
                -9, -8, -7,
                7, 8, 9,
                23, 24, 25,
                39, 40, 41,
            ],
            dtype=np.int32,
        )

        expected = np.array(
            [
                -3, -2, -2,
                -2, -2, -1,
                -1, 0, 0,
                0, 0, 1,
                1, 2, 2,
                2, 2, 3,
            ],
            dtype=np.int32,
        )

        np.testing.assert_array_equal(
            round_divide_ties_to_even(values, 16),
            expected,
        )

    def test_fusion_does_not_divide_by_four(self):
        native = np.full(
            (4, 2, 3, 3, 2, 2),
            16 * 10,
            dtype=np.int32,
        )

        output, _, fused, rounded = (
            fuse_direction_numerators(
                native,
                4,
                return_trace=True,
            )
        )

        np.testing.assert_array_equal(
            fused,
            np.full(
                fused.shape,
                16 * 40,
                dtype=np.int32,
            ),
        )

        np.testing.assert_array_equal(
            rounded,
            np.full(
                rounded.shape,
                40,
                dtype=np.int32,
            ),
        )

        np.testing.assert_array_equal(
            output,
            np.full(
                output.shape,
                40,
                dtype=np.uint8,
            ),
        )

    def test_matches_original_rotation_method(self):
        image = np.ascontiguousarray(
            self.image[:5, :7]
        )

        direct_native = (
            interpolate_image_native_numerators(
                image,
                self.lut,
                4,
                5,
            )
        )

        direct_restored = restore_direction_blocks(
            direct_native
        )

        source_restored = (
            source_style_restored_numerators(
                image,
                self.lut,
                4,
                5,
            )
        )

        np.testing.assert_array_equal(
            direct_restored,
            source_restored,
        )

    def test_fixed_x4_5bit_fingerprint(self):
        self.assertEqual(
            self.output.shape,
            (86, 57, 3, 4, 4),
        )
        self.assertEqual(
            self.output.dtype,
            np.uint8,
        )

        np.testing.assert_array_equal(
            self.fused[0, 0, 0].reshape(-1),
            [
                1091, 1089, 1082, 1071,
                994, 988, 988, 981,
                981, 988, 988, 994,
                1071, 1082, 1089, 1091,
            ],
        )

        np.testing.assert_array_equal(
            self.rounded[0, 0, 0].reshape(-1),
            [
                34, 34, 34, 33,
                31, 31, 31, 31,
                31, 31, 31, 31,
                33, 34, 34, 34,
            ],
        )

        np.testing.assert_array_equal(
            self.output[0, 0, 0].reshape(-1),
            [
                34, 34, 34, 33,
                31, 31, 31, 31,
                31, 31, 31, 31,
                33, 34, 34, 34,
            ],
        )

    def test_rejects_invalid_inputs(self):
        valid = np.zeros(
            (4, 2, 3, 3, 2, 2),
            dtype=np.int32,
        )

        with self.assertRaises(TypeError):
            restore_direction_blocks(
                valid.astype(np.int16)
            )

        with self.assertRaises(ValueError):
            restore_direction_blocks(valid[:3])

        with self.assertRaises(TypeError):
            round_divide_ties_to_even(
                valid.astype(np.float32),
                16,
            )

        with self.assertRaises(ValueError):
            round_divide_ties_to_even(valid, 0)


if __name__ == "__main__":
    unittest.main()