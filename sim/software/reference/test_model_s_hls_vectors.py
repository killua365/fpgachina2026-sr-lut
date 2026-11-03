import hashlib
import unittest
from pathlib import Path

import numpy as np

from model_s_fusion import round_divide_ties_to_even
from model_s_interpolator import LUT_SPECS
from model_s_interpolator import file_sha256
from model_s_interpolator import interpolate_lut_numerators
from model_s_interpolator import load_model_s_lut
from model_s_output import run_model_s
from model_s_quantizer import quantize_u8


PROJECT_ROOT = Path(__file__).resolve().parents[3]

HLS_HEADER = (
    PROJECT_ROOT
    / "data/golden"
    / "hls_x2"
    / "sr_lut_x2_test_vectors.h"
)

EXPECTED_HEADER_SHA256 = (
    "A1BEDA14FFCBCC119150BB0630E0BD68"
    "7DE46B3FAD1A266B9BFCEC9AA7E49DFE"
)

EXPECTED_OUTPUT_SHA256 = (
    "31C3E186019CFC09433D99EFE053C32E"
    "4E8331C9A354DAD322FBEB685AE10B11"
)

INPUT_WORDS = np.array(
    [
        0x00FF0100,
        0x0011100F,
        0x0021201F,
        0x00FFFEF0,
        0x00803504,
        0x00C87F12,
        0x004000FF,
        0x00838281,
        0x00503010,
        0x00DC9664,
        0x000111EF,
        0x00FFFFFF,
    ],
    dtype=np.uint32,
)

EXPECTED_OUTPUT_WORDS = np.array(
    [
        0x00EE0601, 0x00F00701,
        0x002E090D, 0x00000613,
        0x00000100, 0x004B3834,
        0x00FFFEF5, 0x00FBF7D1,

        0x00F00701, 0x00EE0601,
        0x00300710, 0x00000A14,
        0x00000000, 0x003C2C3C,
        0x00FBF7D1, 0x00FFFEF5,

        0x00B81F03, 0x00B61F03,
        0x00908B00, 0x00974115,
        0x0063039C, 0x002605FF,
        0x007D876B, 0x00837F76,

        0x002E4408, 0x002F4508,
        0x00FFDA00, 0x00FF792F,
        0x00700DFF, 0x000300FF,
        0x00788D66, 0x0085936A,

        0x00422C09, 0x00452C08,
        0x00FF9C34, 0x00C0A483,
        0x000000F0, 0x001004E9,
        0x00FFFEFD, 0x00EEEEFE,

        0x00452C08, 0x00422C09,
        0x00FF9F33, 0x00BEA67E,
        0x000000F7, 0x00020CE7,
        0x00EEEEFE, 0x00FFFEFD,
    ],
    dtype=np.uint32,
)


def unpack_rgb_words(words, height, width):
    pixels = np.stack(
        (
            words & 0xFF,
            (words >> 8) & 0xFF,
            (words >> 16) & 0xFF,
        ),
        axis=1,
    ).astype(np.uint8)

    return np.ascontiguousarray(
        pixels.reshape(height, width, 3)
    )


def pack_rgb_words(image):
    pixels = image.astype(np.uint32)

    words = (
        pixels[:, :, 0]
        | (pixels[:, :, 1] << 8)
        | (pixels[:, :, 2] << 16)
    )

    return np.ascontiguousarray(words.reshape(-1))


class ModelSHLSVectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = LUT_SPECS[(2, 4)]

        cls.lut = load_model_s_lut(
            PROJECT_ROOT
            / "data/lut"
            / "original_npy"
            / spec["filename"],
            2,
            4,
        )

    def test_hls_header_integrity(self):
        self.assertEqual(
            file_sha256(HLS_HEADER),
            EXPECTED_HEADER_SHA256,
        )

    def test_hls_rgb_word_packing_order(self):
        image = unpack_rgb_words(
            INPUT_WORDS,
            3,
            4,
        )

        repacked = pack_rgb_words(image)

        np.testing.assert_array_equal(
            repacked,
            INPUT_WORDS,
        )

    def test_hls_core_numerators(self):
        inputs = np.array(
            [
                [0, 0, 0, 0],
                [255, 255, 255, 255],
                [31, 18, 53, 4],
                [240, 17, 128, 255],
                [53, 48, 127, 150],
            ],
            dtype=np.uint8,
        )

        expected = np.array(
            [
                [112, 16, -64, -64],
                [2010, 1290, -207, 988],
                [325, 79, 33, -65],
                [2032, -41, 186, 721],
                [592, 201, -90, 115],
            ],
            dtype=np.int32,
        )

        lower, _, remainders = quantize_u8(
            inputs,
            4,
        )

        actual = interpolate_lut_numerators(
            self.lut,
            lower,
            remainders,
            2,
            4,
        ).reshape(5, 4)

        np.testing.assert_array_equal(
            actual,
            expected,
        )

    def test_hls_rounding_and_clip(self):
        numerators = np.array(
            [
                -17, -8, -1,
                0, 7, 8, 9,
                23, 24, 25,
                4072, 4088, 4096,
            ],
            dtype=np.int32,
        )

        expected = np.array(
            [
                0, 0, 0,
                0, 0, 0, 1,
                1, 2, 2,
                254, 255, 255,
            ],
            dtype=np.uint8,
        )

        rounded = round_divide_ties_to_even(
            numerators,
            16,
        )

        actual = np.clip(
            rounded,
            0,
            255,
        ).astype(np.uint8)

        np.testing.assert_array_equal(
            actual,
            expected,
        )

    def test_hls_fixed_image_vector(self):
        input_image = unpack_rgb_words(
            INPUT_WORDS,
            3,
            4,
        )

        expected = unpack_rgb_words(
            EXPECTED_OUTPUT_WORDS,
            6,
            8,
        )

        actual = run_model_s(
            input_image,
            self.lut,
            2,
            4,
        )

        self.assertEqual(
            actual.shape,
            (6, 8, 3),
        )

        np.testing.assert_array_equal(
            actual,
            expected,
        )

        actual_hash = hashlib.sha256(
            actual.tobytes(order="C")
        ).hexdigest().upper()

        self.assertEqual(
            actual_hash,
            EXPECTED_OUTPUT_SHA256,
        )


if __name__ == "__main__":
    unittest.main()