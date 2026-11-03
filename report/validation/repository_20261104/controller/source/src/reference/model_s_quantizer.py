import argparse

import numpy as np

from model_s_input import load_rgb_u8
from model_s_neighborhoods import DIRECTION_NAMES
from model_s_neighborhoods import extract_direction_neighborhoods


SUPPORTED_SAMPLING_BITS = (4, 5)
CHANNEL_NAMES = ("R", "G", "B")


def quantization_parameters(sampling_bits):
    if isinstance(sampling_bits, bool) or not isinstance(sampling_bits, int):
        raise TypeError("sampling_bits must be an integer")

    if sampling_bits not in SUPPORTED_SAMPLING_BITS:
        raise ValueError(
            f"sampling_bits must be one of {SUPPORTED_SAMPLING_BITS}"
        )

    q = 1 << sampling_bits
    levels = (256 // q) + 1
    return q, levels


def quantize_u8(values, sampling_bits):
    if not isinstance(values, np.ndarray):
        raise TypeError("values must be a NumPy array")

    if values.dtype != np.uint8:
        raise TypeError("values dtype must be uint8")

    if values.size == 0:
        raise ValueError("values must not be empty")

    q, levels = quantization_parameters(sampling_bits)

    wide_values = values.astype(np.uint16)
    lower = np.right_shift(wide_values, sampling_bits)
    remainder = np.bitwise_and(wide_values, q - 1)
    upper = lower + np.uint16(1)

    if np.any(lower >= levels - 1):
        raise AssertionError("lower LUT coordinate is out of range")

    if np.any(upper >= levels):
        raise AssertionError("upper LUT coordinate is out of range")

    if np.any(remainder >= q):
        raise AssertionError("quantization remainder is out of range")

    return (
        np.ascontiguousarray(lower),
        np.ascontiguousarray(upper),
        np.ascontiguousarray(remainder),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input_image")
    parser.add_argument(
        "--sampling-bits",
        type=int,
        choices=SUPPORTED_SAMPLING_BITS,
        default=5,
    )
    parser.add_argument("--x", type=int, default=0)
    parser.add_argument("--y", type=int, default=0)
    parser.add_argument("--direction", type=int, choices=range(4), default=0)
    parser.add_argument("--channel", type=int, choices=range(3), default=0)
    args = parser.parse_args()

    image = load_rgb_u8(args.input_image)
    neighborhoods = extract_direction_neighborhoods(image)
    lower, upper, remainder = quantize_u8(
        neighborhoods,
        args.sampling_bits,
    )

    height, width, _ = image.shape
    if not 0 <= args.x < width or not 0 <= args.y < height:
        raise ValueError(
            f"pixel coordinate must satisfy 0 <= x < {width} "
            f"and 0 <= y < {height}"
        )

    q, levels = quantization_parameters(args.sampling_bits)

    selected = neighborhoods[
        args.direction,
        args.y,
        args.x,
        args.channel,
    ]
    selected_lower = lower[
        args.direction,
        args.y,
        args.x,
        args.channel,
    ]
    selected_upper = upper[
        args.direction,
        args.y,
        args.x,
        args.channel,
    ]
    selected_remainder = remainder[
        args.direction,
        args.y,
        args.x,
        args.channel,
    ]

    reconstructed = selected_lower * q + selected_remainder

    print(f"sampling_bits={args.sampling_bits}")
    print(f"q={q}")
    print(f"levels={levels}")
    print(f"direction={DIRECTION_NAMES[args.direction]}")
    print(f"channel={CHANNEL_NAMES[args.channel]}")
    print(f"pixel=(x={args.x}, y={args.y})")
    print(f"input_abcd={selected.tolist()}")
    print(f"lower_abcd={selected_lower.tolist()}")
    print(f"upper_abcd={selected_upper.tolist()}")
    print(f"remainder_abcd={selected_remainder.tolist()}")
    print(f"reconstructed_abcd={reconstructed.tolist()}")


if __name__ == "__main__":
    main()