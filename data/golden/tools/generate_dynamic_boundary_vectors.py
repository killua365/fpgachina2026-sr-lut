import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[3]
REFERENCE_ROOT = PROJECT_ROOT / "src/reference"
if str(REFERENCE_ROOT) not in sys.path:
    sys.path.insert(0, str(REFERENCE_ROOT))

from model_s_interpolator import load_model_s_lut
from model_s_output import run_model_s


CASES = (
    ("minimum_constant", 32, 32, "constant128"),
    ("odd_gradient", 319, 257, "coordinate_gradient"),
    ("maximum_constant", 320, 320, "constant128"),
)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def make_input(width, height, pattern):
    if pattern == "constant128":
        return np.full((height, width, 3), 128, dtype=np.uint8)
    y, x = np.indices((height, width), dtype=np.uint32)
    return np.stack(
        (
            (3 * x + 5 * y + 17) & 0xFF,
            (11 * x + 7 * y + 29) & 0xFF,
            (13 * x + 19 * y + 43) & 0xFF,
        ),
        axis=-1,
    ).astype(np.uint8)


def save_png(array, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(array, mode="RGB").save(path)


def build_manifest():
    vector_root = PROJECT_ROOT / "data/golden"
    input_root = vector_root / "source_images" / "dynamic_boundaries"
    golden_root = vector_root / "reference_baselines" / "x4_5bit_boundaries"
    lut = load_model_s_lut(
        PROJECT_ROOT
        / "data/lut"
        / "original_npy"
        / "model_s_x4_5bit_int8.npy",
        4,
        5,
    )
    images = []
    for name, width, height, pattern in CASES:
        input_rgb = make_input(width, height, pattern)
        golden_rgb = run_model_s(input_rgb, lut, 4, 5)
        input_path = input_root / f"{name}.png"
        golden_path = golden_root / f"{name}.png"
        save_png(input_rgb, input_path)
        save_png(golden_rgb, golden_path)
        images.append(
            {
                "name": name,
                "pattern": pattern,
                "input": input_path.relative_to(vector_root).as_posix(),
                "golden": golden_path.relative_to(vector_root).as_posix(),
                "input_shape_hwc": list(input_rgb.shape),
                "output_shape_hwc": list(golden_rgb.shape),
                "input_sha256": sha256(input_path),
                "golden_sha256": sha256(golden_path),
            }
        )
    return {
        "schema_version": 1,
        "configuration": "Model-S x4 5-bit dynamic boundary vectors",
        "scale": 4,
        "sampling_bits": 5,
        "images": images,
    }


def verify_manifest(manifest):
    vector_root = PROJECT_ROOT / "data/golden"
    for item in manifest["images"]:
        input_path = vector_root / item["input"]
        golden_path = vector_root / item["golden"]
        if sha256(input_path) != item["input_sha256"]:
            raise AssertionError(f"input hash mismatch: {item['name']}")
        if sha256(golden_path) != item["golden_sha256"]:
            raise AssertionError(f"golden hash mismatch: {item['name']}")
        with Image.open(input_path) as image:
            if [image.height, image.width, 3] != item["input_shape_hwc"]:
                raise AssertionError(f"input shape mismatch: {item['name']}")
        with Image.open(golden_path) as image:
            if [image.height, image.width, 3] != item["output_shape_hwc"]:
                raise AssertionError(f"golden shape mismatch: {item['name']}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    manifest_path = (
        PROJECT_ROOT / "data/golden" / "dynamic_x4_boundary_manifest.json"
    )
    if args.verify_only:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = build_manifest()
        manifest_path.write_text(
            json.dumps(manifest, indent=2) + "\n",
            encoding="utf-8",
        )
    verify_manifest(manifest)
    print(f"DYNAMIC_X4_BOUNDARY_VECTORS_PASS cases={len(manifest['images'])}")
    print(f"manifest={manifest_path}")


if __name__ == "__main__":
    main()
