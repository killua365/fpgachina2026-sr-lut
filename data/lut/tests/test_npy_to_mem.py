import json
import sys
import unittest
from pathlib import Path

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[3]
REFERENCE_ROOT = PROJECT_ROOT / "src/reference"
TOOLS_ROOT = PROJECT_ROOT / "data/lut" / "tools"

for module_root in (REFERENCE_ROOT, TOOLS_ROOT):
    module_root_text = str(module_root)

    if module_root_text not in sys.path:
        sys.path.insert(0, module_root_text)


from model_s_interpolator import LUT_SPECS
from model_s_interpolator import file_sha256
from model_s_interpolator import load_model_s_lut
from npy_to_mem import mem_line_to_row
from npy_to_mem import read_mem_rows
from npy_to_mem import row_to_mem_line
from npy_to_mem import rows_to_mem_lines

LUT_ROOT = (
    PROJECT_ROOT
    / "data/lut"
    / "original_npy"
)

MEM_ROOT = (
    PROJECT_ROOT
    / "data/lut"
    / "bram_mem"
)

EXPECTED_FILES = {
    "x2_4bit": {
        "filename": "model_s_x2_4bit.mem",
        "scale": 2,
        "sampling_bits": 4,
        "entries": 83521,
        "values_per_entry": 4,
        "line_width": 8,
        "file_size": 751689,
        "mem_sha256": (
            "0C5415C57BCFC6BAC675BFC13C815B9B"
            "8B8505C41F8E9A7D78753F74759DF5B6"
        ),
        "payload_sha256": (
            "6ACAFAC5D00977B812E37D6081BC565E"
            "C75C19BDF7C90AAE92D8F87FF31696E2"
        ),
        "first_line": "FCFC0107",
        "last_line": "3EF3517E",
    },
    "x4_5bit": {
        "filename": "model_s_x4_5bit.mem",
        "scale": 4,
        "sampling_bits": 5,
        "entries": 6561,
        "values_per_entry": 16,
        "line_width": 32,
        "file_size": 216513,
        "mem_sha256": (
            "A8A377838900967D0EEBC81F47943490"
            "102DAF801B11970A4008599CC4058F8D"
        ),
        "payload_sha256": (
            "C473F5B882E18A96AEB07B38FA0CC337"
            "9CB8531F0857F927F0BFFE2B9C4EC5C1"
        ),
        "first_line": (
            "03FAFEFE03F507FF"
            "FDFF0607F9F80A06"
        ),
        "last_line": (
            "7F4A7F7F7F817F7E"
            "E27F7F7F8181547F"
        ),
    },
}


class NpyToMemTests(unittest.TestCase):
    def test_signed_byte_order(self):
        row = np.array(
            [7, 1, -4, -4],
            dtype=np.int8,
        )

        line = row_to_mem_line(row)

        self.assertEqual(
            line,
            "FCFC0107",
        )

        reconstructed = mem_line_to_row(
            line,
            4,
        )

        np.testing.assert_array_equal(
            reconstructed,
            row,
        )

        word = int(line, 16)

        self.assertEqual(word & 0xFF, 7)
        self.assertEqual(
            (word >> 8) & 0xFF,
            1,
        )
        self.assertEqual(
            (word >> 16) & 0xFF,
            0xFC,
        )

    def test_generated_mem_file_identity(self):
        for name, expected in (
            EXPECTED_FILES.items()
        ):
            with self.subTest(configuration=name):
                path = (
                    MEM_ROOT
                    / expected["filename"]
                )

                self.assertTrue(path.is_file())
                self.assertEqual(
                    path.stat().st_size,
                    expected["file_size"],
                )
                self.assertEqual(
                    file_sha256(path),
                    expected["mem_sha256"],
                )

                raw_file = path.read_bytes()

                self.assertNotIn(
                    b"\r",
                    raw_file,
                )

                lines = raw_file.decode(
                    "ascii"
                ).splitlines()

                self.assertEqual(
                    len(lines),
                    expected["entries"],
                )

                self.assertTrue(
                    all(
                        len(line)
                        == expected["line_width"]
                        for line in lines
                    )
                )

                self.assertEqual(
                    lines[0],
                    expected["first_line"],
                )

                self.assertEqual(
                    lines[-1],
                    expected["last_line"],
                )

    def test_mem_reconstructs_source_payload(self):
        for name, expected in (
            EXPECTED_FILES.items()
        ):
            with self.subTest(configuration=name):
                scale = expected["scale"]
                sampling_bits = expected[
                    "sampling_bits"
                ]

                spec = LUT_SPECS[
                    (scale, sampling_bits)
                ]

                source = load_model_s_lut(
                    LUT_ROOT / spec["filename"],
                    scale,
                    sampling_bits,
                )

                reconstructed = read_mem_rows(
                    MEM_ROOT
                    / expected["filename"],
                    expected["values_per_entry"],
                )

                np.testing.assert_array_equal(
                    reconstructed,
                    source,
                )

    def test_manifest_matches_files(self):
        manifest_path = (
            MEM_ROOT
            / "lut_mem_manifest.json"
        )

        self.assertTrue(
            manifest_path.is_file()
        )

        manifest = json.loads(
            manifest_path.read_text(
                encoding="ascii"
            )
        )

        records = {
            record["name"]: record
            for record in manifest[
                "configurations"
            ]
        }

        self.assertEqual(
            set(records),
            set(EXPECTED_FILES),
        )

        for name, expected in (
            EXPECTED_FILES.items()
        ):
            record = records[name]

            self.assertEqual(
                record["entries"],
                expected["entries"],
            )
            self.assertEqual(
                record["values_per_entry"],
                expected["values_per_entry"],
            )
            self.assertEqual(
                record["payload_sha256"],
                expected["payload_sha256"],
            )
            self.assertEqual(
                record["mem_sha256"],
                expected["mem_sha256"],
            )
            self.assertEqual(
                record["first_line"],
                expected["first_line"],
            )
            self.assertEqual(
                record["last_line"],
                expected["last_line"],
            )

    def test_rejects_invalid_data(self):
        with self.assertRaises(TypeError):
            row_to_mem_line(
                np.zeros(4, dtype=np.int16)
            )

        with self.assertRaises(ValueError):
            row_to_mem_line(
                np.zeros(5, dtype=np.int8)
            )

        with self.assertRaises(TypeError):
            rows_to_mem_lines(
                np.zeros(
                    (2, 4),
                    dtype=np.float32,
                )
            )

        with self.assertRaises(ValueError):
            mem_line_to_row(
                "ABC",
                4,
            )

        with self.assertRaises(ValueError):
            mem_line_to_row(
                "GGGGGGGG",
                4,
            )


if __name__ == "__main__":
    unittest.main()
    