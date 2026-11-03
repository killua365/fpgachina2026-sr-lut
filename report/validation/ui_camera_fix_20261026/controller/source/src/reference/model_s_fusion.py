import numpy as np

from model_s_interpolator import interpolate_image_native_numerators
from model_s_quantizer import quantization_parameters


def validate_direction_numerators(numerators):
    if not isinstance(numerators, np.ndarray):
        raise TypeError("numerators must be a NumPy array")

    if numerators.dtype != np.int32:
        raise TypeError("numerators dtype must be int32")

    if numerators.ndim != 6:
        raise ValueError(
            "numerators must have shape "
            "(4, H, W, 3, scale, scale)"
        )

    if numerators.shape[0] != 4 or numerators.shape[3] != 3:
        raise ValueError(
            "numerators must contain four directions "
            "and three RGB channels"
        )

    if numerators.shape[1] < 1 or numerators.shape[2] < 1:
        raise ValueError("numerator image dimensions must not be empty")

    if (
        numerators.shape[4] != numerators.shape[5]
        or numerators.shape[4] not in (2, 4)
    ):
        raise ValueError(
            "numerator blocks must be square with scale 2 or 4"
        )

    return (
        numerators.shape[1],
        numerators.shape[2],
        numerators.shape[4],
    )


def restore_direction_blocks(numerators):
    validate_direction_numerators(numerators)

    restored = np.stack(
        [
            np.rot90(
                numerators[direction],
                -direction,
                axes=(-2, -1),
            )
            for direction in range(4)
        ],
        axis=0,
    )

    return np.ascontiguousarray(restored)


def round_divide_ties_to_even(values, divisor):
    if not isinstance(values, np.ndarray):
        raise TypeError("values must be a NumPy array")

    if not np.issubdtype(values.dtype, np.integer):
        raise TypeError("values dtype must be an integer type")

    if values.size == 0:
        raise ValueError("values must not be empty")

    if (
        isinstance(divisor, bool)
        or not isinstance(divisor, (int, np.integer))
    ):
        raise TypeError("divisor must be an integer")

    divisor = int(divisor)

    if divisor <= 0:
        raise ValueError("divisor must be positive")

    wide = values.astype(np.int64)

    if np.any(wide == np.iinfo(np.int64).min):
        raise OverflowError(
            "cannot round the minimum int64 value safely"
        )

    magnitude = np.abs(wide)
    quotient = magnitude // divisor
    remainder = magnitude % divisor
    doubled = remainder * 2

    increment = (
        (doubled > divisor)
        | (
            (doubled == divisor)
            & ((quotient & 1) == 1)
        )
    )

    rounded_magnitude = (
        quotient + increment.astype(np.int64)
    )

    rounded = np.where(
        wide < 0,
        -rounded_magnitude,
        rounded_magnitude,
    )

    limits = np.iinfo(np.int32)

    if (
        np.any(rounded < limits.min)
        or np.any(rounded > limits.max)
    ):
        raise OverflowError(
            "rounded result does not fit in int32"
        )

    return np.ascontiguousarray(
        rounded.astype(np.int32)
    )


def fuse_direction_numerators(
    numerators,
    sampling_bits,
    return_trace=False,
):
    validate_direction_numerators(numerators)
    q, _ = quantization_parameters(sampling_bits)

    restored = restore_direction_blocks(numerators)

    fused_wide = np.sum(
        restored,
        axis=0,
        dtype=np.int64,
    )

    limits = np.iinfo(np.int32)

    if (
        np.any(fused_wide < limits.min)
        or np.any(fused_wide > limits.max)
    ):
        raise OverflowError(
            "fused numerator does not fit in int32"
        )

    fused = np.ascontiguousarray(
        fused_wide.astype(np.int32)
    )

    rounded = round_divide_ties_to_even(
        fused,
        q,
    )

    output = np.ascontiguousarray(
        np.clip(rounded, 0, 255).astype(np.uint8)
    )

    if return_trace:
        return output, restored, fused, rounded

    return output


def infer_image_blocks(
    image,
    lut,
    scale,
    sampling_bits,
    return_trace=False,
):
    native = interpolate_image_native_numerators(
        image,
        lut,
        scale,
        sampling_bits,
    )

    result = fuse_direction_numerators(
        native,
        sampling_bits,
        return_trace=return_trace,
    )

    if return_trace:
        output, restored, fused, rounded = result
        return output, native, restored, fused, rounded

    return result