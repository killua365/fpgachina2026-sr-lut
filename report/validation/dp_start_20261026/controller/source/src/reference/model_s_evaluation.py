import csv
import hashlib
from pathlib import Path

import numpy as np
import cv2
from scipy import signal

from model_s_input import load_rgb_u8
from model_s_interpolator import LUT_SPECS
from model_s_interpolator import file_sha256
from model_s_interpolator import load_model_s_lut
from model_s_output import run_model_s
from model_s_output import save_rgb_u8


IMAGE_NAMES = (
    "baby",
    "bird",
    "butterfly",
    "head",
    "woman",
)

CONFIGURATIONS = (
    {
        "name": "x2_4bit",
        "scale": 2,
        "sampling_bits": 4,
        "lr_directory": "lr_x2",
    },
    {
        "name": "x4_4bit",
        "scale": 4,
        "sampling_bits": 4,
        "lr_directory": "lr_x4",
    },
    {
        "name": "x4_5bit",
        "scale": 4,
        "sampling_bits": 5,
        "lr_directory": "lr_x4",
    },
)

EXPECTED_AVERAGE_PSNR = {
    "x2_4bit": 35.510184141890178,
    "x4_4bit": 29.831005260923369,
    "x4_5bit": 29.631128648960662,
}

EXPECTED_AVERAGE_SSIM = {
    "x2_4bit": 0.947677078100983,
    "x4_4bit": 0.848360007156998,
    "x4_5bit": 0.842479563925467,
}

RGB_TO_YCBCR_MATRIX = np.array(
    [
        [
            0.256788235294118,
            0.504129411764706,
            0.097905882352941,
        ],
        [
            -0.148223529411765,
            -0.290992156862745,
            0.439215686274510,
        ],
        [
            0.439215686274510,
            -0.367788235294118,
            -0.071427450980392,
        ],
    ]
)

YCBCR_OFFSET = np.array(
    [[16], [128], [128]]
)


def rgb_to_ycbcr_original(image):
    if not isinstance(image, np.ndarray):
        raise TypeError("image must be a NumPy array")

    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError(
            "image must have shape (H, W, 3)"
        )

    flattened = np.reshape(
        image,
        (
            image.shape[0] * image.shape[1],
            image.shape[2],
        ),
    )

    converted = np.dot(
        flattened,
        np.transpose(RGB_TO_YCBCR_MATRIX),
    )

    converted[:, 0] += YCBCR_OFFSET[0]
    converted[:, 1] += YCBCR_OFFSET[1]
    converted[:, 2] += YCBCR_OFFSET[2]

    return np.reshape(converted, image.shape)


def calculate_psnr_from_y(
    reference_y,
    output_y,
    crop_border=4,
):
    if not isinstance(reference_y, np.ndarray):
        raise TypeError(
            "reference_y must be a NumPy array"
        )

    if not isinstance(output_y, np.ndarray):
        raise TypeError(
            "output_y must be a NumPy array"
        )

    if reference_y.shape != output_y.shape:
        raise ValueError(
            "reference and output must have equal shapes"
        )

    if reference_y.ndim != 2:
        raise ValueError(
            "Y channel arrays must be two-dimensional"
        )

    if (
        isinstance(crop_border, bool)
        or not isinstance(crop_border, int)
    ):
        raise TypeError(
            "crop_border must be an integer"
        )

    if crop_border < 0:
        raise ValueError(
            "crop_border must not be negative"
        )

    if (
        crop_border * 2 >= reference_y.shape[0]
        or crop_border * 2 >= reference_y.shape[1]
    ):
        raise ValueError(
            "crop border is too large for the image"
        )

    reference = np.array(
        reference_y,
        dtype=np.float32,
    )

    output = np.array(
        output_y,
        dtype=np.float32,
    )

    difference = output - reference

    if crop_border > 0:
        difference = difference[
            crop_border:-crop_border,
            crop_border:-crop_border,
        ]

    rmse = np.sqrt(
        np.mean(np.power(difference, 2))
    )

    if rmse == 0:
        return float("inf")

    return float(
        20 * np.log10(255.0 / rmse)
    )


def calculate_psnr_y(
    reference_rgb,
    output_rgb,
    crop_border=4,
):
    if reference_rgb.shape != output_rgb.shape:
        raise ValueError(
            "reference and output RGB shapes differ"
        )

    reference_y = rgb_to_ycbcr_original(
        reference_rgb
    )[:, :, 0]

    output_y = rgb_to_ycbcr_original(
        output_rgb
    )[:, :, 0]

    return calculate_psnr_from_y(
        reference_y,
        output_y,
        crop_border,
    )


def calculate_psnr_rgb(
    reference_rgb,
    output_rgb,
    crop_border=4,
):
    """Return PSNR over all R/G/B samples using the same crop contract."""

    if reference_rgb.shape != output_rgb.shape:
        raise ValueError("reference and output RGB shapes differ")
    if reference_rgb.ndim != 3 or reference_rgb.shape[2] != 3:
        raise ValueError("RGB images must have shape (H, W, 3)")
    if (
        isinstance(crop_border, bool)
        or not isinstance(crop_border, int)
    ):
        raise TypeError("crop_border must be an integer")
    if crop_border < 0:
        raise ValueError("crop_border must not be negative")
    if (
        crop_border * 2 >= reference_rgb.shape[0]
        or crop_border * 2 >= reference_rgb.shape[1]
    ):
        raise ValueError("crop border is too large for the image")

    difference = (
        np.asarray(output_rgb, dtype=np.float32)
        - np.asarray(reference_rgb, dtype=np.float32)
    )
    if crop_border > 0:
        difference = difference[
            crop_border:-crop_border,
            crop_border:-crop_border,
            :,
        ]
    rmse = np.sqrt(np.mean(np.power(difference, 2)))
    if rmse == 0:
        return float("inf")
    return float(20 * np.log10(255.0 / rmse))


def calculate_ssim_from_y(reference_y, output_y):
    """Return the original LeRF single-scale SSIM for two Y planes."""

    if not isinstance(reference_y, np.ndarray):
        raise TypeError("reference_y must be a NumPy array")
    if not isinstance(output_y, np.ndarray):
        raise TypeError("output_y must be a NumPy array")
    if reference_y.shape != output_y.shape:
        raise ValueError("reference and output Y shapes differ")
    if reference_y.ndim != 2:
        raise ValueError("Y channel arrays must be two-dimensional")
    if reference_y.shape[0] < 11 or reference_y.shape[1] < 11:
        raise ValueError("SSIM requires Y planes of at least 11x11")

    kernel = cv2.getGaussianKernel(11, 1.5)
    window = kernel * kernel.T
    reference = np.asarray(reference_y, dtype=np.float64)
    output = np.asarray(output_y, dtype=np.float64)

    mu_reference = signal.convolve2d(reference, window, mode="valid")
    mu_output = signal.convolve2d(output, window, mode="valid")
    mu_reference_sq = mu_reference * mu_reference
    mu_output_sq = mu_output * mu_output
    mu_product = mu_reference * mu_output

    sigma_reference_sq = (
        signal.convolve2d(reference * reference, window, mode="valid")
        - mu_reference_sq
    )
    sigma_output_sq = (
        signal.convolve2d(output * output, window, mode="valid")
        - mu_output_sq
    )
    sigma_cross = (
        signal.convolve2d(reference * output, window, mode="valid")
        - mu_product
    )

    c1 = (0.01 * 255.0) ** 2
    c2 = (0.03 * 255.0) ** 2
    ssim_map = (
        (2.0 * mu_product + c1) * (2.0 * sigma_cross + c2)
    ) / (
        (mu_reference_sq + mu_output_sq + c1)
        * (sigma_reference_sq + sigma_output_sq + c2)
    )
    return float(np.mean(ssim_map))


def calculate_ssim_y(reference_rgb, output_rgb):
    if reference_rgb.shape != output_rgb.shape:
        raise ValueError("reference and output RGB shapes differ")
    reference_y = rgb_to_ycbcr_original(reference_rgb)[:, :, 0]
    output_y = rgb_to_ycbcr_original(output_rgb)[:, :, 0]
    return calculate_ssim_from_y(reference_y, output_y)


def calculate_ssim_rgb(reference_rgb, output_rgb):
    """Return the arithmetic mean of per-channel RGB SSIM scores."""

    if reference_rgb.shape != output_rgb.shape:
        raise ValueError("reference and output RGB shapes differ")
    if reference_rgb.ndim != 3 or reference_rgb.shape[2] != 3:
        raise ValueError("RGB images must have shape (H, W, 3)")
    values = [
        calculate_ssim_from_y(reference_rgb[..., channel], output_rgb[..., channel])
        for channel in range(3)
    ]
    return float(np.mean(values))


def raw_rgb_sha256(image):
    return hashlib.sha256(
        image.tobytes(order="C")
    ).hexdigest().upper()


def input_filename(configuration, image_name):
    if configuration["scale"] == 2:
        return f"{image_name}_gt.png"

    return f"{image_name}.png"


def evaluate(output_root=None):
    project_root = Path(__file__).resolve().parents[2]

    if output_root is None:
        output_root = (
            project_root
            / "build"
            / "reference_evaluation"
        )
    else:
        output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    image_root = (
        project_root
        / "data/golden"
        / "source_images"
    )

    lut_root = (
        project_root
        / "data/lut"
        / "original_npy"
    )

    existing_golden_root = (
        project_root
        / "data/golden"
        / "reference_baselines"
        / "x4_4bit"
    )

    result_root = output_root / "golden"

    result_root.mkdir(
        parents=True,
        exist_ok=True,
    )

    records = []
    averages = {}
    average_ssim_values = {}
    average_psnr_rgb_values = {}
    average_ssim_rgb_values = {}

    for configuration in CONFIGURATIONS:
        name = configuration["name"]
        scale = configuration["scale"]
        sampling_bits = configuration[
            "sampling_bits"
        ]

        spec = LUT_SPECS[
            (scale, sampling_bits)
        ]

        lut = load_model_s_lut(
            lut_root / spec["filename"],
            scale,
            sampling_bits,
        )

        output_directory = result_root / name
        output_directory.mkdir(
            parents=True,
            exist_ok=True,
        )

        configuration_psnr = []
        configuration_ssim = []
        configuration_psnr_rgb = []
        configuration_ssim_rgb = []

        print(f"configuration={name}")

        for image_name in IMAGE_NAMES:
            lr_path = (
                image_root
                / configuration["lr_directory"]
                / input_filename(
                    configuration,
                    image_name,
                )
            )

            hr_path = (
                image_root
                / "hr"
                / f"{image_name}_gt.png"
            )

            lr_image = load_rgb_u8(lr_path)
            hr_image = load_rgb_u8(hr_path)

            output = run_model_s(
                lr_image,
                lut,
                scale,
                sampling_bits,
            )

            if output.shape != hr_image.shape:
                raise AssertionError(
                    f"shape mismatch for {name}/{image_name}: "
                    f"output={output.shape}, "
                    f"HR={hr_image.shape}"
                )

            output_path = (
                output_directory
                / f"{image_name}.png"
            )

            save_rgb_u8(
                output,
                output_path,
            )

            psnr_value = calculate_psnr_y(
                hr_image,
                output,
                crop_border=4,
            )

            configuration_psnr.append(
                psnr_value
            )

            ssim_value = calculate_ssim_y(
                hr_image,
                output,
            )

            configuration_ssim.append(
                ssim_value
            )

            psnr_rgb_value = calculate_psnr_rgb(
                hr_image,
                output,
                crop_border=4,
            )
            configuration_psnr_rgb.append(
                psnr_rgb_value
            )

            ssim_rgb_value = calculate_ssim_rgb(
                hr_image,
                output,
            )
            configuration_ssim_rgb.append(
                ssim_rgb_value
            )

            existing_equal = "not_available"
            mismatch_channels = "not_available"
            max_channel_diff = "not_available"

            if name == "x4_4bit":
                existing_path = (
                    existing_golden_root
                    / (
                        f"{image_name}_gt_"
                        "lut_interp_4bit.png"
                    )
                )

                existing = load_rgb_u8(
                    existing_path
                )

                difference = np.abs(
                    output.astype(np.int16)
                    - existing.astype(np.int16)
                )

                existing_equal = str(
                    np.array_equal(
                        output,
                        existing,
                    )
                )

                mismatch_channels = str(
                    int(np.count_nonzero(difference))
                )

                max_channel_diff = str(
                    int(difference.max())
                )

                if existing_equal != "True":
                    raise AssertionError(
                        "x4 4bit existing output mismatch: "
                        f"{image_name}"
                    )

            record = {
                "configuration": name,
                "image": image_name,
                "input_height": lr_image.shape[0],
                "input_width": lr_image.shape[1],
                "output_height": output.shape[0],
                "output_width": output.shape[1],
                "psnr_y_db": f"{psnr_value:.15f}",
                "ssim_y": f"{ssim_value:.15f}",
                "psnr_rgb_db": f"{psnr_rgb_value:.15f}",
                "ssim_rgb": f"{ssim_rgb_value:.15f}",
                "raw_rgb_sha256": raw_rgb_sha256(
                    output
                ),
                "png_sha256": file_sha256(
                    output_path
                ),
                "existing_golden_equal": (
                    existing_equal
                ),
                "mismatch_channels": (
                    mismatch_channels
                ),
                "max_channel_diff": (
                    max_channel_diff
                ),
            }

            records.append(record)

            print(
                f"  {image_name}: "
                f"PSNR_Y={psnr_value:.15f} dB, "
                f"SSIM_Y={ssim_value:.15f}, "
                f"PSNR_RGB={psnr_rgb_value:.15f} dB, "
                f"SSIM_RGB={ssim_rgb_value:.15f}, "
                f"RGB_SHA256={record['raw_rgb_sha256']}"
            )

        average = float(
            np.mean(
                np.asarray(configuration_psnr)
            )
        )

        averages[name] = average

        average_ssim = float(
            np.mean(
                np.asarray(configuration_ssim)
            )
        )
        average_ssim_values[name] = average_ssim

        average_psnr_rgb = float(
            np.mean(np.asarray(configuration_psnr_rgb))
        )
        average_ssim_rgb = float(
            np.mean(np.asarray(configuration_ssim_rgb))
        )
        average_psnr_rgb_values[name] = average_psnr_rgb
        average_ssim_rgb_values[name] = average_ssim_rgb

        expected_average = (
            EXPECTED_AVERAGE_PSNR[name]
        )

        if not np.isclose(
            average,
            expected_average,
            rtol=0.0,
            atol=1e-9,
        ):
            raise AssertionError(
                f"average PSNR mismatch for {name}: "
                f"actual={average}, "
                f"expected={expected_average}"
            )

        expected_average_ssim = EXPECTED_AVERAGE_SSIM[name]
        if not np.isclose(
            average_ssim,
            expected_average_ssim,
            rtol=0.0,
            atol=1e-12,
        ):
            raise AssertionError(
                f"average SSIM mismatch for {name}: "
                f"actual={average_ssim}, "
                f"expected={expected_average_ssim}"
            )

        print(
            f"  average_psnr={average:.15f} dB, "
            f"average_ssim={average_ssim:.15f}"
        )

    metrics_path = output_root / "set5_metrics.csv"

    with metrics_path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as destination:
        writer = csv.DictWriter(
            destination,
            fieldnames=list(records[0].keys()),
        )
        writer.writeheader()
        writer.writerows(records)

    report_path = output_root / "evaluation_report.md"

    report_lines = [
        "# Stage B Integer Reference Acceptance",
        "",
        "## Scope",
        "",
        "- Model-S x2 4bit",
        "- Model-S x4 4bit",
        "- Model-S x4 5bit",
        "- Set5 RGB outputs",
        "- Original Y-channel PSNR formula",
        "- Aggregate RGB PSNR and mean per-channel RGB SSIM",
        "- Four-pixel border crop",
        "",
        "## Average PSNR-Y",
        "",
        "| Configuration | Average PSNR-Y (dB) |",
        "|---|---:|",
    ]

    for configuration in CONFIGURATIONS:
        name = configuration["name"]
        report_lines.append(
            f"| {name} | {averages[name]:.15f} |"
        )

    report_lines.extend(
        [
            "",
            "## SSIM Contract",
            "",
            (
                "The CSV records Y-channel SSIM using the original LeRF "
                "11x11 Gaussian window with sigma 1.5. SSIM is a Python "
                "image-quality metric, not an FPGA performance metric."
            ),
            "",
            "| Configuration | Average SSIM-Y |",
            "|---|---:|",
        ]
    )

    for configuration in CONFIGURATIONS:
        name = configuration["name"]
        report_lines.append(
            f"| {name} | {average_ssim_values[name]:.15f} |"
        )

    report_lines.extend(
        [
            "",
            "## RGB Metrics",
            "",
            (
                "PSNR-RGB aggregates the MSE of all R/G/B samples after the "
                "four-pixel border crop. SSIM-RGB is the arithmetic mean of "
                "the three per-channel SSIM values."
            ),
            "",
            "| Configuration | Average PSNR-RGB (dB) | Average SSIM-RGB |",
            "|---|---:|---:|",
        ]
    )

    for configuration in CONFIGURATIONS:
        name = configuration["name"]
        report_lines.append(
            f"| {name} | {average_psnr_rgb_values[name]:.15f} | "
            f"{average_ssim_rgb_values[name]:.15f} |"
        )

    report_lines.extend(
        [
            "",
            "## Existing Output Comparison",
            "",
            (
                "All five x4 4bit outputs match the "
                "existing project outputs pixel-for-pixel."
            ),
            "",
            "- Mismatch channels: 0",
            "- Maximum channel difference: 0",
            "",
            "## HLS Fixed Vector Evidence",
            "",
            (
                "See `stage_03i_hls_x2_tests.txt` for "
                "the x2 4bit HLS fixed-vector test."
            ),
            "",
            "## Important Boundary",
            "",
            (
                "These are software reference results. "
                "They are not FPGA timing or board results."
            ),
            "",
        ]
    )

    report_path.write_text(
        "\n".join(report_lines),
        encoding="utf-8",
    )

    print(f"metrics={metrics_path}")
    print(f"report={report_path}")
    print(f"outputs={result_root}")
    print("STAGE B IMAGE EVALUATION PASS")
    return {
        "result_root": result_root,
        "metrics_path": metrics_path,
        "report_path": report_path,
    }


if __name__ == "__main__":
    evaluate()
