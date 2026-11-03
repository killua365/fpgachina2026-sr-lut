import argparse

import numpy as np

from model_s_input import load_rgb_u8
from model_s_neighborhoods import DIRECTION_NAMES
from model_s_neighborhoods import extract_direction_neighborhoods
from model_s_quantizer import CHANNEL_NAMES
from model_s_quantizer import quantization_parameters
from model_s_quantizer import quantize_u8


def lut_address_parameters(sampling_bits):
    _, levels = quantization_parameters(sampling_bits)

    depth = levels**4
    strides = (
        levels**3,
        levels**2,
        levels,
        1,
    )
    address_bits = (depth - 1).bit_length()

    return levels, depth, strides, address_bits


def flatten_lut_coordinates(coordinates, levels):
    if not isinstance(coordinates, np.ndarray):
        raise TypeError("coordinates must be a NumPy array")

    if coordinates.ndim < 1 or coordinates.shape[-1] != 4:
        raise ValueError("coordinates must have a final dimension of 4")

    if not np.issubdtype(coordinates.dtype, np.integer):
        raise TypeError("coordinates dtype must be an integer type")

    if not isinstance(levels, int) or levels < 2:
        raise ValueError("levels must be an integer of at least 2")

    wide = coordinates.astype(np.int64)

    if np.any(wide < 0) or np.any(wide >= levels):
        raise ValueError("LUT coordinate is out of range")

    address = (
        (
            (
                wide[..., 0] * levels
                + wide[..., 1]
            )
            * levels
            + wide[..., 2]
        )
        * levels
        + wide[..., 3]
    )

    return np.asarray(address, dtype=np.uint32)


def base_lut_address(lower, sampling_bits):
    _, levels = quantization_parameters(sampling_bits)

    if not isinstance(lower, np.ndarray):
        raise TypeError("lower must be a NumPy array")

    if lower.ndim < 1 or lower.shape[-1] != 4:
        raise ValueError("lower must have a final dimension of 4")

    if not np.issubdtype(lower.dtype, np.integer):
        raise TypeError("lower dtype must be an integer type")

    wide = lower.astype(np.int64)

    if np.any(wide < 0) or np.any(wide >= levels - 1):
        raise ValueError("base LUT coordinate is out of range")

    return flatten_lut_coordinates(lower, levels)


def vertex_lut_address(lower, mask, sampling_bits):
    if isinstance(mask, bool) or not isinstance(mask, int):
        raise TypeError("mask must be an integer")

    if not 0 <= mask <= 15:
        raise ValueError("mask must be between 0 and 15")

    base_address = base_lut_address(lower, sampling_bits)
    _, _, strides, _ = lut_address_parameters(sampling_bits)

    offset = (
        ((mask >> 3) & 1) * strides[0]
        + ((mask >> 2) & 1) * strides[1]
        + ((mask >> 1) & 1) * strides[2]
        + (mask & 1) * strides[3]
    )

    return np.asarray(
        base_address + offset,
        dtype=np.uint32,
    )


def all_vertex_lut_addresses(lower, sampling_bits):
    addresses = tuple(
        vertex_lut_address(lower, mask, sampling_bits)
        for mask in range(16)
    )

    return np.ascontiguousarray(
        np.stack(addresses, axis=-1)
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input_image")
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

    image = load_rgb_u8(args.input_image)
    neighborhoods = extract_direction_neighborhoods(image)
    lower, _, _ = quantize_u8(
        neighborhoods,
        args.sampling_bits,
    )

    height, width, _ = image.shape
    if not 0 <= args.x < width or not 0 <= args.y < height:
        raise ValueError(
            f"pixel coordinate must satisfy 0 <= x < {width} "
            f"and 0 <= y < {height}"
        )

    selected_lower = lower[
        args.direction,
        args.y,
        args.x,
        args.channel,
    ]

    levels, depth, strides, address_bits = lut_address_parameters(
        args.sampling_bits
    )

    base_address = base_lut_address(
        selected_lower,
        args.sampling_bits,
    )
    all_addresses = all_vertex_lut_addresses(
        selected_lower,
        args.sampling_bits,
    )

    print(f"sampling_bits={args.sampling_bits}")
    print(f"levels={levels}")
    print(f"depth={depth}")
    print(f"strides={strides}")
    print(f"address_bits={address_bits}")
    print(f"direction={DIRECTION_NAMES[args.direction]}")
    print(f"channel={CHANNEL_NAMES[args.channel]}")
    print(f"pixel=(x={args.x}, y={args.y})")
    print(f"lower_abcd={selected_lower.tolist()}")
    print(f"base_address={int(base_address)}")

    for mask in (0, 1, 2, 4, 8, 15):
        print(
            f"mask=0b{mask:04b} "
            f"address={int(all_addresses[mask])}"
        )


if __name__ == "__main__":
    main()