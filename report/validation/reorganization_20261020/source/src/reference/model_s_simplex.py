import argparse

import numpy as np

from model_s_address import vertex_lut_address
from model_s_input import load_rgb_u8
from model_s_neighborhoods import DIRECTION_NAMES
from model_s_neighborhoods import extract_direction_neighborhoods
from model_s_quantizer import CHANNEL_NAMES
from model_s_quantizer import quantization_parameters
from model_s_quantizer import quantize_u8


def _result(mask1, mask2, mask3, w0, w1, w2, w3, w4):
    masks = np.array(
        [0, mask1, mask2, mask3, 15],
        dtype=np.uint8,
    )
    weights = np.array(
        [w0, w1, w2, w3, w4],
        dtype=np.int16,
    )
    return masks, weights


def _select_simplex_q(fa, fb, fc, fd, q):
    if fa > fb:
        if fb > fc:
            if fc > fd:
                return _result(
                    8, 12, 14,
                    q - fa, fa - fb, fb - fc, fc - fd, fd,
                )
            if fb > fd:
                return _result(
                    8, 12, 13,
                    q - fa, fa - fb, fb - fd, fd - fc, fc,
                )
            if fa > fd:
                return _result(
                    8, 9, 13,
                    q - fa, fa - fd, fd - fb, fb - fc, fc,
                )
            return _result(
                1, 9, 13,
                q - fd, fd - fa, fa - fb, fb - fc, fc,
            )

        if fa > fc:
            if fb > fd:
                return _result(
                    8, 10, 14,
                    q - fa, fa - fc, fc - fb, fb - fd, fd,
                )
            if fc > fd:
                return _result(
                    8, 10, 11,
                    q - fa, fa - fc, fc - fd, fd - fb, fb,
                )
            if fa > fd:
                return _result(
                    8, 9, 11,
                    q - fa, fa - fd, fd - fc, fc - fb, fb,
                )
            return _result(
                1, 9, 11,
                q - fd, fd - fa, fa - fc, fc - fb, fb,
            )

        if fb > fd:
            return _result(
                2, 10, 14,
                q - fc, fc - fa, fa - fb, fb - fd, fd,
            )
        if fc > fd:
            return _result(
                2, 10, 11,
                q - fc, fc - fa, fa - fd, fd - fb, fb,
            )
        if fa > fd:
            return _result(
                2, 3, 11,
                q - fc, fc - fd, fd - fa, fa - fb, fb,
            )
        return _result(
            1, 3, 11,
            q - fd, fd - fc, fc - fa, fa - fb, fb,
        )

    if fa > fc:
        if fc > fd:
            return _result(
                4, 12, 14,
                q - fb, fb - fa, fa - fc, fc - fd, fd,
            )
        if fa > fd:
            return _result(
                4, 12, 13,
                q - fb, fb - fa, fa - fd, fd - fc, fc,
            )
        if fb > fd:
            return _result(
                4, 5, 13,
                q - fb, fb - fd, fd - fa, fa - fc, fc,
            )
        return _result(
            1, 5, 13,
            q - fd, fd - fb, fb - fa, fa - fc, fc,
        )

    if fb > fc:
        if fa > fd:
            return _result(
                4, 6, 14,
                q - fb, fb - fc, fc - fa, fa - fd, fd,
            )
        if fc > fd:
            return _result(
                4, 6, 7,
                q - fb, fb - fc, fc - fd, fd - fa, fa,
            )
        if fb > fd:
            return _result(
                4, 5, 7,
                q - fb, fb - fd, fd - fc, fc - fa, fa,
            )
        return _result(
            1, 5, 7,
            q - fd, fd - fb, fb - fc, fc - fa, fa,
        )

    if fa > fd:
        return _result(
            2, 6, 14,
            q - fc, fc - fb, fb - fa, fa - fd, fd,
        )
    if fb > fd:
        return _result(
            2, 6, 7,
            q - fc, fc - fb, fb - fd, fd - fa, fa,
        )
    if fc > fd:
        return _result(
            2, 3, 7,
            q - fc, fc - fd, fd - fb, fb - fa, fa,
        )
    return _result(
        1, 3, 7,
        q - fd, fd - fc, fc - fb, fb - fa, fa,
    )


def select_simplex_scalar(fa, fb, fc, fd, sampling_bits):
    q, _ = quantization_parameters(sampling_bits)

    values = (fa, fb, fc, fd)
    checked = []

    for value in values:
        if isinstance(value, bool) or not isinstance(
            value,
            (int, np.integer),
        ):
            raise TypeError("simplex remainder must be an integer")

        integer_value = int(value)
        if not 0 <= integer_value < q:
            raise ValueError("simplex remainder is out of range")

        checked.append(integer_value)

    return _select_simplex_q(*checked, q)


def select_simplex_paths(remainders, sampling_bits):
    q, _ = quantization_parameters(sampling_bits)

    if not isinstance(remainders, np.ndarray):
        raise TypeError("remainders must be a NumPy array")

    if remainders.ndim < 1 or remainders.shape[-1] != 4:
        raise ValueError("remainders must have a final dimension of 4")

    if not np.issubdtype(remainders.dtype, np.integer):
        raise TypeError("remainders dtype must be an integer type")

    wide = remainders.astype(np.int16)

    if np.any(wide < 0) or np.any(wide >= q):
        raise ValueError("simplex remainder is out of range")

    flat = wide.reshape(-1, 4)
    masks = np.empty((flat.shape[0], 5), dtype=np.uint8)
    weights = np.empty((flat.shape[0], 5), dtype=np.int16)

    for index, values in enumerate(flat):
        selected_masks, selected_weights = _select_simplex_q(
            int(values[0]),
            int(values[1]),
            int(values[2]),
            int(values[3]),
            q,
        )
        masks[index] = selected_masks
        weights[index] = selected_weights

    output_shape = remainders.shape[:-1] + (5,)

    return (
        np.ascontiguousarray(masks.reshape(output_shape)),
        np.ascontiguousarray(weights.reshape(output_shape)),
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
    lower, _, remainders = quantize_u8(
        neighborhoods,
        args.sampling_bits,
    )

    height, width, _ = image.shape
    if not 0 <= args.x < width or not 0 <= args.y < height:
        raise ValueError("selected pixel is outside the image")

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

    masks, weights = select_simplex_scalar(
        int(selected_remainders[0]),
        int(selected_remainders[1]),
        int(selected_remainders[2]),
        int(selected_remainders[3]),
        args.sampling_bits,
    )

    addresses = [
        int(
            vertex_lut_address(
                selected_lower,
                int(mask),
                args.sampling_bits,
            )
        )
        for mask in masks
    ]

    q, _ = quantization_parameters(args.sampling_bits)

    print(f"sampling_bits={args.sampling_bits}")
    print(f"q={q}")
    print(f"direction={DIRECTION_NAMES[args.direction]}")
    print(f"channel={CHANNEL_NAMES[args.channel]}")
    print(f"pixel=(x={args.x}, y={args.y})")
    print(f"lower_abcd={selected_lower.tolist()}")
    print(f"remainder_abcd={selected_remainders.tolist()}")
    print(f"masks={masks.tolist()}")
    print(f"weights={weights.tolist()}")
    print(f"weight_sum={int(weights.sum())}")
    print(f"vertex_addresses={addresses}")


if __name__ == "__main__":
    main()