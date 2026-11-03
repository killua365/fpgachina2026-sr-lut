"""Live ROI comparison images and Y/RGB quality metrics."""

from __future__ import annotations

import math
from typing import Any, Mapping, Optional

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover - portable fallback
    cv2 = None

try:
    from .geometry import SCALE
except ImportError:  # pragma: no cover - direct script support on PYNQ
    from geometry import SCALE


RGB_TO_YCBCR_MATRIX = np.array(
    [
        [0.256788235294118, 0.504129411764706, 0.097905882352941],
        [-0.148223529411765, -0.290992156862745, 0.439215686274510],
        [0.439215686274510, -0.367788235294118, -0.071427450980392],
    ],
    dtype=np.float64,
)
Y_COEFFICIENTS = RGB_TO_YCBCR_MATRIX[0].copy()
Y_OFFSET = 16.0
PSNR_CROP_BORDER = 4
SSIM_WINDOW_SIZE = 11
SSIM_SIGMA = 1.5


def nearest_x4(rgb: np.ndarray) -> np.ndarray:
    """Return an x4 pixel-replication baseline for one RGB ROI."""

    source = np.asarray(rgb)
    if source.ndim != 3 or source.shape[2] != 3 or source.dtype != np.uint8:
        raise ValueError("ROI must be an HxWx3 uint8 RGB array")
    return np.ascontiguousarray(
        np.repeat(
            np.repeat(source, SCALE, axis=0),
            SCALE,
            axis=1,
        )
    )


def nearest_scaled(rgb: np.ndarray, scale: int) -> np.ndarray:
    """Return a pixel-replication baseline at one supported native scale."""

    if scale not in (2, 4):
        raise ValueError("scale must be 2 or 4")
    source = np.asarray(rgb)
    if source.ndim != 3 or source.shape[2] != 3 or source.dtype != np.uint8:
        raise ValueError("ROI must be an HxWx3 uint8 RGB array")
    return np.ascontiguousarray(
        np.repeat(np.repeat(source, scale, axis=0), scale, axis=1)
    )


def crop_hr_roi(
    hr_rgb: np.ndarray,
    x: int,
    y: int,
    width: int,
    height: int,
    scale: int = SCALE,
) -> np.ndarray:
    """Map an LR ROI to the aligned x4 HR crop."""

    hr = np.asarray(hr_rgb)
    if scale not in (2, 4):
        raise ValueError("scale must be 2 or 4")
    left = int(x) * SCALE
    top = int(y) * SCALE
    right = (int(x) + int(width)) * SCALE
    bottom = (int(y) + int(height)) * SCALE
    crop = np.ascontiguousarray(hr[top:bottom, left:right])
    if scale != SCALE:
        if cv2 is not None:
            crop = cv2.resize(
                crop,
                (int(width) * scale, int(height) * scale),
                interpolation=cv2.INTER_AREA,
            )
        else:
            crop = crop[:: SCALE // scale, :: SCALE // scale]
        crop = np.ascontiguousarray(crop)
    expected = (int(height) * scale, int(width) * scale, 3)
    if crop.shape != expected:
        raise ValueError(f"HR crop has shape {crop.shape}, expected {expected}")
    return crop


def _y_plane(rgb: np.ndarray) -> np.ndarray:
    array = np.asarray(rgb)
    if array.ndim != 3 or array.shape[2] != 3:
        raise ValueError("image must have shape HxWx3")
    red = array[..., 0].astype(np.float64)
    result = red * Y_COEFFICIENTS[0]
    result += array[..., 1] * Y_COEFFICIENTS[1]
    result += array[..., 2] * Y_COEFFICIENTS[2]
    result += Y_OFFSET
    return result


def _calculate_psnr_planes(
    reference_y: np.ndarray,
    output_y: np.ndarray,
    crop_border: int,
) -> Optional[float]:
    height, width = reference_y.shape[:2]
    if crop_border * 2 >= height or crop_border * 2 >= width:
        return None
    difference = (
        np.asarray(output_y, dtype=np.float32)
        - np.asarray(reference_y, dtype=np.float32)
    )
    if crop_border:
        difference = difference[crop_border:-crop_border, crop_border:-crop_border]
    rmse = np.sqrt(np.mean(np.power(difference, 2)))
    if rmse == 0.0:
        return float("inf")
    return float(20.0 * np.log10(255.0 / rmse))


def calculate_psnr_y(
    reference_rgb: np.ndarray,
    output_rgb: np.ndarray,
    crop_border: int = PSNR_CROP_BORDER,
) -> Optional[float]:
    """Return project-contract PSNR-Y, or None when the ROI is too small."""

    if reference_rgb.shape != output_rgb.shape:
        raise ValueError("reference and output shapes differ")
    return _calculate_psnr_planes(
        _y_plane(reference_rgb),
        _y_plane(output_rgb),
        crop_border,
    )


def calculate_psnr_rgb(
    reference_rgb: np.ndarray,
    output_rgb: np.ndarray,
    crop_border: int = PSNR_CROP_BORDER,
) -> Optional[float]:
    """Return PSNR over all RGB samples using the project crop contract."""

    reference, output = _validate_rgb_pair(reference_rgb, output_rgb)
    return _calculate_psnr_planes(reference, output, crop_border)


def _gaussian_kernel() -> np.ndarray:
    radius = SSIM_WINDOW_SIZE // 2
    axis = np.arange(-radius, radius + 1, dtype=np.float64)
    kernel = np.exp(-(axis * axis) / (2.0 * SSIM_SIGMA * SSIM_SIGMA))
    return kernel / np.sum(kernel)


def _convolve_valid_axis(image: np.ndarray, kernel: np.ndarray, axis: int) -> np.ndarray:
    length = image.shape[axis] - kernel.size + 1
    if length <= 0:
        raise ValueError("convolution kernel is larger than the image")
    output_shape = list(image.shape)
    output_shape[axis] = length
    output = np.zeros(output_shape, dtype=np.float64)
    slices = [slice(None)] * image.ndim
    for index, weight in enumerate(kernel):
        slices[axis] = slice(index, index + length)
        output += image[tuple(slices)] * weight
    return output


def _gaussian_valid(image: np.ndarray) -> np.ndarray:
    kernel = _gaussian_kernel()
    if cv2 is not None:
        source = np.ascontiguousarray(image, dtype=np.float64)
        filtered = cv2.sepFilter2D(
            source,
            cv2.CV_64F,
            kernel,
            kernel,
            borderType=cv2.BORDER_CONSTANT,
        )
        radius = kernel.size // 2
        return filtered[radius:-radius, radius:-radius]
    return _convolve_valid_axis(
        _convolve_valid_axis(image, kernel, axis=0),
        kernel,
        axis=1,
    )


def _ssim_reference(reference_y: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    reference = np.asarray(reference_y, dtype=np.float64)
    mu_reference = _gaussian_valid(reference)
    mu_reference_sq = mu_reference * mu_reference
    sigma_reference_sq = _gaussian_valid(reference * reference) - mu_reference_sq
    return mu_reference, mu_reference_sq, sigma_reference_sq


def _calculate_ssim_planes(
    reference_y: np.ndarray,
    output_y: np.ndarray,
    reference_stats: Optional[tuple[np.ndarray, np.ndarray, np.ndarray]] = None,
) -> Optional[float]:
    reference = np.asarray(reference_y, dtype=np.float64)
    output = np.asarray(output_y, dtype=np.float64)
    if reference.shape != output.shape:
        raise ValueError("reference and output shapes differ")
    if min(reference.shape) < SSIM_WINDOW_SIZE:
        return None
    if reference_stats is None:
        reference_stats = _ssim_reference(reference)
    mu_reference, mu_reference_sq, sigma_reference_sq = reference_stats
    mu_output = _gaussian_valid(output)
    mu_output_sq = mu_output * mu_output
    mu_product = mu_reference * mu_output
    sigma_output_sq = _gaussian_valid(output * output) - mu_output_sq
    sigma_cross = _gaussian_valid(reference * output) - mu_product
    c1 = (0.01 * 255.0) ** 2
    c2 = (0.03 * 255.0) ** 2
    score = (
        (2.0 * mu_product + c1) * (2.0 * sigma_cross + c2)
    ) / (
        (mu_reference_sq + mu_output_sq + c1)
        * (sigma_reference_sq + sigma_output_sq + c2)
    )
    return float(np.mean(score))


def calculate_ssim_y(
    reference_rgb: np.ndarray,
    output_rgb: np.ndarray,
) -> Optional[float]:
    """Return the original LeRF Y-channel SSIM, or None for tiny ROIs."""

    if reference_rgb.shape != output_rgb.shape:
        raise ValueError("reference and output shapes differ")
    return _calculate_ssim_planes(_y_plane(reference_rgb), _y_plane(output_rgb))


def calculate_ssim_rgb(
    reference_rgb: np.ndarray,
    output_rgb: np.ndarray,
) -> Optional[float]:
    """Return the arithmetic mean of per-channel RGB SSIM scores."""

    reference, output = _validate_rgb_pair(reference_rgb, output_rgb)
    return _calculate_ssim_rgb(reference, output)


def _calculate_ssim_rgb(
    reference: np.ndarray,
    output: np.ndarray,
    reference_stats: Optional[
        tuple[tuple[np.ndarray, np.ndarray, np.ndarray], ...]
    ] = None,
) -> Optional[float]:
    if min(reference.shape[:2]) < SSIM_WINDOW_SIZE:
        return None
    if reference_stats is None:
        reference_stats = tuple(
            _ssim_reference(reference[..., channel])
            for channel in range(3)
        )
    scores = [
        _calculate_ssim_planes(
            reference[..., channel],
            output[..., channel],
            reference_stats[channel],
        )
        for channel in range(3)
    ]
    if any(score is None for score in scores):
        return None
    return float(np.mean(scores))


def _validate_rgb_pair(
    reference_rgb: np.ndarray,
    output_rgb: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    reference = np.asarray(reference_rgb)
    output = np.asarray(output_rgb)
    if reference.shape != output.shape:
        raise ValueError("reference and output shapes differ")
    if reference.ndim != 3 or reference.shape[2] != 3:
        raise ValueError("images must have shape HxWx3")
    return reference, output


def _delta(value: Optional[float], baseline: Optional[float]) -> Optional[float]:
    if value is None or baseline is None:
        return None
    if math.isinf(value) and math.isinf(baseline):
        return 0.0
    return float(value - baseline)


def compare_against_hr(
    hr_rgb: np.ndarray,
    fpga_rgb: np.ndarray,
    nearest_rgb: np.ndarray,
) -> Mapping[str, Any]:
    """Compare FPGA and nearest-neighbor ROI outputs against one HR target."""

    hr, fpga = _validate_rgb_pair(hr_rgb, fpga_rgb)
    nearest = np.asarray(nearest_rgb)
    if nearest.shape != hr.shape or nearest.ndim != 3 or nearest.shape[2] != 3:
        raise ValueError("reference and output shapes differ")
    reference_y = _y_plane(hr_rgb)
    fpga_y = _y_plane(fpga_rgb)
    nearest_y = _y_plane(nearest_rgb)
    fpga_psnr = _calculate_psnr_planes(reference_y, fpga_y, PSNR_CROP_BORDER)
    nearest_psnr = _calculate_psnr_planes(reference_y, nearest_y, PSNR_CROP_BORDER)
    fpga_psnr_rgb = calculate_psnr_rgb(hr, fpga)
    nearest_psnr_rgb = calculate_psnr_rgb(hr, nearest)
    reference_stats = (
        _ssim_reference(reference_y)
        if min(reference_y.shape) >= SSIM_WINDOW_SIZE
        else None
    )
    reference_stats_rgb = (
        tuple(
            _ssim_reference(hr[..., channel])
            for channel in range(3)
        )
        if min(hr.shape[:2]) >= SSIM_WINDOW_SIZE
        else None
    )
    fpga_ssim = _calculate_ssim_planes(reference_y, fpga_y, reference_stats)
    nearest_ssim = _calculate_ssim_planes(reference_y, nearest_y, reference_stats)
    fpga_ssim_rgb = _calculate_ssim_rgb(hr, fpga, reference_stats_rgb)
    nearest_ssim_rgb = _calculate_ssim_rgb(hr, nearest, reference_stats_rgb)
    psnr_delta = _delta(fpga_psnr, nearest_psnr)
    ssim_delta = _delta(fpga_ssim, nearest_ssim)
    psnr_rgb_delta = _delta(fpga_psnr_rgb, nearest_psnr_rgb)
    ssim_rgb_delta = _delta(fpga_ssim_rgb, nearest_ssim_rgb)

    return {
        "reference_type": "hr_ground_truth",
        "contract": {
            "psnr": "Y channel, four-pixel border crop",
            "ssim": "Y channel, LeRF 11x11 Gaussian window, sigma 1.5",
            "psnr_rgb": "all RGB samples, four-pixel border crop",
            "ssim_rgb": "mean of R/G/B SSIM, LeRF 11x11 Gaussian window, sigma 1.5",
            "baseline": "nearest-neighbor x4 pixel replication",
        },
        "fpga_vs_hr": {
            "psnr_y_db": fpga_psnr,
            "ssim_y": fpga_ssim,
            "psnr_rgb_db": fpga_psnr_rgb,
            "ssim_rgb": fpga_ssim_rgb,
        },
        "nearest_vs_hr": {
            "psnr_y_db": nearest_psnr,
            "ssim_y": nearest_ssim,
            "psnr_rgb_db": nearest_psnr_rgb,
            "ssim_rgb": nearest_ssim_rgb,
        },
        "fpga_minus_nearest": {
            "psnr_y_db": psnr_delta,
            "ssim_y": ssim_delta,
            "psnr_rgb_db": psnr_rgb_delta,
            "ssim_rgb": ssim_rgb_delta,
        },
    }


def compare_against_input(
    input_baseline_rgb: np.ndarray,
    fpga_rgb: np.ndarray,
    scale: int = SCALE,
) -> Mapping[str, Any]:
    """Compare one FPGA output with its scale-aligned input baseline."""

    if scale not in (2, 4):
        raise ValueError("scale must be 2 or 4")
    input_baseline, fpga = _validate_rgb_pair(input_baseline_rgb, fpga_rgb)
    return {
        "reference_type": f"input_nearest_x{scale}",
        "scale": scale,
        "contract": {
            "psnr": "Y channel, four-pixel border crop",
            "ssim": "Y channel, LeRF 11x11 Gaussian window, sigma 1.5",
            "psnr_rgb": "all RGB samples, four-pixel border crop",
            "ssim_rgb": "mean of R/G/B SSIM, LeRF 11x11 Gaussian window, sigma 1.5",
            "baseline": f"input ROI aligned by nearest-neighbor x{scale} pixel replication",
            "interpretation": (
                "output change and fidelity relative to the input baseline; "
                "not an objective image-quality improvement score"
            ),
        },
        "fpga_vs_input": {
            "psnr_y_db": calculate_psnr_y(input_baseline, fpga),
            "ssim_y": calculate_ssim_y(input_baseline, fpga),
            "psnr_rgb_db": calculate_psnr_rgb(input_baseline, fpga),
            "ssim_rgb": calculate_ssim_rgb(input_baseline, fpga),
        },
    }


__all__ = [
    "calculate_psnr_y",
    "calculate_psnr_rgb",
    "calculate_ssim_y",
    "calculate_ssim_rgb",
    "compare_against_input",
    "compare_against_hr",
    "crop_hr_roi",
    "nearest_scaled",
    "nearest_x4",
]
