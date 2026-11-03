import argparse
from pathlib import Path

import numpy as np
from PIL import Image

from model_s_fusion import infer_image_blocks
from model_s_input import load_rgb_u8
from model_s_interpolator import get_lut_spec
from model_s_interpolator import load_model_s_lut


def validate_output_blocks(blocks):
    if not isinstance(blocks, np.ndarray):
        raise TypeError("blocks must be a NumPy array")

    if blocks.dtype != np.uint8:
        raise TypeError("blocks dtype must be uint8")

    if blocks.ndim != 5:
        raise ValueError(
            "blocks must have shape "
            "(H, W, 3, scale, scale)"
        )

    if blocks.shape[0] < 1 or blocks.shape[1] < 1:
        raise ValueError(
            "block image dimensions must not be empty"
        )

    if blocks.shape[2] != 3:
        raise ValueError(
            "blocks must contain three RGB channels"
        )

    if (
        blocks.shape[3] != blocks.shape[4]
        or blocks.shape[3] not in (2, 4)
    ):
        raise ValueError(
            "blocks must be square with scale 2 or 4"
        )

    return (
        blocks.shape[0],
        blocks.shape[1],
        blocks.shape[3],
    )


def stitch_output_blocks(blocks):
    height, width, scale = validate_output_blocks(blocks)

    output = (
        blocks
        .transpose(0, 3, 1, 4, 2)
        .reshape(
            height * scale,
            width * scale,
            3,
        )
    )

    return np.ascontiguousarray(output)


def run_model_s(image, lut, scale, sampling_bits):
    blocks = infer_image_blocks(
        image,
        lut,
        scale,
        sampling_bits,
    )

    if blocks.shape[-2:] != (scale, scale):
        raise AssertionError(
            "interpolator returned an unexpected block size"
        )

    return stitch_output_blocks(blocks)


def save_rgb_u8(image, path):
    if (
        not isinstance(image, np.ndarray)
        or image.dtype != np.uint8
        or image.ndim != 3
        or image.shape[2] != 3
    ):
        raise TypeError(
            "image must be an RGB uint8 NumPy array"
        )

    output_path = Path(path)

    if not output_path.parent.is_dir():
        raise FileNotFoundError(
            "output directory does not exist: "
            f"{output_path.parent}"
        )

    Image.fromarray(
        image,
        mode="RGB",
    ).save(output_path)

    return output_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input_image")
    parser.add_argument("output_image")
    parser.add_argument(
        "--scale",
        type=int,
        choices=(2, 4),
        default=4,
    )
    parser.add_argument(
        "--sampling-bits",
        type=int,
        choices=(4, 5),
        default=4,
    )
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[2]
    spec = get_lut_spec(
        args.scale,
        args.sampling_bits,
    )

    lut_path = (
        project_root
        / "data/lut"
        / "original_npy"
        / spec["filename"]
    )

    image = load_rgb_u8(args.input_image)

    lut = load_model_s_lut(
        lut_path,
        args.scale,
        args.sampling_bits,
    )

    output = run_model_s(
        image,
        lut,
        args.scale,
        args.sampling_bits,
    )

    output_path = save_rgb_u8(
        output,
        args.output_image,
    )

    print(
        f"configuration=x{args.scale}_"
        f"{args.sampling_bits}bit"
    )
    print(f"input_shape={image.shape}")
    print(f"output_shape={output.shape}")
    print(f"output_dtype={output.dtype}")
    print(f"output_min={int(output.min())}")
    print(f"output_max={int(output.max())}")
    print(f"saved={output_path.resolve()}")


if __name__ == "__main__":
    main()