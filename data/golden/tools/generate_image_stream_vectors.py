import hashlib
import sys
from pathlib import Path

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[3]
REFERENCE_ROOT = PROJECT_ROOT / "src/reference"
TOOLS_ROOT = PROJECT_ROOT / "data/golden" / "tools"
LUT_ROOT = PROJECT_ROOT / "data/lut" / "original_npy"
SOURCE_ROOT = PROJECT_ROOT / "data/golden" / "source_images"
OUTPUT_ROOT = PROJECT_ROOT / "data/golden" / "image_stream"

for module_root in (REFERENCE_ROOT, TOOLS_ROOT):
    module_root_text = str(module_root)

    if module_root_text not in sys.path:
        sys.path.insert(0, module_root_text)


from generate_rtl_core_vectors import file_sha256
from generate_rtl_core_vectors import NUMERATOR_BITS
from generate_rtl_core_vectors import pack_signed_hex_line
from generate_rtl_core_vectors import pack_unsigned_hex_line
from generate_rtl_core_vectors import write_ascii_lines
from generate_rtl_core_vectors import write_json_file
from model_s_fusion import infer_image_blocks
from model_s_input import load_rgb_u8
from model_s_interpolator import get_lut_spec
from model_s_interpolator import load_model_s_lut
from model_s_neighborhoods import extract_direction_neighborhoods
from model_s_output import run_model_s
from model_s_output import stitch_output_blocks


CONFIGURATIONS = (
    {
        "name": "x2_4bit",
        "scale": 2,
        "sampling_bits": 4,
        "full_input": SOURCE_ROOT / "lr_x2" / "woman_gt.png",
    },
    {
        "name": "x4_5bit",
        "scale": 4,
        "sampling_bits": 5,
        "full_input": SOURCE_ROOT / "lr_x4" / "woman.png",
    },
)

FLOW_PATTERN_LENGTH = 256


def raw_array_sha256(array):
    contiguous = np.ascontiguousarray(array)
    return hashlib.sha256(contiguous.tobytes()).hexdigest().upper()


def build_small_rgb_image():
    return np.array(
        [
            [
                [0, 255, 16],
                [15, 32, 127],
                [16, 31, 128],
                [255, 0, 254],
            ],
            [
                [1, 17, 33],
                [63, 64, 65],
                [127, 128, 129],
                [254, 253, 252],
            ],
            [
                [32, 48, 80],
                [96, 112, 144],
                [160, 176, 208],
                [224, 240, 255],
            ],
        ],
        dtype=np.uint8,
    )


def pack_axis_rgb_line(rgb, last):
    values = tuple(int(value) for value in rgb)

    if len(values) != 3:
        raise ValueError("RGB pixels must contain three values")

    if any(value < 0 or value > 255 for value in values):
        raise ValueError("RGB values must be between 0 and 255")

    if isinstance(last, bool):
        last_value = int(last)
    elif isinstance(last, (int, np.integer)) and int(last) in (0, 1):
        last_value = int(last)
    else:
        raise ValueError("last must be zero or one")

    word = (
        values[0]
        | (values[1] << 8)
        | (values[2] << 16)
        | (last_value << 24)
    )
    return f"{word:07X}"


def build_axis_lines(image):
    flat = np.ascontiguousarray(image).reshape(-1, 3)

    return [
        pack_axis_rgb_line(
            pixel,
            index == flat.shape[0] - 1,
        )
        for index, pixel in enumerate(flat)
    ]


def build_flow_patterns():
    source_valid = []
    sink_ready = []

    for cycle in range(FLOW_PATTERN_LENGTH):
        source_valid.append(
            "0" if cycle % 37 in (9, 10, 11) else "1"
        )
        sink_ready.append(
            "0"
            if cycle % 64 in range(16, 24) or cycle % 13 == 5
            else "1"
        )

    return {
        "source_valid.mem": source_valid,
        "sink_ready.mem": sink_ready,
    }


def load_configuration_lut(scale, sampling_bits):
    spec = get_lut_spec(scale, sampling_bits)
    return load_model_s_lut(
        LUT_ROOT / spec["filename"],
        scale,
        sampling_bits,
    )


def build_configuration_vectors(configuration):
    scale = configuration["scale"]
    sampling_bits = configuration["sampling_bits"]
    lut = load_configuration_lut(scale, sampling_bits)
    small_image = build_small_rgb_image()
    small_neighborhoods = extract_direction_neighborhoods(
        small_image
    )
    (
        small_blocks,
        small_native,
        small_restored,
        small_fused,
        small_rounded,
    ) = infer_image_blocks(
        small_image,
        lut,
        scale,
        sampling_bits,
        return_trace=True,
    )
    small_output = stitch_output_blocks(small_blocks)

    full_input = load_rgb_u8(configuration["full_input"])
    full_output = run_model_s(
        full_input,
        lut,
        scale,
        sampling_bits,
    )

    neighborhood_rows = (
        small_neighborhoods
        .transpose(1, 2, 0, 3, 4)
        .reshape(-1, 4)
    )
    native_rows = (
        small_native
        .transpose(1, 2, 0, 3, 4, 5)
        .reshape(-1, scale * scale)
    )
    restored_rows = (
        small_restored
        .transpose(1, 2, 0, 3, 4, 5)
        .reshape(-1, scale * scale)
    )
    fused_rows = small_fused.reshape(-1, scale * scale)
    rounded_rows = small_rounded.reshape(-1, scale * scale)

    files = {
        "small_input_axis.mem": build_axis_lines(small_image),
        "small_neighborhoods.mem": [
            pack_unsigned_hex_line(row, 8)
            for row in neighborhood_rows
        ],
        "small_native_numerators.mem": [
            pack_signed_hex_line(row, NUMERATOR_BITS)
            for row in native_rows
        ],
        "small_restored_numerators.mem": [
            pack_signed_hex_line(row, NUMERATOR_BITS)
            for row in restored_rows
        ],
        "small_fused_numerators.mem": [
            pack_signed_hex_line(row, NUMERATOR_BITS + 2)
            for row in fused_rows
        ],
        "small_rounded.mem": [
            pack_signed_hex_line(row, NUMERATOR_BITS)
            for row in rounded_rows
        ],
        "small_output_axis.mem": build_axis_lines(small_output),
        "full_input_axis.mem": build_axis_lines(full_input),
        "full_output_axis.mem": build_axis_lines(full_output),
    }

    arrays = {
        "small_image": small_image,
        "small_neighborhoods": small_neighborhoods,
        "small_native": small_native,
        "small_restored": small_restored,
        "small_fused": small_fused,
        "small_rounded": small_rounded,
        "small_output": small_output,
        "full_input": full_input,
        "full_output": full_output,
    }

    return arrays, files


def build_image_stream_manifest(
    configuration,
    output_directory,
    arrays,
    files,
):
    scale = configuration["scale"]
    sampling_bits = configuration["sampling_bits"]
    word_bits = {
        "small_input_axis.mem": 25,
        "small_neighborhoods.mem": 32,
        "small_native_numerators.mem": scale * scale * NUMERATOR_BITS,
        "small_restored_numerators.mem": scale * scale * NUMERATOR_BITS,
        "small_fused_numerators.mem": scale * scale * (NUMERATOR_BITS + 2),
        "small_rounded.mem": scale * scale * NUMERATOR_BITS,
        "small_output_axis.mem": 25,
        "full_input_axis.mem": 25,
        "full_output_axis.mem": 25,
    }
    records = {}

    for filename, lines in files.items():
        path = output_directory / filename
        records[filename] = {
            "lines": len(lines),
            "word_bits": word_bits[filename],
            "size_bytes": path.stat().st_size,
            "sha256": file_sha256(path),
        }

    return {
        "format_version": 1,
        "configuration": configuration["name"],
        "scale": scale,
        "sampling_bits": sampling_bits,
        "axis_word": {
            "rgb": "R[7:0], G[15:8], B[23:16]",
            "last": "bit[24], asserted only on final frame pixel",
        },
        "small": {
            "input_shape": list(arrays["small_image"].shape),
            "output_shape": list(arrays["small_output"].shape),
            "input_raw_sha256": raw_array_sha256(
                arrays["small_image"]
            ),
            "output_raw_sha256": raw_array_sha256(
                arrays["small_output"]
            ),
            "neighborhood_line_order": (
                "pixel_row_major, direction_0_to_3, channel_R_G_B"
            ),
            "numerator_line_order": (
                "pixel_row_major, direction_0_to_3, channel_R_G_B"
            ),
            "fused_line_order": (
                "pixel_row_major, channel_R_G_B"
            ),
            "coverage": [
                "four_corners",
                "four_edges",
                "four_directions",
                "three_rgb_channels",
                "rotation_restore",
                "ties_to_even",
                "clip_0_255",
            ],
        },
        "full": {
            "source_file": str(
                configuration["full_input"].relative_to(PROJECT_ROOT)
            ).replace("\\", "/"),
            "source_file_sha256": file_sha256(
                configuration["full_input"]
            ),
            "input_shape": list(arrays["full_input"].shape),
            "output_shape": list(arrays["full_output"].shape),
            "input_raw_sha256": raw_array_sha256(
                arrays["full_input"]
            ),
            "output_raw_sha256": raw_array_sha256(
                arrays["full_output"]
            ),
        },
        "files": records,
    }


def generate_configuration(configuration):
    output_directory = OUTPUT_ROOT / configuration["name"]
    arrays, files = build_configuration_vectors(configuration)

    for filename, lines in files.items():
        write_ascii_lines(output_directory / filename, lines)

    manifest = build_image_stream_manifest(
        configuration,
        output_directory,
        arrays,
        files,
    )
    manifest_path = output_directory / "manifest.json"
    write_json_file(manifest_path, manifest)

    print(f"configuration={configuration['name']}")
    print(f"  small_input_shape={arrays['small_image'].shape}")
    print(f"  small_output_shape={arrays['small_output'].shape}")
    print(f"  full_input_shape={arrays['full_input'].shape}")
    print(f"  full_output_shape={arrays['full_output'].shape}")

    for filename, lines in files.items():
        print(f"  file={filename}")
        print(f"    lines={len(lines)}")
        print(f"    sha256={file_sha256(output_directory / filename)}")

    print(f"  manifest_sha256={file_sha256(manifest_path)}")


def generate_flow_control():
    output_directory = OUTPUT_ROOT / "flow_control"
    files = build_flow_patterns()

    for filename, lines in files.items():
        write_ascii_lines(output_directory / filename, lines)

    manifest = {
        "format_version": 1,
        "pattern_length": FLOW_PATTERN_LENGTH,
        "replay": "Repeat cyclically until the frame handshake completes",
        "source_valid": (
            "Drive input valid; keep input data stable while ready is zero"
        ),
        "sink_ready": (
            "Drive output ready; includes an eight-cycle continuous stall"
        ),
        "files": {
            filename: {
                "lines": len(lines),
                "word_bits": 1,
                "size_bytes": (output_directory / filename).stat().st_size,
                "sha256": file_sha256(output_directory / filename),
            }
            for filename, lines in files.items()
        },
    }
    manifest_path = output_directory / "manifest.json"
    write_json_file(manifest_path, manifest)

    print("flow_control")
    print(f"  pattern_length={FLOW_PATTERN_LENGTH}")

    for filename in files:
        print(
            f"  {filename}_sha256="
            f"{file_sha256(output_directory / filename)}"
        )

    print(f"  manifest_sha256={file_sha256(manifest_path)}")


def main():
    for configuration in CONFIGURATIONS:
        generate_configuration(configuration)

    generate_flow_control()
    print("IMAGE STREAM VECTOR GENERATION PASS")


if __name__ == "__main__":
    main()
