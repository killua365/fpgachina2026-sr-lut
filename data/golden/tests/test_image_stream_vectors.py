import hashlib
import json
import sys
import unittest
from pathlib import Path

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[3]
REFERENCE_ROOT = PROJECT_ROOT / "src/reference"
TOOLS_ROOT = PROJECT_ROOT / "data/golden" / "tools"
VECTOR_ROOT = PROJECT_ROOT / "data/golden" / "image_stream"

for module_root in (REFERENCE_ROOT, TOOLS_ROOT):
    module_root_text = str(module_root)

    if module_root_text not in sys.path:
        sys.path.insert(0, module_root_text)


from generate_image_stream_vectors import CONFIGURATIONS
from generate_image_stream_vectors import FLOW_PATTERN_LENGTH
from generate_image_stream_vectors import build_axis_lines
from generate_image_stream_vectors import build_configuration_vectors
from generate_image_stream_vectors import build_flow_patterns
from generate_image_stream_vectors import build_small_rgb_image
from generate_image_stream_vectors import pack_axis_rgb_line
from generate_rtl_core_vectors import NUMERATOR_BITS
from model_s_input import load_rgb_u8
from model_s_interpolator import get_lut_spec
from model_s_interpolator import load_model_s_lut
from model_s_output import run_model_s


EXPECTED = {
    "x2_4bit": {
        "scale": 2,
        "sampling_bits": 4,
        "small_output_shape": (6, 8, 3),
        "full_input_shape": (172, 114, 3),
        "full_output_shape": (344, 228, 3),
        "manifest_sha256": "53699F229FF97D20F26D68B111AF81F1709078F629BFB3404DD67816037E7B4D",
        "files": {
            "small_input_axis.mem": (
                "0E25D02141D40BD48852BB6804CD8CEE"
                "7FCF7DAE5F1935808E244BE0DF55872D"
            ),
            "small_neighborhoods.mem": (
                "7458B9E5CD2FB253FAD7F1513EFAE27"
                "5FEA5E8D8372AB678C7B3492535E835AE"
            ),
            "small_native_numerators.mem": (
                "DF17BB4FF2D526FBCD8A8F773C4D472"
                "3D40D0AFA18EB22717096F70848180E29"
            ),
            "small_restored_numerators.mem": (
                "3ABF69CDB0944CDB220467FE8C459AF1"
                "B88818491A6BDDE684B5DA69DD4E66CD"
            ),
            "small_fused_numerators.mem": (
                "645AB9AB0820E25017ECD5AA3885D52D"
                "073985FA5D43DF5E7A7395908C7AE5A7"
            ),
            "small_rounded.mem": (
                "9E61D7B9AF0876287BD34BBA757A4186"
                "F38BFACDF39E03DD3A265410C1CE06FE"
            ),
            "small_output_axis.mem": (
                "73569AF0DDE9988A0080E5C8734A812"
                "2C0D497ED47C40CB6F7BE0EF220928638"
            ),
            "full_input_axis.mem": (
                "795D9330529898919C891670E1596129"
                "DE320AE549A5BDA28D1D897F5D9DB4E8"
            ),
            "full_output_axis.mem": (
                "D7BF37CB54E82293685A1FD0DBEB5D4"
                "D93AE1E7D2741EFC393D73EC3E56666AD"
            ),
        },
    },
    "x4_5bit": {
        "scale": 4,
        "sampling_bits": 5,
        "small_output_shape": (12, 16, 3),
        "full_input_shape": (86, 57, 3),
        "full_output_shape": (344, 228, 3),
        "manifest_sha256": "3BD4543DBE3089966D53EF53C3FBA132058D54AF691EAD76B441A30DCF8A44F7",
        "files": {
            "small_input_axis.mem": (
                "0E25D02141D40BD48852BB6804CD8CEE"
                "7FCF7DAE5F1935808E244BE0DF55872D"
            ),
            "small_neighborhoods.mem": (
                "7458B9E5CD2FB253FAD7F1513EFAE27"
                "5FEA5E8D8372AB678C7B3492535E835AE"
            ),
            "small_native_numerators.mem": (
                "BADED1EC538FAA9184D1DC3AF9F80BF4"
                "530513565CB8C66175DDF465EB081165"
            ),
            "small_restored_numerators.mem": (
                "1E462BC945EEC55D67AD847A213D5263"
                "47C37EAEB6DECB93C59DE01A2F867598"
            ),
            "small_fused_numerators.mem": (
                "423DF74AAB8547BD833C0192B2DDE7EE"
                "F52C1D472DDD188EBC870C39CB8313AC"
            ),
            "small_rounded.mem": (
                "482346D63B9FEE2DCF126DA562EC7A6F"
                "070E6BD8942D1266D83904815708644F"
            ),
            "small_output_axis.mem": (
                "71923044996ABDAA100ACCCFF315F438"
                "BBF06BC8E80C9848A8D85B860A627890"
            ),
            "full_input_axis.mem": (
                "F1860F7986958A52D7AF29C5B5FB8EA"
                "5B8014B4D4FEC809346157906E9D49B22"
            ),
            "full_output_axis.mem": (
                "B352423A6B8DDC3782BD0DF579F0D890"
                "38B18E9F7A3AE76F547A687BB84018CC"
            ),
        },
    },
}

FLOW_EXPECTED = {
    "source_valid.mem": (
        "6B5F0E77E74375C04DF1AA6B343E345"
        "E60E741B5CEF33377796C7FCBAB16E11E"
    ),
    "sink_ready.mem": (
        "3A1D243E80CD37D0A8A47E636631C32"
        "EB9F3B555FD52F62C44E1A2C38CB3B75E"
    ),
    "manifest.json": (
        "14FF794D5E98CBE9378F6224E1BFDE74"
        "D55F0C2B7DC27D394C4CB92D154FDEE4"
    ),
}


def read_ascii_lines(path):
    raw = Path(path).read_bytes()
    if b"\r" in raw:
        raise AssertionError(f"file does not use LF-only lines: {path}")
    return raw, raw.decode("ascii").splitlines()


def unpack_hex_fields(line, field_bits, count, signed=False):
    word = int(line, 16)
    mask = (1 << field_bits) - 1
    sign_bit = 1 << (field_bits - 1)
    values = []

    for index in range(count):
        value = (word >> (index * field_bits)) & mask
        if signed and value & sign_bit:
            value -= 1 << field_bits
        values.append(value)

    if word >> (field_bits * count):
        raise AssertionError("hex line contains bits above its declared width")

    return tuple(values)


def unpack_axis_lines(lines):
    pixels = []
    last_values = []

    for line in lines:
        if len(line) != 7 or line != line.upper():
            raise AssertionError("AXI words must be seven uppercase hex digits")
        word = int(line, 16)
        if word >= (1 << 25):
            raise AssertionError("AXI word contains bits above bit 24")
        pixels.append(
            (
                word & 0xFF,
                (word >> 8) & 0xFF,
                (word >> 16) & 0xFF,
            )
        )
        last_values.append((word >> 24) & 1)

    return np.array(pixels, dtype=np.uint8), last_values


def reflected_index(index, size):
    if index < 0:
        return 1
    if index >= size:
        return size - 2
    return index


def round_ties_to_even_scalar(value, divisor):
    sign = -1 if value < 0 else 1
    magnitude = abs(int(value))
    quotient, remainder = divmod(magnitude, divisor)

    if remainder * 2 > divisor:
        quotient += 1
    elif remainder * 2 == divisor and quotient % 2 == 1:
        quotient += 1

    return sign * quotient


class ImageStreamVectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.built = {
            configuration["name"]: build_configuration_vectors(
                configuration
            )
            for configuration in CONFIGURATIONS
        }

    def test_small_image_is_fixed_and_covers_quantizer_boundaries(self):
        image = build_small_rgb_image()
        self.assertEqual(image.shape, (3, 4, 3))
        self.assertEqual(image.dtype, np.dtype(np.uint8))
        self.assertTrue(image.flags.c_contiguous)
        self.assertEqual(image[0, 0].tolist(), [0, 255, 16])
        self.assertEqual(image[-1, -1].tolist(), [224, 240, 255])

        values = set(int(value) for value in image.reshape(-1))
        for sampling_bits in (4, 5):
            q = 1 << sampling_bits
            self.assertTrue({0, q - 1, q, 2 * q - 1, 255} <= values)

    def test_axis_packing_and_frame_last(self):
        self.assertEqual(pack_axis_rgb_line((0, 255, 16), 0), "010FF00")
        self.assertEqual(pack_axis_rgb_line((224, 240, 255), 1), "1FFF0E0")

        with self.assertRaises(ValueError):
            pack_axis_rgb_line((1, 2), 0)
        with self.assertRaises(ValueError):
            pack_axis_rgb_line((1, 2, 256), 0)
        with self.assertRaises(ValueError):
            pack_axis_rgb_line((1, 2, 3), 2)

        lines = build_axis_lines(build_small_rgb_image())
        pixels, last_values = unpack_axis_lines(lines)
        np.testing.assert_array_equal(
            pixels,
            build_small_rgb_image().reshape(-1, 3),
        )
        self.assertEqual(sum(last_values), 1)
        self.assertEqual(last_values[-1], 1)
        self.assertTrue(all(value == 0 for value in last_values[:-1]))

    def test_reflect_neighborhoods_at_all_four_corners(self):
        offsets = (
            ((0, 0), (0, 1), (1, 0), (1, 1)),
            ((0, 0), (1, 0), (0, -1), (1, -1)),
            ((0, 0), (0, -1), (-1, 0), (-1, -1)),
            ((0, 0), (-1, 0), (0, 1), (-1, 1)),
        )
        image = build_small_rgb_image()
        height, width, _ = image.shape
        corners = ((0, 0), (0, width - 1), (height - 1, 0),
                   (height - 1, width - 1))
        neighborhoods = self.built["x2_4bit"][0][
            "small_neighborhoods"
        ]

        for y, x in corners:
            for direction, direction_offsets in enumerate(offsets):
                for neighbor, (dy, dx) in enumerate(direction_offsets):
                    source_y = reflected_index(y + dy, height)
                    source_x = reflected_index(x + dx, width)
                    np.testing.assert_array_equal(
                        neighborhoods[direction, y, x, :, neighbor],
                        image[source_y, source_x],
                    )

    def test_small_pipeline_is_arithmetically_continuous(self):
        for configuration in CONFIGURATIONS:
            name = configuration["name"]
            scale = configuration["scale"]
            q = 1 << configuration["sampling_bits"]
            arrays, _ = self.built[name]

            independently_restored = np.stack(
                [
                    np.rot90(
                        arrays["small_native"][direction],
                        -direction,
                        axes=(-2, -1),
                    )
                    for direction in range(4)
                ],
                axis=0,
            )
            np.testing.assert_array_equal(
                arrays["small_restored"],
                independently_restored,
            )

            independently_fused = independently_restored.sum(
                axis=0,
                dtype=np.int64,
            )
            np.testing.assert_array_equal(
                arrays["small_fused"],
                independently_fused,
            )

            independently_rounded = np.array(
                [
                    round_ties_to_even_scalar(value, q)
                    for value in independently_fused.reshape(-1)
                ],
                dtype=np.int32,
            ).reshape(independently_fused.shape)
            np.testing.assert_array_equal(
                arrays["small_rounded"],
                independently_rounded,
            )
            self.assertLess(int(independently_rounded.min()), 0)
            self.assertGreater(int(independently_rounded.max()), 255)

            clipped_blocks = np.clip(
                independently_rounded,
                0,
                255,
            ).astype(np.uint8)
            height, width = clipped_blocks.shape[:2]
            independently_stitched = (
                clipped_blocks
                .transpose(0, 3, 1, 4, 2)
                .reshape(height * scale, width * scale, 3)
            )
            np.testing.assert_array_equal(
                arrays["small_output"],
                independently_stitched,
            )

    def test_small_intermediate_files_unpack_to_expected_arrays(self):
        for configuration in CONFIGURATIONS:
            name = configuration["name"]
            scale = configuration["scale"]
            arrays, _ = self.built[name]
            directory = VECTOR_ROOT / name
            expected_rows = {
                "small_neighborhoods.mem": (
                    arrays["small_neighborhoods"]
                    .transpose(1, 2, 0, 3, 4)
                    .reshape(-1, 4),
                    8,
                    4,
                    False,
                ),
                "small_native_numerators.mem": (
                    arrays["small_native"]
                    .transpose(1, 2, 0, 3, 4, 5)
                    .reshape(-1, scale * scale),
                    NUMERATOR_BITS,
                    scale * scale,
                    True,
                ),
                "small_restored_numerators.mem": (
                    arrays["small_restored"]
                    .transpose(1, 2, 0, 3, 4, 5)
                    .reshape(-1, scale * scale),
                    NUMERATOR_BITS,
                    scale * scale,
                    True,
                ),
                "small_fused_numerators.mem": (
                    arrays["small_fused"].reshape(-1, scale * scale),
                    NUMERATOR_BITS + 2,
                    scale * scale,
                    True,
                ),
                "small_rounded.mem": (
                    arrays["small_rounded"].reshape(-1, scale * scale),
                    NUMERATOR_BITS,
                    scale * scale,
                    True,
                ),
            }

            for filename, details in expected_rows.items():
                expected, field_bits, count, signed = details
                _, lines = read_ascii_lines(directory / filename)
                actual = np.array(
                    [
                        unpack_hex_fields(
                            line,
                            field_bits,
                            count,
                            signed=signed,
                        )
                        for line in lines
                    ],
                    dtype=expected.dtype,
                )
                np.testing.assert_array_equal(actual, expected)

    def test_all_pixel_stream_files_unpack_to_golden_images(self):
        stream_to_array = {
            "small_input_axis.mem": "small_image",
            "small_output_axis.mem": "small_output",
            "full_input_axis.mem": "full_input",
            "full_output_axis.mem": "full_output",
        }

        for configuration in CONFIGURATIONS:
            name = configuration["name"]
            arrays, _ = self.built[name]
            directory = VECTOR_ROOT / name

            for filename, array_name in stream_to_array.items():
                _, lines = read_ascii_lines(directory / filename)
                pixels, last_values = unpack_axis_lines(lines)
                expected = arrays[array_name].reshape(-1, 3)
                np.testing.assert_array_equal(pixels, expected)
                self.assertEqual(last_values.count(1), 1)
                self.assertEqual(last_values[-1], 1)

    def test_full_frames_match_reference_model(self):
        lut_root = PROJECT_ROOT / "data/lut" / "original_npy"

        for configuration in CONFIGURATIONS:
            name = configuration["name"]
            expected = EXPECTED[name]
            arrays, _ = self.built[name]
            image = load_rgb_u8(configuration["full_input"])
            spec = get_lut_spec(
                configuration["scale"],
                configuration["sampling_bits"],
            )
            lut = load_model_s_lut(
                lut_root / spec["filename"],
                configuration["scale"],
                configuration["sampling_bits"],
            )
            golden = run_model_s(
                image,
                lut,
                configuration["scale"],
                configuration["sampling_bits"],
            )

            self.assertEqual(image.shape, expected["full_input_shape"])
            self.assertEqual(golden.shape, expected["full_output_shape"])
            self.assertEqual(
                arrays["small_output"].shape,
                expected["small_output_shape"],
            )
            np.testing.assert_array_equal(arrays["full_input"], image)
            np.testing.assert_array_equal(arrays["full_output"], golden)

    def test_flow_control_patterns_and_files(self):
        files = build_flow_patterns()
        directory = VECTOR_ROOT / "flow_control"
        self.assertEqual(set(files), {"source_valid.mem", "sink_ready.mem"})

        for filename, expected_lines in files.items():
            raw, lines = read_ascii_lines(directory / filename)
            self.assertEqual(lines, expected_lines)
            self.assertEqual(len(lines), FLOW_PATTERN_LENGTH)
            self.assertTrue(set(lines) <= {"0", "1"})
            self.assertIn("0", lines)
            self.assertIn("1", lines)
            self.assertEqual(
                hashlib.sha256(raw).hexdigest().upper(),
                FLOW_EXPECTED[filename],
            )

        ready = "".join(files["sink_ready.mem"])
        self.assertIn("00000000", ready)

    def test_manifests_sizes_hashes_and_fixed_identity(self):
        for configuration in CONFIGURATIONS:
            name = configuration["name"]
            expected = EXPECTED[name]
            directory = VECTOR_ROOT / name
            manifest_path = directory / "manifest.json"
            raw, _ = read_ascii_lines(manifest_path)
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
            self.assertEqual(
                tuple(manifest["small"]["output_shape"]),
                expected["small_output_shape"],
            )
            self.assertEqual(
                tuple(manifest["full"]["input_shape"]),
                expected["full_input_shape"],
            )
            self.assertEqual(
                tuple(manifest["full"]["output_shape"]),
                expected["full_output_shape"],
            )
            self.assertEqual(set(manifest["files"]), set(expected["files"]))

            for filename, expected_hash in expected["files"].items():
                path = directory / filename
                file_raw, lines = read_ascii_lines(path)
                record = manifest["files"][filename]
                actual_hash = hashlib.sha256(file_raw).hexdigest().upper()
                self.assertEqual(actual_hash, expected_hash)
                self.assertEqual(record["sha256"], actual_hash)
                self.assertEqual(record["size_bytes"], len(file_raw))
                self.assertEqual(record["lines"], len(lines))

        flow_manifest_path = VECTOR_ROOT / "flow_control" / "manifest.json"
        flow_raw, _ = read_ascii_lines(flow_manifest_path)
        self.assertEqual(
            hashlib.sha256(flow_raw).hexdigest().upper(),
            FLOW_EXPECTED["manifest.json"],
        )


if __name__ == "__main__":
    unittest.main()
