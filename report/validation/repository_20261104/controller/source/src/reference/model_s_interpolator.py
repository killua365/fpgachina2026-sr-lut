import argparse
import hashlib
from pathlib import Path

import numpy as np

from model_s_address import base_lut_address
from model_s_address import lut_address_parameters
from model_s_input import load_rgb_u8
from model_s_neighborhoods import DIRECTION_NAMES
from model_s_neighborhoods import extract_direction_neighborhoods
from model_s_quantizer import CHANNEL_NAMES
from model_s_quantizer import quantization_parameters
from model_s_quantizer import quantize_u8
from model_s_simplex import select_simplex_paths


LUT_SPECS = {
    (2, 4): {
        "filename": "model_s_x2_4bit_int8.npy",
        "shape": (83521, 1, 2, 2),
        "sha256": (
            "04745833137253B1BAAC2146E0D21D415C181F17D0780ECED6C33CE12DE2AC82"
        ),
    },
    (4, 4): {
        "filename": "model_s_x4_4bit_int8.npy",
        "shape": (83521, 1, 4, 4),
        "sha256": (
            "AD3FE9046BB664AFADE06C703CD3398273723C19AF30400DE4C241680085CBA9"
        ),
    },
    (4, 5): {
        "filename": "model_s_x4_5bit_int8.npy",
        "shape": (6561, 1, 4, 4),
        "sha256": (
            "EC7B0026798475DDA56338FCE22EF75F4647B7455DAE8DE65DD9CF20FBB2BF01"
        ),
    },
}


def get_lut_spec(scale, sampling_bits):
    key = (scale, sampling_bits)

    if key not in LUT_SPECS:
        raise ValueError(
            f"unsupported LUT configuration: "
            f"scale={scale}, sampling_bits={sampling_bits}"
        )

    return LUT_SPECS[key]


def file_sha256(path):
    digest = hashlib.sha256()

    with Path(path).open("rb") as source:
        while True:
            block = source.read(1024 * 1024)
            if not block:
                break
            digest.update(block)

    return digest.hexdigest().upper()


def validate_flat_lut(lut, scale, sampling_bits):
    spec = get_lut_spec(scale, sampling_bits)
    expected_depth = spec["shape"][0]
    expected_values = scale * scale

    if not isinstance(lut, np.ndarray):
        raise TypeError("lut must be a NumPy array")

    if lut.dtype != np.int8:
        raise TypeError("lut dtype must be int8")

    if lut.shape != (expected_depth, expected_values):
        raise ValueError(
            f"flat LUT shape must be "
            f"{(expected_depth, expected_values)}, "
            f"but received {lut.shape}"
        )


def load_model_s_lut(path, scale, sampling_bits):
    lut_path = Path(path)
    spec = get_lut_spec(scale, sampling_bits)

    if not lut_path.is_file():
        raise FileNotFoundError(f"LUT file does not exist: {lut_path}")

    actual_hash = file_sha256(lut_path)
    if actual_hash != spec["sha256"]:
        raise ValueError(
            f"LUT SHA-256 mismatch: expected {spec['sha256']}, "
            f"received {actual_hash}"
        )

    source = np.load(lut_path, allow_pickle=False)

    if source.dtype != np.int8:
        raise TypeError("source LUT dtype must be int8")

    if source.shape != spec["shape"]:
        raise ValueError(
            f"source LUT shape must be {spec['shape']}, "
            f"but received {source.shape}"
        )

    flat = np.ascontiguousarray(
        source.reshape(spec["shape"][0], scale * scale)
    )
    validate_flat_lut(flat, scale, sampling_bits)
    return flat


def mask_offset_table(sampling_bits):
    _, _, strides, _ = lut_address_parameters(sampling_bits)

    offsets = np.empty(16, dtype=np.uint32)

    for mask in range(16):
        offsets[mask] = (
            ((mask >> 3) & 1) * strides[0]
            + ((mask >> 2) & 1) * strides[1]
            + ((mask >> 1) & 1) * strides[2]
            + (mask & 1) * strides[3]
        )

    return offsets


def interpolate_lut_numerators(
    lut,
    lower,
    remainders,
    scale,
    sampling_bits,
    return_trace=False,
):
    validate_flat_lut(lut, scale, sampling_bits)

    if not isinstance(lower, np.ndarray):
        raise TypeError("lower must be a NumPy array")

    if not isinstance(remainders, np.ndarray):
        raise TypeError("remainders must be a NumPy array")

    if lower.shape != remainders.shape:
        raise ValueError("lower and remainders must have the same shape")

    if lower.ndim < 1 or lower.shape[-1] != 4:
        raise ValueError("quantized inputs must end with four dimensions")

    if not np.issubdtype(lower.dtype, np.integer):
        raise TypeError("lower dtype must be an integer type")

    if not np.issubdtype(remainders.dtype, np.integer):
        raise TypeError("remainders dtype must be an integer type")

    q, levels = quantization_parameters(sampling_bits)

    if np.any(lower < 0) or np.any(lower >= levels - 1):
        raise ValueError("lower LUT coordinate is out of range")

    if np.any(remainders < 0) or np.any(remainders >= q):
        raise ValueError("interpolation remainder is out of range")

    masks, weights = select_simplex_paths(
        remainders,
        sampling_bits,
    )

    base_addresses = base_lut_address(
        lower,
        sampling_bits,
    )

    offsets = mask_offset_table(sampling_bits)
    addresses = (
        base_addresses[..., np.newaxis]
        + offsets[masks]
    ).astype(np.uint32)

    if np.any(addresses >= lut.shape[0]):
        raise AssertionError("generated LUT address is out of range")

    vertex_values = lut[addresses].astype(np.int32)
    signed_weights = weights[..., :, np.newaxis].astype(np.int32)

    numerator = np.sum(
        vertex_values * signed_weights,
        axis=-2,
        dtype=np.int32,
    )

    output_shape = lower.shape[:-1] + (scale, scale)
    numerator = np.ascontiguousarray(
        numerator.reshape(output_shape)
    )

    if return_trace:
        return numerator, masks, weights, addresses

    return numerator


def interpolate_image_native_numerators(
    image,
    lut,
    scale,
    sampling_bits,
):
    neighborhoods = extract_direction_neighborhoods(image)
    lower, _, remainders = quantize_u8(
        neighborhoods,
        sampling_bits,
    )

    return interpolate_lut_numerators(
        lut,
        lower,
        remainders,
        scale,
        sampling_bits,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input_image")
    parser.add_argument("--scale", type=int, choices=(2, 4), default=4)
    parser.add_argument(
        "--sampling-bits",
        type=int,
        choices=(4, 5),
        default=5,
    )
    parser.add_argument("--x", type=int, default=0)
    parser.add_argument("--y", type=int, default=0)
    parser.add_argument("--direction", type=int, choices=range(4), default=0)
    parser.add_argument("--channel", type=int, choices=range(3), default=0)
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[2]
    spec = get_lut_spec(args.scale, args.sampling_bits)
    lut_path = (
        project_root
        / "data/lut"
        / "original_npy"
        / spec["filename"]
    )

    image = load_rgb_u8(args.input_image)
    neighborhoods = extract_direction_neighborhoods(image)
    lower, _, remainders = quantize_u8(
        neighborhoods,
        args.sampling_bits,
    )
    lut = load_model_s_lut(
        lut_path,
        args.scale,
        args.sampling_bits,
    )

    selected_lower = lower[
        args.direction,
        args.y,
        args.x,
        args.channel,
    ]
    selected_remainders = remainders[
        args.direction,
        args.y,
        args.x,
        args.channel,
    ]

    numerator, masks, weights, addresses = (
        interpolate_lut_numerators(
            lut,
            selected_lower,
            selected_remainders,
            args.scale,
            args.sampling_bits,
            return_trace=True,
        )
    )

    vertex_values = lut[addresses]
    q, _ = quantization_parameters(args.sampling_bits)

    print(f"lut={lut_path.name}")
    print(f"direction={DIRECTION_NAMES[args.direction]}")
    print(f"channel={CHANNEL_NAMES[args.channel]}")
    print(f"pixel=(x={args.x}, y={args.y})")
    print(f"lower_abcd={selected_lower.tolist()}")
    print(f"remainder_abcd={selected_remainders.tolist()}")
    print(f"masks={masks.tolist()}")
    print(f"weights={weights.tolist()}")
    print(f"addresses={addresses.tolist()}")
    print(f"vertex_values={vertex_values.tolist()}")
    print(f"numerator_flat={numerator.reshape(-1).tolist()}")
    print(
        "direction_div_q="
        f"{(numerator.astype(np.float64).reshape(-1) / q).tolist()}"
    )


if __name__ == "__main__":
    main()