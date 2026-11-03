import hashlib
import json
import sys
import unittest
from pathlib import Path

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[3]
REFERENCE_ROOT = PROJECT_ROOT / "src/reference"
TOOLS_ROOT = PROJECT_ROOT / "data/golden" / "tools"
VECTOR_ROOT = PROJECT_ROOT / "data/golden" / "rtl_core"
LUT_ROOT = PROJECT_ROOT / "data/lut" / "original_npy"

for module_root in (REFERENCE_ROOT, TOOLS_ROOT):
    module_root_text = str(module_root)

    if module_root_text not in sys.path:
        sys.path.insert(0, module_root_text)


from generate_rtl_core_vectors import build_core_cases
from generate_rtl_core_vectors import build_core_lines
from generate_rtl_core_vectors import build_core_numerators
from generate_rtl_core_vectors import build_core_vectors
from generate_rtl_core_vectors import build_directed_cases
from generate_rtl_core_vectors import build_quantizer_lines
from generate_rtl_core_vectors import build_random_cases
from generate_rtl_core_vectors import NUMERATOR_BITS
from generate_rtl_core_vectors import WEIGHT_BITS
from generate_rtl_core_vectors import pack_signed_hex_line
from generate_rtl_core_vectors import pack_unsigned_hex_line
from model_s_address import vertex_lut_address
from model_s_interpolator import get_lut_spec
from model_s_interpolator import interpolate_lut_numerators
from model_s_interpolator import load_model_s_lut
from model_s_quantizer import quantize_u8


EXPECTED_FILES = {
    "x2_4bit": {
        "scale": 2,
        "sampling_bits": 4,
        "sha256": (
            "50C988219007746DCB314E3DCED4BDDF"
            "003223D492C23176C67C327B2D452804"
        ),
        "known_lines": {
            0: "00010000",
            15: "0F01000F",
            16: "00020110",
            255: "0F100FFF",
        },
        "core_sha256": {
            "case_labels.txt": (
                "59485BABBD8476DBCAA9559111BFFFD5"
                "DD908CB3EDBE156431F64F1B5A156D75"
            ),
            "inputs.mem": (
                "2031BC0C63FF9E79F1470FBA09C42526"
                "5C51DE57AF1C9989179E700EB4C4CC62"
            ),
            "lower.mem": (
                "B301E97BFE8B0473545A8DF70BF62089"
                "AEF8CCF428C4A0714D5B2193919AE562"
            ),
            "remainders.mem": (
                "FE9A1AD2F00B2323AF8C3B140215BD04"
                "9C87CF9F0B894CCD4EEEC6619BE5FC87"
            ),
            "masks.mem": (
                "E3FE591B7CA73CBA51E5F2A1F0780D9F"
                "4CF4AC4D816D6F148E9AE61B8489BECA"
            ),
            "weights.mem": (
                "83D429BCD359E60B7B185B5CEF8AA4B8"
                "61C8A8FA527D916B94AFE8C2DFD7285B"
            ),
            "addresses.mem": (
                "E09ECD270093B7A69DC37DC770ADCD35"
                "8131E0440C3678B550226E816231C75C"
            ),
            "numerators.mem": (
                "F934182B7E582AED526251D2528C4682"
                "5981701457B5C34591F11891E3CA8CF3"
            ),
        },
        "weight_minimum": -11,
        "negative_case_count": 279,
        "lut_depth": 83_521,
        "address_bits": 17,
        "numerator_minimum": -1_028,
        "numerator_maximum": 2_032,
        "manifest_sha256": "080C6BAF0F71A036C37CA260DC2B6BD0390F5AA8DEB308A2DDF89E7D55BFB598",
    },
    "x4_5bit": {
        "scale": 4,
        "sampling_bits": 5,
        "sha256": (
            "A9759E787528EC96E7A359479CE2B5835"
            "5FD7846A9F941A6826C4F0FC64EC64A"
        ),
        "known_lines": {
            0: "00010000",
            15: "0F01000F",
            16: "10010010",
            255: "1F0807FF",
        },
        "core_sha256": {
            "case_labels.txt": (
                "59485BABBD8476DBCAA9559111BFFFD5"
                "DD908CB3EDBE156431F64F1B5A156D75"
            ),
            "inputs.mem": (
                "1CDBEEF3FB3CD6C7F094AA3A161CCB0A"
                "48743BD9969194B837528DE74422E114"
            ),
            "lower.mem": (
                "93F0E5BD3A6DF6E077BEDCD4E26C8F2F"
                "5D44B6A54C4C34307994722364959AF2"
            ),
            "remainders.mem": (
                "2A2C47C88F1C8C1271DDBB6986E4B2C5"
                "2F1526D6E7D907061882DFEB7CBF46AC"
            ),
            "masks.mem": (
                "086A3C7587F0930112518004458620BD"
                "C01A56A8198A98A2A71188E53FB2B024"
            ),
            "weights.mem": (
                "9D601F6166446019E0282130A43C87A7"
                "F2F1B35C080A193D17C22307BBEB3E7B"
            ),
            "addresses.mem": (
                "EBD8743BBF4BD1FF797E90BD32AEE071"
                "9003BFDF45D8B61F62306D2D112281FA"
            ),
            "numerators.mem": (
                "59AE078B0CEAC2EC7F7B5C596FE15D99"
                "28E3712A953C7B203E7EDBB67DD78626"
            ),
        },
        "weight_minimum": -25,
        "negative_case_count": 348,
        "lut_depth": 6_561,
        "address_bits": 13,
        "numerator_minimum": -4_064,
        "numerator_maximum": 4_064,
        "manifest_sha256": "D7C0E2B6767FF46041F557CAEA6C8127FC6B63F1014A15B955A02AB4611DABC0",
    },
}


def unpack_four_bytes(line):
    word = int(line, 16)

    return tuple(
        (word >> (8 * index)) & 0xFF
        for index in range(4)
    )


def unpack_hex_fields(line, field_bits, count, signed=False):
    word = int(line, 16)
    field_mask = (1 << field_bits) - 1
    sign_bit = 1 << (field_bits - 1)
    full_range = 1 << field_bits
    values = []

    for index in range(count):
        value = (
            word >> (index * field_bits)
        ) & field_mask

        if signed and value & sign_bit:
            value -= full_range

        values.append(value)

    return tuple(values)


class RTLCoreVectorTests(unittest.TestCase):
    def test_unsigned_packing_and_rejections(self):
        self.assertEqual(
            pack_unsigned_hex_line(
                (0x11, 0x22, 0x33, 0x44),
                8,
            ),
            "44332211",
        )

        self.assertEqual(
            pack_unsigned_hex_line(
                (1, 2, 3, 4),
                4,
            ),
            "4321",
        )

        with self.assertRaises(ValueError):
            pack_unsigned_hex_line((), 8)

        with self.assertRaises(ValueError):
            pack_unsigned_hex_line((256,), 8)

        with self.assertRaises(ValueError):
            pack_unsigned_hex_line((-1,), 8)

        with self.assertRaises(TypeError):
            pack_unsigned_hex_line((True,), 8)

        with self.assertRaises(ValueError):
            pack_unsigned_hex_line((0,), 0)

    def test_signed_packing_and_rejections(self):
        self.assertEqual(
            pack_signed_hex_line(
                (-1, -128, 0, 127),
                8,
            ),
            "7F0080FF",
        )
        self.assertEqual(
            pack_signed_hex_line(
                (-1, -8, 0, 7),
                4,
            ),
            "708F",
        )

        with self.assertRaises(ValueError):
            pack_signed_hex_line((), 8)

        with self.assertRaises(ValueError):
            pack_signed_hex_line((-129,), 8)

        with self.assertRaises(ValueError):
            pack_signed_hex_line((128,), 8)

        with self.assertRaises(TypeError):
            pack_signed_hex_line((True,), 8)

        with self.assertRaises(ValueError):
            pack_signed_hex_line((0,), 0)

    def test_directed_case_coverage(self):
        for name, expected in EXPECTED_FILES.items():
            with self.subTest(configuration=name):
                sampling_bits = expected["sampling_bits"]
                q = 1 << sampling_bits

                labels, inputs = build_directed_cases(
                    sampling_bits
                )

                self.assertEqual(len(labels), 41)
                self.assertEqual(inputs.shape, (41, 4))
                self.assertEqual(inputs.dtype, np.dtype(np.uint8))
                self.assertEqual(len(set(labels)), 41)
                self.assertEqual(
                    len(
                        {
                            tuple(row)
                            for row in inputs.tolist()
                        }
                    ),
                    41,
                )

                self.assertEqual(
                    sum(
                        label.startswith("boundary_")
                        for label in labels
                    ),
                    8,
                )
                self.assertEqual(
                    sum(
                        label.startswith("strict_")
                        for label in labels
                    ),
                    24,
                )
                self.assertEqual(
                    sum(
                        label.startswith("tie_")
                        for label in labels
                    ),
                    9,
                )

                lower, _, remainders = quantize_u8(
                    inputs,
                    sampling_bits,
                )

                reconstructed = (
                    lower.astype(np.uint16) * q
                    + remainders.astype(np.uint16)
                )
                np.testing.assert_array_equal(
                    reconstructed,
                    inputs.astype(np.uint16),
                )

                for label, row in zip(
                    labels[8:32],
                    remainders[8:32],
                ):
                    order = sorted(
                        range(4),
                        key=lambda index: int(row[index]),
                        reverse=True,
                    )
                    expected_label = "strict_" + "".join(
                        "abcd"[index]
                        for index in order
                    )
                    self.assertEqual(label, expected_label)
                    self.assertEqual(
                        len(set(row.tolist())),
                        4,
                    )

                for row in remainders[32:]:
                    self.assertLess(
                        len(set(row.tolist())),
                        4,
                    )

    def test_fixed_random_case_coverage(self):
        labels, inputs = build_random_cases()
        repeated_labels, repeated_inputs = (
            build_random_cases()
        )

        self.assertEqual(len(labels), 10_000)
        self.assertEqual(inputs.shape, (10_000, 4))
        self.assertEqual(inputs.dtype, np.dtype(np.uint8))
        self.assertEqual(labels[0], "random_00000")
        self.assertEqual(labels[-1], "random_09999")
        self.assertEqual(inputs[0].tolist(), [60, 106, 83, 208])
        self.assertEqual(inputs[-1].tolist(), [106, 114, 1, 243])
        self.assertEqual(len(set(labels)), 10_000)
        self.assertEqual(
            len(
                {
                    tuple(row)
                    for row in inputs.tolist()
                }
            ),
            10_000,
        )

        self.assertEqual(repeated_labels, labels)
        np.testing.assert_array_equal(
            repeated_inputs,
            inputs,
        )

        actual_hash = hashlib.sha256(
            inputs.tobytes(order="C")
        ).hexdigest().upper()
        self.assertEqual(
            actual_hash,
            (
                "482ADB0643CFA9075923CA28C2FCE061"
                "B44EDE80BC1C0ECF3111A35693768B06"
            ),
        )

        for name, expected in EXPECTED_FILES.items():
            with self.subTest(configuration=name):
                sampling_bits = expected["sampling_bits"]
                directed_labels, directed_inputs = (
                    build_directed_cases(sampling_bits)
                )
                core_labels, core_inputs = build_core_cases(
                    sampling_bits
                )

                self.assertEqual(len(core_labels), 10_041)
                self.assertEqual(core_inputs.shape, (10_041, 4))
                self.assertEqual(
                    core_labels[:41],
                    directed_labels,
                )
                self.assertEqual(
                    core_labels[41:],
                    labels,
                )
                np.testing.assert_array_equal(
                    core_inputs[:41],
                    directed_inputs,
                )
                np.testing.assert_array_equal(
                    core_inputs[41:],
                    inputs,
                )

    def test_core_vectors_and_packing(self):
        common_widths = {
            "inputs.mem": 8,
            "lower.mem": 8,
            "remainders.mem": 8,
            "masks.mem": 5,
            "weights.mem": (5 * WEIGHT_BITS + 3) // 4,
            "addresses.mem": 40,
        }

        for name, expected in EXPECTED_FILES.items():
            with self.subTest(configuration=name):
                scale = expected["scale"]
                sampling_bits = expected["sampling_bits"]
                q = 1 << sampling_bits
                widths = {
                    **common_widths,
                    "numerators.mem": (
                        scale * scale * NUMERATOR_BITS + 3
                    ) // 4,
                }
                (
                    labels,
                    inputs,
                    lower,
                    remainders,
                    masks,
                    weights,
                    addresses,
                ) = build_core_vectors(sampling_bits)
                built_labels, files = build_core_lines(
                    scale,
                    sampling_bits
                )

                self.assertEqual(built_labels, labels)
                self.assertEqual(inputs.shape, (10_041, 4))
                self.assertEqual(lower.shape, (10_041, 4))
                self.assertEqual(remainders.shape, (10_041, 4))
                self.assertEqual(masks.shape, (10_041, 5))
                self.assertEqual(weights.shape, (10_041, 5))
                self.assertEqual(addresses.shape, (10_041, 5))
                self.assertEqual(masks.dtype, np.dtype(np.uint8))
                self.assertEqual(weights.dtype, np.dtype(np.int16))
                self.assertEqual(addresses.dtype, np.dtype(np.uint32))

                self.assertTrue(np.all(masks[:, 0] == 0))
                self.assertTrue(np.all(masks[:, -1] == 15))
                self.assertTrue(np.all(weights.sum(axis=1) == q))
                self.assertEqual(int(weights.max()), q)
                self.assertEqual(
                    int(weights.min()),
                    expected["weight_minimum"],
                )
                self.assertEqual(
                    int(np.any(weights < 0, axis=1).sum()),
                    expected["negative_case_count"],
                )
                self.assertEqual(int(addresses.min()), 0)
                self.assertEqual(
                    int(addresses.max()),
                    expected["lut_depth"] - 1,
                )
                self.assertTrue(
                    np.all(
                        addresses
                        < (1 << expected["address_bits"])
                    )
                )

                for case_index in (0, 1, 40, 41, 10_040):
                    for vertex_index in range(5):
                        expected_address = int(
                            vertex_lut_address(
                                lower[case_index],
                                int(masks[case_index, vertex_index]),
                                sampling_bits,
                            )
                        )
                        self.assertEqual(
                            int(addresses[case_index, vertex_index]),
                            expected_address,
                        )

                for mask_row in masks:
                    for previous, following in zip(
                        mask_row[:-1],
                        mask_row[1:],
                    ):
                        previous = int(previous)
                        following = int(following)
                        self.assertEqual(
                            previous & following,
                            previous,
                        )
                        self.assertEqual(
                            (previous ^ following).bit_count(),
                            1,
                        )

                self.assertEqual(set(files), set(widths))

                for filename, lines in files.items():
                    self.assertEqual(len(lines), 10_041)
                    self.assertTrue(
                        all(
                            len(line) == widths[filename]
                            for line in lines
                        )
                    )
                    self.assertTrue(
                        all(line == line.upper() for line in lines)
                    )

                for line, expected_row in zip(
                    files["masks.mem"],
                    masks,
                ):
                    self.assertEqual(
                        unpack_hex_fields(line, 4, 5),
                        tuple(int(value) for value in expected_row),
                    )

                for line, expected_row in zip(
                    files["weights.mem"],
                    weights,
                ):
                    self.assertEqual(
                        unpack_hex_fields(
                            line,
                            WEIGHT_BITS,
                            5,
                            signed=True,
                        ),
                        tuple(int(value) for value in expected_row),
                    )

                for line, expected_row in zip(
                    files["addresses.mem"],
                    addresses,
                ):
                    self.assertEqual(
                        unpack_hex_fields(line, 32, 5),
                        tuple(int(value) for value in expected_row),
                    )

    def test_core_numerators_match_golden(self):
        for name, expected in EXPECTED_FILES.items():
            with self.subTest(configuration=name):
                scale = expected["scale"]
                sampling_bits = expected["sampling_bits"]

                (
                    labels,
                    _,
                    lower,
                    remainders,
                    _,
                    weights,
                    addresses,
                ) = build_core_vectors(sampling_bits)

                actual = build_core_numerators(
                    scale,
                    sampling_bits,
                    addresses,
                    weights,
                )

                spec = get_lut_spec(
                    scale,
                    sampling_bits,
                )
                lut = load_model_s_lut(
                    LUT_ROOT / spec["filename"],
                    scale,
                    sampling_bits,
                )
                golden = interpolate_lut_numerators(
                    lut,
                    lower,
                    remainders,
                    scale,
                    sampling_bits,
                ).reshape(len(labels), -1)

                self.assertEqual(
                    actual.shape,
                    (10_041, scale * scale),
                )
                self.assertEqual(
                    actual.dtype,
                    np.dtype(np.int32),
                )
                self.assertEqual(
                    int(actual.min()),
                    expected["numerator_minimum"],
                )
                self.assertEqual(
                    int(actual.max()),
                    expected["numerator_maximum"],
                )
                np.testing.assert_array_equal(
                    actual,
                    golden,
                )

                _, files = build_core_lines(
                    scale,
                    sampling_bits,
                )

                for line, expected_row in zip(
                    files["numerators.mem"],
                    actual,
                ):
                    self.assertEqual(
                        unpack_hex_fields(
                            line,
                            NUMERATOR_BITS,
                            scale * scale,
                            signed=True,
                        ),
                        tuple(int(value) for value in expected_row),
                    )

    def test_generated_core_files(self):
        for name, expected in EXPECTED_FILES.items():
            with self.subTest(configuration=name):
                labels, files = build_core_lines(
                    expected["scale"],
                    expected["sampling_bits"]
                )
                expected_contents = {
                    "case_labels.txt": list(labels),
                    **files,
                }

                for filename, expected_lines in (
                    expected_contents.items()
                ):
                    path = VECTOR_ROOT / name / filename
                    self.assertTrue(path.is_file())

                    raw = path.read_bytes()
                    self.assertNotIn(b"\r", raw)
                    self.assertEqual(
                        raw.decode("ascii").splitlines(),
                        expected_lines,
                    )
                    self.assertEqual(len(expected_lines), 10_041)

                    actual_hash = hashlib.sha256(
                        raw
                    ).hexdigest().upper()
                    self.assertEqual(
                        actual_hash,
                        expected["core_sha256"][filename],
                    )

    def test_generated_core_manifests(self):
        expected_word_bits = {
            "quantizer.mem": 32,
            "inputs.mem": 32,
            "lower.mem": 32,
            "remainders.mem": 32,
            "masks.mem": 20,
            "weights.mem": 5 * WEIGHT_BITS,
            "addresses.mem": 160,
        }

        for name, expected in EXPECTED_FILES.items():
            with self.subTest(configuration=name):
                path = VECTOR_ROOT / name / "manifest.json"
                self.assertTrue(path.is_file())
                raw = path.read_bytes()
                self.assertNotIn(b"\r", raw)
                self.assertEqual(
                    hashlib.sha256(raw).hexdigest().upper(),
                    expected["manifest_sha256"],
                )
                manifest = json.loads(raw.decode("ascii"))

                self.assertEqual(manifest["configuration"], name)
                self.assertEqual(manifest["scale"], expected["scale"])
                self.assertEqual(
                    manifest["sampling_bits"],
                    expected["sampling_bits"],
                )
                self.assertEqual(manifest["case_count"], 10_041)
                self.assertEqual(manifest["random_seed"], 20_250_806)
                self.assertEqual(
                    manifest["lut_depth"],
                    expected["lut_depth"],
                )
                self.assertEqual(
                    manifest["address_bits"],
                    expected["address_bits"],
                )

                expected_files = {
                    "quantizer.mem",
                    "case_labels.txt",
                    *expected["core_sha256"].keys(),
                }
                self.assertEqual(
                    set(manifest["files"]),
                    expected_files,
                )

                expected_word_bits["numerators.mem"] = (
                    expected["scale"] ** 2 * NUMERATOR_BITS
                )

                for filename, record in manifest["files"].items():
                    file_path = VECTOR_ROOT / name / filename
                    self.assertEqual(
                        record["size_bytes"],
                        file_path.stat().st_size,
                    )
                    self.assertEqual(
                        record["sha256"],
                        hashlib.sha256(
                            file_path.read_bytes()
                        ).hexdigest().upper(),
                    )

                    if filename in expected_word_bits:
                        self.assertEqual(
                            record["word_bits"],
                            expected_word_bits[filename],
                        )

    def test_all_256_quantizer_lines(self):
        for name, expected in EXPECTED_FILES.items():
            with self.subTest(configuration=name):
                sampling_bits = expected["sampling_bits"]
                q = 1 << sampling_bits

                lines = build_quantizer_lines(
                    sampling_bits
                )

                self.assertEqual(len(lines), 256)

                for value, line in enumerate(lines):
                    self.assertEqual(len(line), 8)
                    self.assertEqual(line, line.upper())

                    self.assertTrue(
                        all(
                            character in "0123456789ABCDEF"
                            for character in line
                        )
                    )

                    actual = unpack_four_bytes(line)

                    expected_fields = (
                        value,
                        value >> sampling_bits,
                        (value >> sampling_bits) + 1,
                        value & (q - 1),
                    )

                    self.assertEqual(
                        actual,
                        expected_fields,
                    )

                for index, expected_line in (
                    expected["known_lines"].items()
                ):
                    self.assertEqual(
                        lines[index],
                        expected_line,
                    )

    def test_generated_quantizer_files(self):
        for name, expected in EXPECTED_FILES.items():
            with self.subTest(configuration=name):
                path = (
                    VECTOR_ROOT
                    / name
                    / "quantizer.mem"
                )

                self.assertTrue(path.is_file())
                self.assertEqual(path.stat().st_size, 2304)

                raw = path.read_bytes()

                self.assertNotIn(b"\r", raw)

                lines = raw.decode(
                    "ascii"
                ).splitlines()

                self.assertEqual(len(lines), 256)

                self.assertEqual(
                    lines,
                    build_quantizer_lines(
                        expected["sampling_bits"]
                    ),
                )

                actual_hash = hashlib.sha256(
                    raw
                ).hexdigest().upper()

                self.assertEqual(
                    actual_hash,
                    expected["sha256"],
                )


if __name__ == "__main__":
    unittest.main()
