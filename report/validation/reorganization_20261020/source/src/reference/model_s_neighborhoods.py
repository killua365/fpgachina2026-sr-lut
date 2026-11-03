import argparse
from pathlib import Path

import numpy as np

from model_s_input import load_rgb_u8
from model_s_input import reflect_pad_one
from model_s_input import validate_rgb_u8


DIRECTION_NAMES = (
    "right_down",
    "down_left",
    "left_up",
    "up_right",
)

NEIGHBOR_NAMES = ("a", "b", "c", "d")


def extract_direction_neighborhoods(image):
    height, width = validate_rgb_u8(image)
    padded = reflect_pad_one(image)

    center = padded[1 : height + 1, 1 : width + 1]
    right = padded[1 : height + 1, 2 : width + 2]
    left = padded[1 : height + 1, 0:width]
    up = padded[0:height, 1 : width + 1]
    down = padded[2 : height + 2, 1 : width + 1]

    right_down = np.stack(
        (
            center,
            right,
            down,
            padded[2 : height + 2, 2 : width + 2],
        ),
        axis=-1,
    )

    down_left = np.stack(
        (
            center,
            down,
            left,
            padded[2 : height + 2, 0:width],
        ),
        axis=-1,
    )

    left_up = np.stack(
        (
            center,
            left,
            up,
            padded[0:height, 0:width],
        ),
        axis=-1,
    )

    up_right = np.stack(
        (
            center,
            up,
            right,
            padded[0:height, 2 : width + 2],
        ),
        axis=-1,
    )

    neighborhoods = np.stack(
        (
            right_down,
            down_left,
            left_up,
            up_right,
        ),
        axis=0,
    )

    return np.ascontiguousarray(neighborhoods)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input_image")
    parser.add_argument("--x", type=int, default=0)
    parser.add_argument("--y", type=int, default=0)
    args = parser.parse_args()

    image = load_rgb_u8(args.input_image)
    neighborhoods = extract_direction_neighborhoods(image)

    height, width, _ = image.shape
    if not 0 <= args.x < width or not 0 <= args.y < height:
        raise ValueError(
            f"pixel coordinate must satisfy 0 <= x < {width} "
            f"and 0 <= y < {height}"
        )

    print(f"input_path={Path(args.input_image).resolve()}")
    print(f"input_shape={image.shape}")
    print(f"neighborhood_shape={neighborhoods.shape}")
    print(f"dtype={neighborhoods.dtype}")
    print(f"selected_pixel=(x={args.x}, y={args.y})")

    for direction_index, direction_name in enumerate(DIRECTION_NAMES):
        channel_first = neighborhoods[
            direction_index,
            args.y,
            args.x,
        ]
        rgb_values = channel_first.transpose(1, 0)

        values_text = " ".join(
            f"{name}={rgb_values[index].tolist()}"
            for index, name in enumerate(NEIGHBOR_NAMES)
        )
        print(f"{direction_name}: {values_text}")


if __name__ == "__main__":
    main()