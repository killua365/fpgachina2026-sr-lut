import csv
import hashlib
import tempfile
import unittest
from pathlib import Path

import numpy as np

from model_s_evaluation import calculate_psnr_from_y
from model_s_evaluation import calculate_psnr_rgb
from model_s_evaluation import calculate_ssim_from_y
from model_s_evaluation import calculate_ssim_rgb
from model_s_evaluation import evaluate
from model_s_evaluation import rgb_to_ycbcr_original
from model_s_input import load_rgb_u8


PROJECT_ROOT = Path(__file__).resolve().parents[3]

GOLDEN_ROOT = (
    PROJECT_ROOT
    / "data/golden"
    / "reference_baselines"
    / "x4_4bit"
)

EXPECTED_AVERAGES = {
    "x2_4bit": 35.510184141890178,
    "x4_4bit": 29.831005260923369,
    "x4_5bit": 29.631128648960662,
}

EXPECTED_SSIM_AVERAGES = {
    "x2_4bit": 0.947677078100983,
    "x4_4bit": 0.848360007156998,
    "x4_5bit": 0.842479563925467,
}

EXPECTED_RAW_HASHES = {
    ("x2_4bit", "baby"): (
        "42C606E4A110D2E0A1B48AA9AED9CC27F"
        "9055E28322D4B30A98397C43F5A19E3"
    ),
    ("x2_4bit", "bird"): (
        "0E608F22210A23796EA3E76B82B75EBE9"
        "693AEC0548486512815608AC96DF404"
    ),
    ("x2_4bit", "butterfly"): (
        "64EDC1EFFD26472CF7A2F6C65071EE0B8"
        "2656C667028049E894F3C40C68E54E2"
    ),
    ("x2_4bit", "head"): (
        "853AE185AE34C1BDBA0D6312DACFAD2E9"
        "B54F9D61D657E99BAADC173101A2749"
    ),
    ("x2_4bit", "woman"): (
        "D2B6371C035304C7D064368CBE7CCA7A5"
        "A30BE46C435D93300926BAAFC40BDAE"
    ),
    ("x4_4bit", "baby"): (
        "7A67BEC54C2378B4A1AE50E1BD404814E"
        "EB0D66B6DD8394A0364E9E39F53F81F"
    ),
    ("x4_4bit", "bird"): (
        "5EC24890F9F2E89C6D2D6AD3E53EF42C"
        "426D7AD4498EE614CF5128A2940FDC44"
    ),
    ("x4_4bit", "butterfly"): (
        "E032291D934ED45BF0F1E30DBEFAEF1E3"
        "B668A9126BDF76130D940791C5F3C80"
    ),
    ("x4_4bit", "head"): (
        "8B1AC7EFB53128E5FF555F43256FB6741"
        "BF7FEB533DD3DE3202CE1022886DE80"
    ),
    ("x4_4bit", "woman"): (
        "909DA3D7C6665BF15C8B727B47D59A45"
        "1CC2AF8EC4C4D158BFCFF20F41F4E2C8"
    ),
    ("x4_5bit", "baby"): (
        "36E549675F435CAD9338CA30807FA78D01"
        "2656F2D648CC3DC071A4DE7ED4F2F1"
    ),
    ("x4_5bit", "bird"): (
        "B382F1E34DEEA520391104D8D0EA4B342"
        "CBB0BE40A24AAF3ABE8F97D8CF71758"
    ),
    ("x4_5bit", "butterfly"): (
        "A8E7FC0AFA61D8EBC904A9B37A5FD2E3"
        "A5692F1A08C02E779F67A53FC6D63E14"
    ),
    ("x4_5bit", "head"): (
        "C3A03DD2E6F3FA1E6DFC74AABB1E5832"
        "63C6DF43CADD5C87F79957A550D93D76"
    ),
    ("x4_5bit", "woman"): (
        "25E99D2C4B1433079B47000AD0A7DD72"
        "B9BF4CB02319464E78885275BA498277"
    ),
}


def raw_rgb_sha256(image):
    return hashlib.sha256(
        image.tobytes(order="C")
    ).hexdigest().upper()


class ModelSEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary_directory = tempfile.TemporaryDirectory()
        paths = evaluate(Path(cls.temporary_directory.name))
        cls.result_root = paths["result_root"]
        cls.metrics_path = paths["metrics_path"]
        cls.report_path = paths["report_path"]

    @classmethod
    def tearDownClass(cls):
        cls.temporary_directory.cleanup()

    def test_rgb_to_ycbcr_black_and_white(self):
        image = np.array(
            [[[0, 0, 0], [255, 255, 255]]],
            dtype=np.uint8,
        )

        converted = rgb_to_ycbcr_original(image)

        np.testing.assert_allclose(
            converted[0, 0],
            [16, 128, 128],
            atol=1e-10,
        )

        np.testing.assert_allclose(
            converted[0, 1],
            [235, 128, 128],
            atol=1e-10,
        )

    def test_known_psnr_value(self):
        reference = np.zeros(
            (12, 12),
            dtype=np.float32,
        )

        output = np.full(
            (12, 12),
            10,
            dtype=np.float32,
        )

        actual = calculate_psnr_from_y(
            reference,
            output,
            crop_border=4,
        )

        expected = float(
            20 * np.log10(255.0 / 10.0)
        )

        self.assertAlmostEqual(
            actual,
            expected,
            places=12,
        )

    def test_rgb_metrics_identical_and_colour_change(self):
        reference = np.full((12, 12, 3), 128, dtype=np.uint8)
        self.assertEqual(calculate_psnr_rgb(reference, reference), float("inf"))
        self.assertAlmostEqual(calculate_ssim_rgb(reference, reference), 1.0, places=12)
        changed = reference.copy()
        changed[..., 0] = 180
        self.assertLess(calculate_psnr_rgb(reference, changed), float("inf"))
        self.assertLess(calculate_ssim_rgb(reference, changed), 1.0)

    def test_ssim_identical_and_changed(self):
        reference = np.arange(32 * 32, dtype=np.float64).reshape(32, 32) % 256
        self.assertAlmostEqual(
            calculate_ssim_from_y(reference, reference.copy()),
            1.0,
            places=12,
        )
        changed = reference.copy()
        changed[8:24, 8:24] += 20
        self.assertLess(calculate_ssim_from_y(reference, changed), 1.0)

    def test_metrics_csv_and_averages(self):
        self.assertTrue(self.metrics_path.is_file())

        with self.metrics_path.open(
            "r",
            encoding="utf-8",
            newline="",
        ) as source:
            rows = list(csv.DictReader(source))

        self.assertEqual(len(rows), 15)

        grouped = {}
        grouped_ssim = {}

        for row in rows:
            configuration = row["configuration"]
            image_name = row["image"]

            grouped.setdefault(
                configuration,
                [],
            ).append(float(row["psnr_y_db"]))

            grouped_ssim.setdefault(
                configuration,
                [],
            ).append(float(row["ssim_y"]))

            self.assertGreater(float(row["ssim_y"]), 0.0)
            self.assertLessEqual(float(row["ssim_y"]), 1.0)
            self.assertGreater(float(row["ssim_rgb"]), 0.0)
            self.assertLessEqual(float(row["ssim_rgb"]), 1.0)
            self.assertGreater(float(row["psnr_rgb_db"]), 0.0)

            self.assertEqual(
                row["raw_rgb_sha256"],
                EXPECTED_RAW_HASHES[
                    (configuration, image_name)
                ],
            )

        for configuration, expected in (
            EXPECTED_AVERAGES.items()
        ):
            actual = float(
                np.mean(
                    np.asarray(grouped[configuration])
                )
            )

            self.assertAlmostEqual(
                actual,
                expected,
                places=9,
            )

        for configuration, expected in EXPECTED_SSIM_AVERAGES.items():
            actual = float(np.mean(np.asarray(grouped_ssim[configuration])))
            self.assertAlmostEqual(actual, expected, places=12)

    def test_saved_output_raw_hashes(self):
        for key, expected_hash in (
            EXPECTED_RAW_HASHES.items()
        ):
            configuration, image_name = key

            with self.subTest(
                configuration=configuration,
                image=image_name,
            ):
                image = load_rgb_u8(
                    self.result_root
                    / configuration
                    / f"{image_name}.png"
                )

                self.assertEqual(
                    raw_rgb_sha256(image),
                    expected_hash,
                )

    def test_x4_4bit_matches_existing_outputs(self):
        for image_name in (
            "baby",
            "bird",
            "butterfly",
            "head",
            "woman",
        ):
            with self.subTest(image=image_name):
                actual = load_rgb_u8(
                    self.result_root
                    / "x4_4bit"
                    / f"{image_name}.png"
                )

                expected = load_rgb_u8(
                    GOLDEN_ROOT
                    / (
                        f"{image_name}_gt_"
                        "lut_interp_4bit.png"
                    )
                )

                np.testing.assert_array_equal(
                    actual,
                    expected,
                )

    def test_stage_b_report_exists(self):
        self.assertTrue(self.report_path.is_file())

        report = self.report_path.read_text(
            encoding="utf-8"
        )

        for configuration in EXPECTED_AVERAGES:
            self.assertIn(
                configuration,
                report,
            )

        self.assertIn(
            "Mismatch channels: 0",
            report,
        )


if __name__ == "__main__":
    unittest.main()
