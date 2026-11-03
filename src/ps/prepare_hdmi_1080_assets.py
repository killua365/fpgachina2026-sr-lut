"""Build a 1080p demo set from complete source photographs.

Outputs are written to a new directory and never overwrite the frozen 720p
regression set. The same Model-S reference used by the FPGA vector generator
produces the golden image, so the manifest can be bound to a later BIT/HWH.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""): h.update(b)
    return h.hexdigest().upper()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--source", type=Path, default=Path("data/images/demo_assets/images/original"))
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--scale", type=int, choices=(2, 4), default=4)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    records = []
    for source in sorted(args.source.glob("*.png")):
        with Image.open(source) as im:
            rgb = np.asarray(im.convert("RGB"), dtype=np.uint8)
        # 480x270 is the contractual 16:9 source for x4 1080p. Letterbox
        # rather than stretch, preserving the complete photograph.
        canvas = np.zeros((270, 480, 3), np.uint8)
        ratio = min(480 / rgb.shape[1], 270 / rgb.shape[0])
        size = (max(1, round(rgb.shape[1] * ratio)), max(1, round(rgb.shape[0] * ratio)))
        resized = cv2.resize(rgb, size, interpolation=cv2.INTER_AREA)
        y, x = (270 - size[1]) // 2, (480 - size[0]) // 2
        canvas[y:y + size[1], x:x + size[0]] = resized
        stem = source.stem.replace("_gt", "")
        inp = args.output / f"{stem}_input_480x270.png"
        Image.fromarray(canvas).save(inp)
        # Import lazily so this utility remains inspectable without the model
        # dependencies; execution on the development image has them installed.
        from model_s_interpolator import get_lut_spec, load_model_s_lut
        from model_s_output import run_model_s
        spec = get_lut_spec(4, 5)
        lut = load_model_s_lut(Path("data/lut/original_npy") / spec["filename"], 4, 5)
        golden = run_model_s(canvas, lut, 4, 5)
        out = args.output / f"{stem}_golden_1920x1080.png"
        Image.fromarray(golden).save(out)
        records.append({"name": stem, "input": inp.name, "golden": out.name,
                        "input_sha256": digest(inp), "golden_sha256": digest(out),
                        "source_sha256": digest(source), "mode": "1920x1080@30"})
    manifest = {"schema_version": 1, "kind": "hdmi_1080p_demo_assets",
                "source_policy": "complete_single_rgb_image_no_flip_or_fold",
                "scale": 4, "input_size": [480, 270], "output_size": [1920, 1080],
                "images": records}
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__": raise SystemExit(main())
