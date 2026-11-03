"""Serialized FPGA and explicitly non-formal preview execution for ROI x4."""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
import threading
import time
import os
import inspect
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

import numpy as np
from PIL import Image

try:
    from .geometry import RoiPlan
    from .geometry import SCALE
    from .geometry import plan_roi
except ImportError:  # pragma: no cover - direct script support on PYNQ
    from geometry import RoiPlan
    from geometry import SCALE
    from geometry import plan_roi


MAX_SOURCE_WIDTH = 4096
MAX_SOURCE_HEIGHT = 4096
MAX_SOURCE_PIXELS = 16_777_216


@dataclass(frozen=True)
class RoiExecution:
    image: np.ndarray
    plan: RoiPlan
    metrics: Mapping[str, Any]


def validate_source_image(rgb: np.ndarray) -> tuple[int, int]:
    array = np.asarray(rgb)
    if array.ndim != 3 or array.shape[2] != 3 or array.dtype != np.uint8:
        raise ValueError("source image must be an HxWx3 uint8 RGB array")
    height, width = array.shape[:2]
    if height < 32 or width < 32:
        raise ValueError("source image must be at least 32x32")
    if height > MAX_SOURCE_HEIGHT or width > MAX_SOURCE_WIDTH:
        raise ValueError("source image dimensions must not exceed 4096x4096")
    if height * width > MAX_SOURCE_PIXELS:
        raise ValueError("source image exceeds the 16-megapixel safety limit")
    return height, width


class RoiProcessor:
    """Run a planned ROI through one shared overlay instance."""

    def __init__(
        self,
        *,
        preview: bool = False,
        runner: Optional[Callable[[np.ndarray, Any], Any]] = None,
        overlay_loader: Optional[Callable[[], Any]] = None,
        overlay: Any = None,
        clock: Callable[[], float] = time.perf_counter,
        default_scale: int = SCALE,
        hardware_lock: Optional[threading.RLock] = None,
        reconfiguration_lock: Optional[threading.RLock] = None,
    ) -> None:
        self.preview = bool(preview)
        self._runner = runner
        self._overlay_loader = overlay_loader
        self._overlay = overlay
        self._overlays: dict[int, Any] = {}
        self._active_scale: Optional[int] = 4 if overlay is not None else None
        self._clock = clock
        self._lock = hardware_lock or threading.RLock()
        self._reconfiguration_lock = reconfiguration_lock or threading.RLock()
        if default_scale not in (2, 4):
            raise ValueError("default_scale must be 2 or 4")
        self.default_scale = int(default_scale)

    @property
    def mode(self) -> str:
        return "software_preview" if self.preview else "fpga"

    def _load_hardware(self, scale: int) -> tuple[Callable[[np.ndarray, Any], Any], Any]:
        if self._runner is None or (
            self._overlay_loader is None
            and (self._overlay is None or self._active_scale != scale)
        ):
            from sr_lut_x4_driver import load_overlay
            from sr_lut_x4_driver import run_frame

            if self._runner is None:
                self._runner = run_frame
            if self._overlay_loader is None:
                self._overlay_loader = load_overlay
        if scale == 4 and self._overlay is not None and self._active_scale == 4:
            self._overlays[4] = self._overlay
        # PYNQ invalidates every previous Overlay object when a new bitstream is
        # downloaded.  A scale switch must therefore reload even if that scale
        # was used earlier in this process.
        if self._active_scale != scale or scale not in self._overlays:
            assert self._overlay_loader is not None
            # GPIO remains available during normal DMA work, but must pause
            # while a bitstream download temporarily removes the AXI fabric.
            with self._reconfiguration_lock:
                if scale == 2:
                    overlay_dir = os.environ.get("SR_LUT_X2_OVERLAY_DIR")
                    if not overlay_dir:
                        raise RuntimeError(
                            "x2 Overlay is unavailable; set SR_LUT_X2_OVERLAY_DIR"
                        )
                    self._overlays[2] = self._overlay_loader(
                        overlay_dir=Path(overlay_dir), overlay_stem="sr_lut_x2"
                    )
                else:
                    overlay_dir = os.environ.get("SR_LUT_X4_OVERLAY_DIR")
                    if overlay_dir:
                        self._overlays[4] = self._overlay_loader(
                            overlay_dir=Path(overlay_dir), overlay_stem="sr_lut_x4"
                        )
                    else:
                        # Keep the packaged formal x4 media Overlay as the default.
                        self._overlays[4] = self._overlay_loader()
            self._active_scale = scale
            self._overlays = {scale: self._overlays[scale]}
        active_overlay = self._overlays[scale]
        assert self._runner is not None
        return self._runner, active_overlay

    def prepare(self, scale: Optional[int] = None) -> None:
        """Create the shared overlay on the caller thread before serving requests."""

        if self.preview:
            return
        with self._lock:
            self._load_hardware(
                self.default_scale if scale is None else int(scale)
            )

    def invalidate_hardware(self) -> None:
        """Discard PYNQ objects invalidated by another bitstream download."""

        if self.preview:
            return
        with self._lock:
            self._overlay = None
            self._overlays.clear()
            self._active_scale = None

    @contextmanager
    def hardware_session(self, scale: Optional[int] = None):
        """Reserve the shared FPGA overlay for one complete image or video operation."""

        with self._lock:
            if self.preview:
                yield None
            else:
                _, overlay = self._load_hardware(
                    self.default_scale if scale is None else int(scale)
                )
                yield overlay

    @staticmethod
    def _preview_x4(window: np.ndarray) -> np.ndarray:
        resampling = getattr(Image, "Resampling", Image)
        image = Image.fromarray(window, mode="RGB")
        enlarged = image.resize(
            (window.shape[1] * SCALE, window.shape[0] * SCALE),
            resample=resampling.BICUBIC,
        )
        return np.asarray(enlarged, dtype=np.uint8).copy()

    def process(
        self,
        source_rgb: np.ndarray,
        x: int,
        y: int,
        width: int,
        height: int,
        scale: Optional[int] = None,
    ) -> RoiExecution:
        source = np.asarray(source_rgb)
        source_height, source_width = validate_source_image(source)
        active_scale = self.default_scale if scale is None else int(scale)
        if active_scale not in (2, 4):
            raise ValueError("scale must be 2 or 4")
        plan = plan_roi(source_width, source_height, x, y, width, height, active_scale)
        window = np.ascontiguousarray(
            source[
                plan.process_window.y : plan.process_window.bottom,
                plan.process_window.x : plan.process_window.right,
            ]
        )

        started = self._clock()
        driver_metrics = None
        hardware_metrics = None
        with self._lock:
            if self.preview:
                full_output = self._preview_x4(window) if active_scale == 4 else np.asarray(
                    Image.fromarray(window, mode="RGB").resize(
                        (window.shape[1] * 2, window.shape[0] * 2),
                        resample=getattr(Image, "Resampling", Image).BICUBIC,
                    ), dtype=np.uint8
                )
            else:
                runner, overlay = self._load_hardware(active_scale)
                # Keep compatibility with the original two-argument test and
                # board runners while passing the selected scale to dynamic
                # drivers that expose it.
                try:
                    parameters = inspect.signature(runner).parameters
                    accepts_scale = "scale" in parameters or any(
                        parameter.kind is inspect.Parameter.VAR_KEYWORD
                        for parameter in parameters.values()
                    )
                except (TypeError, ValueError):
                    accepts_scale = True
                frame_result = (
                    runner(window, overlay, scale=active_scale)
                    if accepts_scale
                    else runner(window, overlay)
                )
                full_output = np.asarray(frame_result.image, dtype=np.uint8)
                if getattr(frame_result, "metrics", None) is not None:
                    driver_metrics = dict(frame_result.metrics.as_dict())
                if getattr(frame_result, "hardware_metrics", None) is not None:
                    hardware_metrics = dict(frame_result.hardware_metrics.as_dict())

        expected_shape = (
            plan.process_window.height * active_scale,
            plan.process_window.width * active_scale,
            3,
        )
        if full_output.shape != expected_shape:
            raise RuntimeError(
                f"x4 processor returned {full_output.shape}, expected {expected_shape}"
            )
        crop = plan.output_crop
        result = np.ascontiguousarray(
            full_output[crop.y : crop.bottom, crop.x : crop.right]
        )
        finished = self._clock()
        metrics = {
            "execution_mode": self.mode,
            "formal_fpga_result": not self.preview,
            "host_pipeline_seconds": finished - started,
            "driver": driver_metrics,
            "pl": hardware_metrics,
        }
        return RoiExecution(image=result, plan=plan, metrics=metrics)


__all__ = [
    "MAX_SOURCE_HEIGHT",
    "MAX_SOURCE_PIXELS",
    "MAX_SOURCE_WIDTH",
    "RoiExecution",
    "RoiProcessor",
    "validate_source_image",
]
