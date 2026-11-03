import hashlib
import json
import string
import sys
from pathlib import Path

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[3]
REFERENCE_ROOT = PROJECT_ROOT / "src/reference"

reference_root_text = str(REFERENCE_ROOT)

if reference_root_text not in sys.path:
    sys.path.insert(0, reference_root_text)


from model_s_interpolator import LUT_SPECS
from model_s_interpolator import file_sha256
from model_s_interpolator import load_model_s_lut


MEM_CONFIGURATIONS = (
    {
        "name": "x2_4bit",
        "scale": 2,
        "sampling_bits": 4,
        "mem_filename": "model_s_x2_4bit.mem",
    },
    {
        "name": "x4_5bit",
        "scale": 4,
        "sampling_bits": 5,
        "mem_filename": "model_s_x4_5bit.mem",
    },
)


def validate_lut_rows(rows):
    if not isinstance(rows, np.ndarray):
        raise TypeError("rows must be a NumPy array")

    if rows.dtype != np.int8:
        raise TypeError("rows dtype must be int8")

    if rows.ndim != 2:
        raise ValueError(
            "rows must have shape "
            "(entries, values_per_entry)"
        )

    if rows.shape[0] < 1 or rows.shape[1] < 1:
        raise ValueError("rows must not be empty")

    if rows.shape[1] not in (4, 16):
        raise ValueError(
            "values per entry must be 4 or 16"
        )

    return rows.shape


def row_to_mem_line(row):
    if not isinstance(row, np.ndarray):
        raise TypeError("row must be a NumPy array")

    if row.dtype != np.int8:
        raise TypeError("row dtype must be int8")

    if row.ndim != 1 or row.size not in (4, 16):
        raise ValueError(
            "row must contain 4 or 16 int8 values"
        )

    unsigned = np.ascontiguousarray(
        row
    ).view(np.uint8)

    displayed_bytes = unsigned[::-1]

    return (
        displayed_bytes
        .tobytes()
        .hex()
        .upper()
    )


def rows_to_mem_lines(rows):
    validate_lut_rows(rows)

    contiguous = np.ascontiguousarray(rows)

    return [
        row_to_mem_line(contiguous[index])
        for index in range(contiguous.shape[0])
    ]


def mem_line_to_row(
    line,
    values_per_entry,
):
    if not isinstance(line, str):
        raise TypeError("line must be a string")

    if (
        isinstance(values_per_entry, bool)
        or not isinstance(values_per_entry, int)
    ):
        raise TypeError(
            "values_per_entry must be an integer"
        )

    if values_per_entry not in (4, 16):
        raise ValueError(
            "values_per_entry must be 4 or 16"
        )

    text = line.strip()

    if len(text) != values_per_entry * 2:
        raise ValueError(
            "MEM line has an unexpected width"
        )

    if any(
        character not in string.hexdigits
        for character in text
    ):
        raise ValueError(
            "MEM line contains a non-hex character"
        )

    displayed_bytes = bytes.fromhex(text)
    payload_bytes = displayed_bytes[::-1]

    return (
        np.frombuffer(
            payload_bytes,
            dtype=np.uint8,
        )
        .view(np.int8)
        .copy()
    )


def read_mem_rows(
    path,
    values_per_entry,
):
    mem_path = Path(path)

    if not mem_path.is_file():
        raise FileNotFoundError(
            f"MEM file does not exist: {mem_path}"
        )

    lines = mem_path.read_text(
        encoding="ascii"
    ).splitlines()

    if not lines:
        raise ValueError("MEM file is empty")

    payload_parts = [
        mem_line_to_row(
            line,
            values_per_entry,
        )
        .view(np.uint8)
        .tobytes()
        for line in lines
    ]

    payload = b"".join(payload_parts)

    rows = (
        np.frombuffer(
            payload,
            dtype=np.uint8,
        )
        .view(np.int8)
        .reshape(
            len(lines),
            values_per_entry,
        )
        .copy()
    )

    return np.ascontiguousarray(rows)


def write_mem_file(path, lines):
    output_path = Path(path)
    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with output_path.open(
        "w",
        encoding="ascii",
        newline="\n",
    ) as destination:
        for line in lines:
            destination.write(line)
            destination.write("\n")


def generate_configuration(
    project_root,
    configuration,
):
    scale = configuration["scale"]
    sampling_bits = configuration[
        "sampling_bits"
    ]

    spec = LUT_SPECS[
        (scale, sampling_bits)
    ]

    source_path = (
        project_root
        / "data/lut"
        / "original_npy"
        / spec["filename"]
    )

    output_path = (
        project_root
        / "data/lut"
        / "bram_mem"
        / configuration["mem_filename"]
    )

    rows = load_model_s_lut(
        source_path,
        scale,
        sampling_bits,
    )

    lines = rows_to_mem_lines(rows)

    write_mem_file(
        output_path,
        lines,
    )

    reconstructed = read_mem_rows(
        output_path,
        rows.shape[1],
    )

    if not np.array_equal(
        reconstructed,
        rows,
    ):
        raise AssertionError(
            "MEM reconstruction differs from source LUT"
        )

    payload = (
        rows
        .view(np.uint8)
        .tobytes(order="C")
    )

    record = {
        "name": configuration["name"],
        "source_npy": spec["filename"],
        "source_npy_sha256": file_sha256(
            source_path
        ),
        "source_shape": list(spec["shape"]),
        "dtype": str(rows.dtype),
        "entries": rows.shape[0],
        "values_per_entry": rows.shape[1],
        "word_bits": rows.shape[1] * 8,
        "payload_bytes": len(payload),
        "payload_sha256": hashlib.sha256(
            payload
        ).hexdigest().upper(),
        "mem_file": configuration[
            "mem_filename"
        ],
        "mem_size_bytes": output_path.stat().st_size,
        "mem_sha256": file_sha256(
            output_path
        ),
        "first_values": rows[0].tolist(),
        "first_line": lines[0],
        "last_values": rows[-1].tolist(),
        "last_line": lines[-1],
    }

    print(
        f"configuration={record['name']}"
    )
    print(
        f"  entries={record['entries']}"
    )
    print(
        f"  word_bits={record['word_bits']}"
    )
    print(
        f"  payload_bytes={record['payload_bytes']}"
    )
    print(
        f"  first_line={record['first_line']}"
    )
    print(
        f"  last_line={record['last_line']}"
    )
    print(
        f"  mem_sha256={record['mem_sha256']}"
    )

    return record


def main():
    project_root = PROJECT_ROOT

    records = [
        generate_configuration(
            project_root,
            configuration,
        )
        for configuration in MEM_CONFIGURATIONS
    ]

    manifest = {
        "format": (
            "One uppercase hexadecimal word per line"
        ),
        "address_order": (
            "Line number equals flat LUT address"
        ),
        "byte_mapping": (
            "Source LUT value 0 maps to bits 7:0; "
            "value 1 maps to bits 15:8; and so on"
        ),
        "configurations": records,
    }

    manifest_path = (
        project_root
        / "data/lut"
        / "bram_mem"
        / "lut_mem_manifest.json"
    )

    manifest_path.write_text(
        json.dumps(
            manifest,
            indent=2,
        )
        + "\n",
        encoding="ascii",
    )

    print(f"manifest={manifest_path}")
    print("LUT MEM CONversion.txt PASS")


if __name__ == "__main__":
    main()