import argparse
from pathlib import Path

import numpy as np
from PIL import Image


def validate_rgb_u8(image):
    if not isinstance(image, np.ndarray):
        raise TypeError("image must be a NumPy array")

    if image.ndim != 3:
        raise ValueError("image must have shape (height, width, 3)")

    if image.shape[2] != 3:
        raise ValueError("image must contain exactly three RGB channels")

    if image.dtype != np.uint8:
        raise TypeError("image dtype must be uint8")

    height, width, _ = image.shape
    if height < 2 or width < 2:
        raise ValueError("image height and width must both be at least 2")

    return height, width


def load_rgb_u8(path):
    image_path = Path(path)

    if not image_path.is_file():
        raise FileNotFoundError(f"input image does not exist: {image_path}")

    with Image.open(image_path) as source:
        rgb_image = source.convert("RGB")
        image = np.array(rgb_image, dtype=np.uint8, copy=True)

    validate_rgb_u8(image)
    return image


def reflect_pad_one(image):
    validate_rgb_u8(image)

    padded = np.pad(
        image,
        ((1, 1), (1, 1), (0, 0)),
        mode="reflect",
    )

    return np.ascontiguousarray(padded)


def load_and_pad_rgb_u8(path):
    image = load_rgb_u8(path)
    padded = reflect_pad_one(image)
    return image, padded


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input_image")
    args = parser.parse_args()

    image, padded = load_and_pad_rgb_u8(args.input_image)

    print(f"input_path={Path(args.input_image).resolve()}")
    print(f"input_shape={image.shape}")
    print(f"padded_shape={padded.shape}")
    print(f"dtype={image.dtype}")
    print(f"interior_preserved={np.array_equal(padded[1:-1, 1:-1], image)}")
    print(f"top_left_matches_input_1_1={np.array_equal(padded[0, 0], image[1, 1])}")


if __name__ == "__main__":
    main()