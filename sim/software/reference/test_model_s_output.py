import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from model_s_input import load_rgb_u8
from model_s_interpolator import LUT_SPECS
from model_s_interpolator import file_sha256
from model_s_interpolator import load_model_s_lut
from model_s_output import run_model_s
from model_s_output import save_rgb_u8
from model_s_output import stitch_output_blocks


PROJECT_ROOT = Path(__file__).resolve().parents[3]

LUT_ROOT = (
    PROJECT_ROOT
    / "data/lut"
    / "original_npy"
)

IMAGE_ROOT = (
    PROJECT_ROOT
    / "data/golden"
    / "source_images"
)

GOLDEN_ROOT = (
    PROJECT_ROOT
    / "data/golden"
    / "reference_baselines"
    / "x4_4bit"
)

GOLDEN_HASHES = {
    "baby_gt_lut_interp_4bit.png": (
        "8466E02028CCE7B3B6762BF8E5A012D2"
        "668482A49A0C7CDED7D92BBB9E09F9A9"
    ),
    "bird_gt_lut_interp_4bit.png": (
        "1F9BCEE8C6C50138FB24A8E4273F390D"
        "997D532745058004FA19CC62AAE7842B"
    ),
    "butterfly_gt_lut_interp_4bit.png": (
        "A88C4601F86B73DAB52E7539A3C6A106"
        "2466766C632A1829D084E0261F10A253"
    ),
    "head_gt_lut_interp_4bit.png": (
        "E716F287A86B9BD1885E1A728109D101"
        "B0EECA1DF7DA825AFB91DC544C158017"
    ),
    "woman_gt_lut_interp_4bit.png": (
        "1E6B190CBA73D7FE2B05052AD5C37283"
        "891420A448CEBE21DC81FDF063896631"
    ),
}


def load_configured_lut(scale, sampling_bits):
    filename = LUT_SPECS[
        (scale, sampling_bits)
    ]["filename"]

    return load_model_s_lut(
        LUT_ROOT / filename,
        scale,
        sampling_bits,
    )


def load_rgb_copy(path):
    with Image.open(path) as source:
        return np.array(
            source.convert("RGB"),
            dtype=np.uint8,
            copy=True,
        )


class ModelSOutputTests(unittest.TestCase):
    def test_stitch_synthetic_mapping(self):
        blocks = np.empty(
            (2, 2, 3, 2, 2),
            dtype=np.uint8,
        )

        expected = np.empty(
            (4, 4, 3),
            dtype=np.uint8,
        )

        for y in range(2):
            for x in range(2):
                for channel in range(3):
                    for dy in range(2):
                        for dx in range(2):
                            value = (
                                100 * y
                                + 40 * x
                                + 10 * channel
                                + 2 * dy
                                + dx
                            )

                            blocks[
                                y, x, channel, dy, dx
                            ] = value

                            expected[
                                2 * y + dy,
                                2 * x + dx,
                                channel,
                            ] = value

        actual = stitch_output_blocks(blocks)

        np.testing.assert_array_equal(
            actual,
            expected,
        )

    def test_golden_integrity(self):
        actual_names = sorted(
            path.name
            for path in GOLDEN_ROOT.glob("*.png")
        )

        self.assertEqual(
            actual_names,
            sorted(GOLDEN_HASHES),
        )

        for filename, expected_hash in (
            GOLDEN_HASHES.items()
        ):
            with self.subTest(filename=filename):
                self.assertEqual(
                    file_sha256(
                        GOLDEN_ROOT / filename
                    ),
                    expected_hash,
                )

    def test_all_five_x4_4bit_images_match(self):
        model_lut = load_configured_lut(4, 4)

        for stem in (
            "baby",
            "bird",
            "butterfly",
            "head",
            "woman",
        ):
            with self.subTest(image=stem):
                image = load_rgb_u8(
                    IMAGE_ROOT
                    / "lr_x4"
                    / f"{stem}.png"
                )

                actual = run_model_s(
                    image,
                    model_lut,
                    4,
                    4,
                )

                expected = load_rgb_copy(
                    GOLDEN_ROOT
                    / (
                        f"{stem}_gt_"
                        "lut_interp_4bit.png"
                    )
                )

                self.assertEqual(
                    actual.shape,
                    (
                        image.shape[0] * 4,
                        image.shape[1] * 4,
                        3,
                    ),
                )

                np.testing.assert_array_equal(
                    actual,
                    expected,
                )

    def test_saved_output_round_trip(self):
        image = load_rgb_u8(
            IMAGE_ROOT / "lr_x4" / "woman.png"
        )
        expected = run_model_s(
            image,
            load_configured_lut(4, 4),
            4,
            4,
        )

        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "woman.png"
            save_rgb_u8(expected, output_path)
            actual = load_rgb_copy(output_path)

        np.testing.assert_array_equal(actual, expected)

    def test_other_supported_output_shapes(self):
        configurations = (
            (
                IMAGE_ROOT
                / "lr_x2"
                / "woman_gt.png",
                2,
                4,
            ),
            (
                IMAGE_ROOT
                / "lr_x4"
                / "woman.png",
                4,
                5,
            ),
        )

        for image_path, scale, bits in configurations:
            with self.subTest(
                scale=scale,
                sampling_bits=bits,
            ):
                image = load_rgb_u8(image_path)

                output = run_model_s(
                    image,
                    load_configured_lut(scale, bits),
                    scale,
                    bits,
                )

                self.assertEqual(
                    output.shape,
                    (
                        image.shape[0] * scale,
                        image.shape[1] * scale,
                        3,
                    ),
                )

                self.assertEqual(
                    output.dtype,
                    np.uint8,
                )

    def test_rejects_invalid_blocks(self):
        valid = np.zeros(
            (2, 3, 3, 2, 2),
            dtype=np.uint8,
        )

        with self.assertRaises(TypeError):
            stitch_output_blocks(
                valid.astype(np.int16)
            )

        with self.assertRaises(ValueError):
            stitch_output_blocks(
                valid[:, :, :2]
            )

        with self.assertRaises(ValueError):
            stitch_output_blocks(
                valid[:, :, :, :, 0]
            )


if __name__ == "__main__":
    unittest.main()
