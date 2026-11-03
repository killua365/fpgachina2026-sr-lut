import hashlib
import itertools
import json
import sys
from pathlib import Path

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[3]
REFERENCE_ROOT = PROJECT_ROOT / "src/reference"
OUTPUT_ROOT = PROJECT_ROOT / "data/golden" / "rtl_core"
LUT_ROOT = PROJECT_ROOT / "data/lut" / "original_npy"

reference_root_text = str(REFERENCE_ROOT)

if reference_root_text not in sys.path:
    sys.path.insert(0, reference_root_text)


from model_s_address import all_vertex_lut_addresses
from model_s_interpolator import get_lut_spec
from model_s_interpolator import load_model_s_lut
from model_s_quantizer import quantization_parameters
from model_s_quantizer import quantize_u8
from model_s_simplex import select_simplex_paths


CONFIGURATIONS = (
    {
        "name": "x2_4bit",
        "scale": 2,
        "sampling_bits": 4,
    },
    {
        "name": "x4_5bit",
        "scale": 4,
        "sampling_bits": 5,
    },
)


RANDOM_CASE_COUNT = 10_000
RANDOM_SEED = 20_250_806
WEIGHT_BITS = 7
NUMERATOR_BITS = 15


def pack_unsigned_hex_line(values, field_bits):
    if (
        isinstance(field_bits, bool)
        or not isinstance(field_bits, int)
        or field_bits < 1
    ):
        raise ValueError("field_bits must be a positive integer")

    values = tuple(values)

    if not values:
        raise ValueError("values must not be empty")

    maximum = (1 << field_bits) - 1
    checked = []

    for value in values:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, np.integer))
        ):
            raise TypeError("packed values must be integers")

        integer_value = int(value)

        if not 0 <= integer_value <= maximum:
            raise ValueError("packed value is out of range")

        checked.append(integer_value)

    word = 0

    for index, value in enumerate(checked):
        word |= value << (index * field_bits)

    hex_width = (len(checked) * field_bits + 3) // 4
    return f"{word:0{hex_width}X}"


def pack_signed_hex_line(values, field_bits):
    if (
        isinstance(field_bits, bool)
        or not isinstance(field_bits, int)
        or field_bits < 1
    ):
        raise ValueError("field_bits must be a positive integer")

    values = tuple(values)

    if not values:
        raise ValueError("values must not be empty")

    minimum = -(1 << (field_bits - 1))
    maximum = (1 << (field_bits - 1)) - 1
    field_mask = (1 << field_bits) - 1
    encoded_values = []

    for value in values:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, np.integer))
        ):
            raise TypeError("packed values must be integers")

        integer_value = int(value)

        if not minimum <= integer_value <= maximum:
            raise ValueError("packed value is out of range")

        encoded_values.append(integer_value & field_mask)

    return pack_unsigned_hex_line(
        encoded_values,
        field_bits,
    )


def write_ascii_lines(path, lines):
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open(
        "w",
        encoding="ascii",
        newline="\n",
    ) as destination:
        for line in lines:
            destination.write(line)
            destination.write("\n")


def write_json_file(path, data):
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open(
        "w",
        encoding="ascii",
        newline="\n",
    ) as destination:
        json.dump(
            data,
            destination,
            ensure_ascii=True,
            indent=2,
        )
        destination.write("\n")


def build_quantizer_lines(sampling_bits):
    inputs = np.arange(256, dtype=np.uint8)

    lower, upper, remainder = quantize_u8(
        inputs,
        sampling_bits,
    )

    return [
        pack_unsigned_hex_line(
            (
                inputs[index],
                lower[index],
                upper[index],
                remainder[index],
            ),
            8,
        )
        for index in range(256)
    ]


def build_directed_cases(sampling_bits):
    q, _ = quantization_parameters(sampling_bits)

    cases = [
        ("boundary_all_zero", (0, 0, 0, 0)),
        ("boundary_all_255", (255, 255, 255, 255)),
        ("boundary_low_edges", (0, 1, q - 1, q)),
        ("boundary_low_edges_reverse", (q, q - 1, 1, 0)),
        (
            "boundary_second_edges",
            (q - 1, q, 2 * q - 1, 2 * q),
        ),
        (
            "boundary_second_edges_reverse",
            (2 * q, 2 * q - 1, q, q - 1),
        ),
        (
            "boundary_high_edges",
            (255 - q, 256 - q, 254, 255),
        ),
        (
            "boundary_high_edges_reverse",
            (255, 254, 256 - q, 255 - q),
        ),
    ]

    bases = tuple(
        (index + 1) * q
        for index in range(4)
    )
    descending_values = (
        q - 3,
        q // 2 + 1,
        q // 4 + 1,
        1,
    )

    for order in itertools.permutations(range(4)):
        remainders = [0, 0, 0, 0]

        for rank, dimension in enumerate(order):
            remainders[dimension] = descending_values[rank]

        label = "strict_" + "".join(
            "abcd"[dimension]
            for dimension in order
        )
        inputs = tuple(
            bases[index] + remainders[index]
            for index in range(4)
        )
        cases.append((label, inputs))

    high = q - 3
    middle = q // 2
    low = 1

    tie_remainders = (
        ("tie_all_zero", (0, 0, 0, 0)),
        (
            "tie_all_middle",
            (middle, middle, middle, middle),
        ),
        ("tie_ab_high_cd_low", (high, high, low, low)),
        ("tie_ac_high_bd_low", (high, low, high, low)),
        ("tie_ad_high_bc_low", (high, low, low, high)),
        ("tie_ab_low_cd_high", (low, low, high, high)),
        ("tie_ac_low_bd_high", (low, high, low, high)),
        ("tie_ad_low_bc_high", (low, high, high, low)),
        ("tie_abc_high_d_low", (high, high, high, low)),
    )

    for label, remainders in tie_remainders:
        inputs = tuple(
            bases[index] + remainders[index]
            for index in range(4)
        )
        cases.append((label, inputs))

    labels = tuple(label for label, _ in cases)
    inputs = np.asarray(
        [values for _, values in cases],
        dtype=np.uint8,
    )

    return labels, inputs


def build_random_cases():
    random_generator = np.random.default_rng(
        RANDOM_SEED
    )
    inputs = random_generator.integers(
        0,
        256,
        size=(RANDOM_CASE_COUNT, 4),
        dtype=np.uint8,
    )
    labels = tuple(
        f"random_{index:05d}"
        for index in range(RANDOM_CASE_COUNT)
    )

    return labels, inputs


def build_core_cases(sampling_bits):
    directed_labels, directed_inputs = (
        build_directed_cases(sampling_bits)
    )
    random_labels, random_inputs = build_random_cases()

    labels = directed_labels + random_labels
    inputs = np.concatenate(
        (directed_inputs, random_inputs),
        axis=0,
    )

    return labels, np.ascontiguousarray(inputs)


def build_core_vectors(sampling_bits):
    labels, inputs = build_core_cases(sampling_bits)
    lower, _, remainders = quantize_u8(
        inputs,
        sampling_bits,
    )
    masks, weights = select_simplex_paths(
        remainders,
        sampling_bits,
    )
    all_addresses = all_vertex_lut_addresses(
        lower,
        sampling_bits,
    )
    addresses = np.take_along_axis(
        all_addresses,
        masks,
        axis=-1,
    )

    return (
        labels,
        inputs,
        lower,
        remainders,
        masks,
        weights,
        addresses,
    )


def build_core_numerators(
    scale,
    sampling_bits,
    addresses,
    weights,
):
    spec = get_lut_spec(scale, sampling_bits)
    lut = load_model_s_lut(
        LUT_ROOT / spec["filename"],
        scale,
        sampling_bits,
    )

    vertex_values = lut[addresses].astype(np.int32)
    signed_weights = weights[
        :, :, np.newaxis
    ].astype(np.int32)

    numerators = np.sum(
        vertex_values * signed_weights,
        axis=1,
        dtype=np.int32,
    )

    return np.ascontiguousarray(
        numerators,
        dtype=np.int32,
    )


def build_core_lines(scale, sampling_bits):
    (
        labels,
        inputs,
        lower,
        remainders,
        masks,
        weights,
        addresses,
    ) = build_core_vectors(sampling_bits)
    numerators = build_core_numerators(
        scale,
        sampling_bits,
        addresses,
        weights,
    )

    files = {
        "inputs.mem": [
            pack_unsigned_hex_line(row, 8)
            for row in inputs
        ],
        "lower.mem": [
            pack_unsigned_hex_line(row, 8)
            for row in lower
        ],
        "remainders.mem": [
            pack_unsigned_hex_line(row, 8)
            for row in remainders
        ],
        "masks.mem": [
            pack_unsigned_hex_line(row, 4)
            for row in masks
        ],
        "weights.mem": [
            pack_signed_hex_line(row, WEIGHT_BITS)
            for row in weights
        ],
        "addresses.mem": [
            pack_unsigned_hex_line(row, 32)
            for row in addresses
        ],
        "numerators.mem": [
            pack_signed_hex_line(row, NUMERATOR_BITS)
            for row in numerators
        ],
    }

    return labels, files


def file_sha256(path):
    return hashlib.sha256(
        Path(path).read_bytes()
    ).hexdigest().upper()


def build_core_manifest(
    configuration,
    output_directory,
    labels,
    files,
):
    name = configuration["name"]
    scale = configuration["scale"]
    sampling_bits = configuration["sampling_bits"]
    q, levels = quantization_parameters(sampling_bits)
    lut_spec = get_lut_spec(scale, sampling_bits)
    lut_depth = levels ** 4
    address_bits = (lut_depth - 1).bit_length()

    word_bits = {
        "quantizer.mem": 32,
        "inputs.mem": 32,
        "lower.mem": 32,
        "remainders.mem": 32,
        "masks.mem": 20,
        "weights.mem": 5 * WEIGHT_BITS,
        "addresses.mem": 160,
        "numerators.mem": scale * scale * NUMERATOR_BITS,
    }
    line_counts = {
        "quantizer.mem": 256,
        "case_labels.txt": len(labels),
        **{
            filename: len(lines)
            for filename, lines in files.items()
        },
    }
    file_records = {}

    for filename, lines in line_counts.items():
        path = output_directory / filename
        record = {
            "lines": lines,
            "size_bytes": path.stat().st_size,
            "sha256": file_sha256(path),
        }

        if filename in word_bits:
            record["word_bits"] = word_bits[filename]

        file_records[filename] = record

    return {
        "format_version": 1,
        "configuration": name,
        "scale": scale,
        "sampling_bits": sampling_bits,
        "q": q,
        "levels": levels,
        "lut_depth": lut_depth,
        "address_bits": address_bits,
        "outputs_per_entry": scale * scale,
        "case_count": len(labels),
        "coverage": {
            "boundary": 8,
            "strict_permutations": 24,
            "ties": 9,
            "fixed_random": RANDOM_CASE_COUNT,
        },
        "random_seed": RANDOM_SEED,
        "lut_source": {
            "filename": lut_spec["filename"],
            "sha256": lut_spec["sha256"],
        },
        "packing": {
            "first_value": "least_significant_bits",
            "signed": "twos_complement",
            "text_encoding": "ASCII",
            "line_ending": "LF",
        },
        "files": file_records,
    }


def generate_quantizer_files():
    for configuration in CONFIGURATIONS:
        name = configuration["name"]
        sampling_bits = configuration["sampling_bits"]

        lines = build_quantizer_lines(sampling_bits)

        output_path = (
            OUTPUT_ROOT
            / name
            / "quantizer.mem"
        )

        write_ascii_lines(output_path, lines)

        print(f"configuration={name}")
        print(f"  lines={len(lines)}")
        print(f"  first_line={lines[0]}")
        print(f"  last_line={lines[-1]}")
        print(f"  sha256={file_sha256(output_path)}")


def generate_core_files():
    for configuration in CONFIGURATIONS:
        name = configuration["name"]
        scale = configuration["scale"]
        sampling_bits = configuration["sampling_bits"]
        labels, files = build_core_lines(
            scale,
            sampling_bits,
        )
        output_directory = OUTPUT_ROOT / name

        label_path = output_directory / "case_labels.txt"
        write_ascii_lines(label_path, labels)

        print(f"configuration={name}")
        print(f"  core_cases={len(labels)}")
        print(
            "  case_labels_sha256="
            f"{file_sha256(label_path)}"
        )

        for filename, lines in files.items():
            output_path = output_directory / filename
            write_ascii_lines(output_path, lines)

            print(f"  file={filename}")
            print(f"    lines={len(lines)}")
            print(f"    first_line={lines[0]}")
            print(f"    last_line={lines[-1]}")
            print(f"    sha256={file_sha256(output_path)}")

        manifest = build_core_manifest(
            configuration,
            output_directory,
            labels,
            files,
        )
        manifest_path = output_directory / "manifest.json"
        write_json_file(manifest_path, manifest)
        print(f"  manifest={manifest_path.name}")
        print(f"    sha256={file_sha256(manifest_path)}")


def main():
    generate_quantizer_files()
    generate_core_files()
    print("RTL CORE VECTOR GENERATION PASS")


if __name__ == "__main__":
    main()
