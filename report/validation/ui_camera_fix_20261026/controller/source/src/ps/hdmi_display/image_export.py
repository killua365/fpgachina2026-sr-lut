"""Lossless SR/ROI image export with optional real-reference quality scores."""

from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import shutil
import tempfile

import numpy as np
from PIL import Image

from roi_magnifier.quality import PSNR_CROP_BORDER, calculate_psnr_rgb, calculate_ssim_rgb
from .frames import FramePair
from .compositor import resize_nearest
from .media import sha256_file
from .artifact_access import prepare_readable_artifact


def export_image(pair: FramePair, destination: Path, *, reference_rgb=None, source_identity=None,
                 comparison_rgb=None, cancelled=lambda: False):
    destination = Path(destination).resolve()
    if destination.exists() or not destination.parent.is_dir():
        raise ValueError("Choose a new image export directory")
    if reference_rgb is not None:
        if reference_rgb.dtype != np.uint8 or reference_rgb.shape != pair.output_rgb.shape:
            raise ValueError("The true reference must match the unscaled SR result geometry")
        if not source_identity or not source_identity.get("reference_origin"):
            raise ValueError("A real reference origin is required for quality measurements")
    baseline = resize_nearest(pair.source_rgb, (pair.output_rgb.shape[1], pair.output_rgb.shape[0]))
    if comparison_rgb is not None and (comparison_rgb.dtype != np.uint8 or comparison_rgb.ndim != 3
            or comparison_rgb.shape[2] != 3 or min(comparison_rgb.shape[:2]) < 1):
        raise ValueError('Comparison must be a nonempty RGB image')
    if shutil.disk_usage(destination.parent).free < pair.output_rgb.nbytes * 6 + 16 * 1024 * 1024:
        raise OSError("Insufficient space for PNG export")
    temp = Path(tempfile.mkdtemp(prefix=".sr_image_export_", dir=destination.parent))
    try:
        images = {"sr.png": pair.output_rgb, "nearest.png": baseline, "source.png": pair.source_rgb}
        if comparison_rgb is not None:
            images['comparison.png'] = comparison_rgb
        quality = None
        if reference_rgb is not None:
            images["reference.png"] = reference_rgb

            def metrics(image):
                psnr = calculate_psnr_rgb(reference_rgb, image)
                return {"psnr_rgb_db": "infinity" if psnr is not None and math.isinf(psnr) else psnr,
                        "ssim_rgb": calculate_ssim_rgb(reference_rgb, image)}

            quality = {"sr": metrics(pair.output_rgb), "nearest": metrics(baseline),
                       "psnr_crop_border": PSNR_CROP_BORDER,
                       "reference_rgb_sha256": hashlib.sha256(reference_rgb.tobytes()).hexdigest()}
        files = []
        for name, array in images.items():
            if cancelled():
                raise InterruptedError('Snapshot export cancelled')
            path = temp / name
            Image.fromarray(array).save(path, format="PNG")
            with Image.open(path) as decoded:
                if not np.array_equal(np.asarray(decoded), array):
                    raise RuntimeError("PNG read-back differs from the raw image")
            files.append({"path": name, "sha256": sha256_file(path),
                          "rgb_sha256": hashlib.sha256(array.tobytes()).hexdigest()})
        report = {"schema_version": 1, "kind": "hdmi_image_export", "scale": pair.scale,
                  "frame_token": asdict(pair.token), "source_time": pair.source_time,
                  "source_identity": source_identity, "quality": quality, "files": files,
                  "png_readback_exact": True, "golden_reference_verified": False}
        (temp / "export.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        if destination.exists():
            raise FileExistsError(destination)
        prepare_readable_artifact(temp)
        if cancelled():
            raise InterruptedError('Snapshot export cancelled')
        temp.rename(destination)
        return destination / "export.json"
    finally:
        if temp.exists():
            shutil.rmtree(temp)
