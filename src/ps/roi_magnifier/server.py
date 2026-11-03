"""Dependency-light PYNQ web server for the Stage 39 SR-LUT portal."""

from __future__ import annotations

import argparse
import base64
from collections import OrderedDict
from dataclasses import dataclass
from functools import partial
import hashlib
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from io import BytesIO
import json
import math
import mimetypes
import os
from pathlib import Path
import secrets
import sys
import threading
import time
import traceback
from typing import Any, Mapping, Optional, Sequence
from urllib.parse import unquote
from urllib.parse import parse_qs
from urllib.parse import urlsplit

import numpy as np
from PIL import Image
from PIL import ImageOps

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from roi_magnifier.processor import MAX_SOURCE_HEIGHT
    from roi_magnifier.processor import MAX_SOURCE_PIXELS
    from roi_magnifier.processor import MAX_SOURCE_WIDTH
    from roi_magnifier.processor import RoiProcessor
    from roi_magnifier.processor import validate_source_image
    from roi_magnifier.geometry import MAX_PROCESS_HEIGHT
    from roi_magnifier.geometry import MAX_PROCESS_WIDTH
    from roi_magnifier.geometry import MIN_PROCESS_HEIGHT
    from roi_magnifier.geometry import MIN_PROCESS_WIDTH
    from roi_magnifier.quality import compare_against_hr
    from roi_magnifier.quality import compare_against_input
    from roi_magnifier.quality import crop_hr_roi
    from roi_magnifier.quality import nearest_x4
    from roi_magnifier.quality import nearest_scaled
    from roi_magnifier.live_stream import LiveCameraService, LiveStreamBusy
    from roi_magnifier.live_stream import Stage38MjpegCameraService
    from roi_magnifier.mode_control import BoardModeGPIO, ButtonModeMonitor
    from roi_magnifier.mode_control import ModeController, ModeTransitionError
    from roi_magnifier.scale_control import BoardScaleGPIO, ScaleController
    from roi_magnifier.scale_control import ScaleLockedError, ScaleSwitchMonitor
else:
    from .processor import MAX_SOURCE_HEIGHT
    from .processor import MAX_SOURCE_PIXELS
    from .processor import MAX_SOURCE_WIDTH
    from .processor import RoiProcessor
    from .processor import validate_source_image
    from .geometry import MAX_PROCESS_HEIGHT
    from .geometry import MAX_PROCESS_WIDTH
    from .geometry import MIN_PROCESS_HEIGHT
    from .geometry import MIN_PROCESS_WIDTH
    from .quality import compare_against_hr
    from .quality import compare_against_input
    from .quality import crop_hr_roi
    from .quality import nearest_x4
    from .quality import nearest_scaled
    from .live_stream import LiveCameraService, LiveStreamBusy
    from .live_stream import Stage38MjpegCameraService
    from .mode_control import BoardModeGPIO, ButtonModeMonitor
    from .mode_control import ModeController, ModeTransitionError
    from .scale_control import BoardScaleGPIO, ScaleController
    from .scale_control import ScaleLockedError, ScaleSwitchMonitor

from media_jobs import APPLICATION_VERSION
from media_jobs import MAX_VIDEO_UPLOAD_BYTES
from media_jobs import VideoJobManager
from video_pipeline.identity import HARDWARE_STAGE
from video_pipeline.identity import require_overlay_identity


MAX_REQUEST_BYTES = 24 * 1024 * 1024
ALLOWED_IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/webp"}
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/mjpeg_stream.js": ("mjpeg_stream.js", "text/javascript; charset=utf-8"),
}
TEXT_SUFFIXES = {".csv", ".json", ".md", ".mem", ".rpt", ".sv", ".tcl", ".txt", ".v", ".xdc"}
ROI_RESULT_CACHE_ITEMS = 3


class ApiError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = int(status)


@dataclass(frozen=True)
class Sample:
    name: str
    path: Path
    hr_path: Path
    width: int
    height: int
    sha256: str
    hr_sha256: str

    def public(self) -> Mapping[str, Any]:
        return {
            "name": self.name,
            "width": self.width,
            "height": self.height,
            "sha256": self.sha256,
            "has_hr": True,
        }


@dataclass(frozen=True)
class SourceImage:
    name: str
    rgb: np.ndarray
    png_data_url: str
    sha256: str
    generation: int
    hr_rgb: Optional[np.ndarray] = None
    sample: bool = False

    def public(self, *, include_png: bool = False) -> dict[str, Any]:
        height, width = self.rgb.shape[:2]
        result = {
            "name": self.name,
            "width": width,
            "height": height,
            "sha256": self.sha256,
            "generation": self.generation,
            "has_hr": self.hr_rgb is not None,
            "sample": self.sample,
        }
        if include_png:
            result["png_data_url"] = self.png_data_url
        return result


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def encode_png_bytes(rgb: np.ndarray) -> bytes:
    output = BytesIO()
    # A low compression level keeps the live ROI path responsive while remaining lossless.
    Image.fromarray(rgb, mode="RGB").save(
        output,
        format="PNG",
        optimize=False,
        compress_level=1,
    )
    return output.getvalue()


def encode_png(rgb: np.ndarray) -> tuple[bytes, str]:
    content = encode_png_bytes(rgb)
    return content, "data:image/png;base64," + base64.b64encode(content).decode("ascii")


def strict_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: strict_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [strict_json(item) for item in value]
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        value = float(value)
    if isinstance(value, float) and not math.isfinite(value):
        return "Infinity" if value > 0 else "-Infinity"
    return value


def decode_image_data_url(data_url: str) -> np.ndarray:
    if not isinstance(data_url, str) or not data_url.startswith("data:"):
        raise ValueError("image must be supplied as a data URL")
    header, separator, encoded = data_url.partition(",")
    if not separator or ";base64" not in header:
        raise ValueError("image data URL must use base64 encoding")
    mime_type = header[5:].split(";", 1)[0].lower()
    if mime_type not in ALLOWED_IMAGE_MIME_TYPES:
        raise ValueError("supported upload formats are PNG, JPEG, and WebP")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as error:
        raise ValueError("image data URL contains invalid base64") from error
    if len(raw) > MAX_REQUEST_BYTES:
        raise ValueError("decoded image exceeds the upload limit")
    try:
        with Image.open(BytesIO(raw)) as image:
            width, height = image.size
            if width > MAX_SOURCE_WIDTH or height > MAX_SOURCE_HEIGHT:
                raise ValueError("source image dimensions must not exceed 4096x4096")
            if width * height > MAX_SOURCE_PIXELS:
                raise ValueError("source image exceeds the 16-megapixel safety limit")
            normalized = ImageOps.exif_transpose(image).convert("RGB")
            rgb = np.asarray(normalized, dtype=np.uint8).copy()
    except ValueError:
        raise
    except Exception as error:
        raise ValueError("uploaded data is not a valid image") from error
    validate_source_image(rgb)
    return rgb


def load_samples(samples_dir: Optional[Path]) -> dict[str, Sample]:
    if samples_dir is None:
        return {}
    root = samples_dir.resolve()
    manifest_path = root / "dynamic_x4_set5_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"sample manifest is missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    samples: dict[str, Sample] = {}
    for item in manifest.get("images", []):
        name = item.get("name")
        if not isinstance(name, str) or not name or name in samples:
            raise ValueError("sample manifest contains an invalid or duplicate name")
        candidate = (root / item["input"]).resolve()
        hr_candidate = (root / item["hr"]).resolve()
        try:
            candidate.relative_to(root)
            hr_candidate.relative_to(root)
        except ValueError as error:
            raise ValueError(f"sample path escapes the sample directory: {name}") from error
        if not candidate.is_file():
            raise FileNotFoundError(f"sample image is missing: {candidate}")
        if not hr_candidate.is_file():
            raise FileNotFoundError(f"sample HR image is missing: {hr_candidate}")
        expected = str(item["input_sha256"]).upper()
        actual = sha256_file(candidate)
        if actual != expected:
            raise ValueError(f"sample SHA-256 mismatch: {name}")
        expected_hr = str(item["hr_sha256"]).upper()
        actual_hr = sha256_file(hr_candidate)
        if actual_hr != expected_hr:
            raise ValueError(f"sample HR SHA-256 mismatch: {name}")
        shape = item.get("input_shape_hwc", [])
        if len(shape) != 3 or int(shape[2]) != 3:
            raise ValueError(f"sample shape is invalid: {name}")
        samples[name] = Sample(
            name=name,
            path=candidate,
            hr_path=hr_candidate,
            width=int(shape[1]),
            height=int(shape[0]),
            sha256=expected,
            hr_sha256=expected_hr,
        )
    return samples


class ApplicationState:
    def __init__(
        self,
        processor: RoiProcessor,
        samples: Mapping[str, Sample],
        video_jobs: Optional[VideoJobManager] = None,
        live_camera: Optional[LiveCameraService] = None,
        roi_processor: Optional[RoiProcessor] = None,
        camera_only: bool = False,
        candidate: bool = False,
        mode_controller: Optional[ModeController] = None,
        scale_controller: Optional[ScaleController] = None,
        demo_sample: Optional[str] = None,
        demo_video: Optional[Path] = None,
        board_control_only: bool = False,
        hardware_identity: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.processor = processor
        # The camera Overlay has no RGBX image-output contract. A camera-only
        # diagnostic portal therefore hides image and uploaded-video actions.
        self.roi_processor = roi_processor or processor
        self.camera_only = bool(camera_only)
        self.candidate = bool(candidate)
        self.samples = {} if self.camera_only else dict(samples)
        self.video_jobs = video_jobs
        self.live_camera = live_camera
        self.mode_controller = mode_controller
        self.mode_gpio = None
        self.button_monitor: Optional[ButtonModeMonitor] = None
        self.scale_controller = scale_controller
        self.scale_switch_monitor: Optional[ScaleSwitchMonitor] = None
        # When physical controls are present they are the only authority for
        # operating mode and native scale. Browser writes remain disabled.
        self.board_control_only = bool(board_control_only)
        self.hardware_identity = dict(
            hardware_identity
            or {
                "identity_match": False,
                "manifest_match": False,
                "error": "Overlay identity was not evaluated",
                "overlays": {},
            }
        )
        self.demo_sample = demo_sample
        self.demo_video = None if demo_video is None else Path(demo_video).resolve()
        self._demo_video_job_id: Optional[str] = None
        self._reset_lock = threading.Lock()
        self._source: Optional[SourceImage] = None
        self._generation = 0
        self._source_lock = threading.Lock()
        self._roi_results: OrderedDict[str, dict[str, bytes]] = OrderedDict()
        self._roi_results_lock = threading.Lock()
        if self.mode_controller is not None:
            self.mode_controller.set_transition_handler(self._transition_mode)

    def _active_video_jobs(self) -> list[Any]:
        if self.video_jobs is None:
            return []
        return self.video_jobs.active_jobs()

    def _transition_mode(
        self, previous: str, target: str, source: str
    ) -> Mapping[str, Any]:
        details: dict[str, Any] = {}
        camera_processor = getattr(self.live_camera, "processor", None)
        if previous == "camera" and target != "camera" and self.live_camera is not None:
            self.live_camera.request_stop()
            stopped = self.live_camera.wait_for_stop(5.0)
            if stopped.get("active"):
                raise RuntimeError("camera worker did not stop before mode switch")
            invalidate = getattr(camera_processor, "invalidate_hardware", None)
            if callable(invalidate):
                invalidate()
        if target != "video" and self.video_jobs is not None:
            # Cancel queued jobs as well as jobs that already own the FPGA.
            # Otherwise a dead PC agent leaves a stale job that is reused the
            # next time BTN1 selects video mode.
            self.video_jobs.cancel_all()
            deadline = time.monotonic() + 5.0
            while self._active_video_jobs() and time.monotonic() < deadline:
                time.sleep(0.05)
            remaining = self._active_video_jobs()
            if remaining:
                names = ", ".join(job.job_id for job in remaining)
                raise RuntimeError(
                    f"video job did not release the FPGA before mode switch: {names}"
                )
        if target == "camera":
            if self.live_camera is None:
                raise RuntimeError("live camera service is unavailable")
            # A failed camera start may already have downloaded its bitstream,
            # so invalidate the media path before attempting the transition.
            self.processor.invalidate_hardware()
            invalidate = getattr(camera_processor, "invalidate_hardware", None)
            if callable(invalidate):
                invalidate()
            status = self.live_camera.status()
            if not status.get("active"):
                self.live_camera.start()
            if self.mode_gpio is not None:
                self.mode_gpio.request_rebind()
            details["stream_url"] = "/api/live/stream"
        elif target in {"image", "video"}:
            self.processor.invalidate_hardware()
            if self.mode_gpio is not None:
                self.mode_gpio.request_rebind()
            if target == "image" and self.demo_sample and source == "button":
                selected = self.load_sample(self.demo_sample)
                details.update(
                    {
                        "sample_name": selected.name,
                        "source_generation": selected.generation,
                    }
                )
            elif target == "video" and self.demo_video is not None and source == "button":
                if self.video_jobs is None:
                    raise RuntimeError("video service is unavailable")
                if not self.demo_video.is_file():
                    raise RuntimeError(f"demo video is missing: {self.demo_video}")
                reusable = None
                if self._demo_video_job_id:
                    try:
                        reusable = self.video_jobs.get(self._demo_video_job_id)
                    except KeyError:
                        reusable = None
                if reusable is None or reusable.state in {"failed", "cancelled"}:
                    mime_type = (
                        mimetypes.guess_type(self.demo_video.name)[0]
                        or "video/x-msvideo"
                    )
                    with self.demo_video.open("rb") as stream:
                        reusable = self.video_jobs.create(
                            stream,
                            self.demo_video.stat().st_size,
                            self.demo_video.name,
                            mime_type,
                            scale=self.effective_scale(4),
                        )
                    self._demo_video_job_id = reusable.job_id
                details["video_job_id"] = reusable.job_id
        elif target == "idle":
            # Idle is a real hardware boundary: invalidate both media paths so
            # a subsequent mode cannot inherit a DMA/Overlay session.
            self.processor.invalidate_hardware()
            invalidate = getattr(camera_processor, "invalidate_hardware", None)
            if callable(invalidate):
                invalidate()
            self._demo_video_job_id = None
            details["reset"] = True
        return details

    def request_mode(self, target: str, *, source: str = "api") -> Mapping[str, Any]:
        if self.mode_controller is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "mode control is unavailable")
        if self.board_control_only and source != "button":
            raise ApiError(
                HTTPStatus.CONFLICT,
                "模式由开发板按键控制，请使用 BTN0/BTN1/BTN2；BTN3 为全局复位",
            )
        try:
            force = bool(
                target == "camera"
                and self.live_camera is not None
                and not self.live_camera.status().get("active")
            )
            return self.mode_controller.request(target, source=source, force=force)
        except ValueError as error:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(error)) from error
        except ModeTransitionError as error:
            raise ApiError(HTTPStatus.CONFLICT, str(error)) from error

    def reset_all(self, *, source: str = "button") -> Mapping[str, Any]:
        """Stop every media path and publish the default idle state.

        This is deliberately separate from the browser mode API.  BTN3 calls
        it directly so reset remains available even when the browser is busy,
        a video job is still queued, or the current mode is already idle.
        """

        with self._reset_lock:
            if self.mode_controller is not None:
                try:
                    status = self.mode_controller.request(
                        "idle", source=source, force=True
                    )
                except ModeTransitionError as error:
                    # The transition handler has already requested cancellation
                    # and hardware invalidation.  Keep the controller truthful
                    # while surfacing the failure to the caller/LEDs.
                    raise ApiError(HTTPStatus.CONFLICT, str(error)) from error
            else:
                self._transition_mode("idle", "idle", source)
                status = {"state": "idle", "active_mode": "idle", "source": source}
            with self._source_lock:
                self._source = None
                self._generation += 1
            with self._roi_results_lock:
                self._roi_results.clear()
            self._demo_video_job_id = None
            return status

    def mode_status(self) -> Mapping[str, Any]:
        if self.mode_controller is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "mode control is unavailable")
        status = self.mode_controller.status()
        status["button_monitor"] = (
            {"running": False, "available": False}
            if self.button_monitor is None
            else {"available": True, **self.button_monitor.status()}
        )
        return status

    def effective_scale(self, requested: int = 4) -> int:
        if int(requested) not in (2, 4):
            raise ValueError("scale must be 2 or 4")
        if self.scale_controller is None:
            return int(requested)
        return self.scale_controller.selected_scale()

    def request_scale(self, scale: int) -> Mapping[str, Any]:
        if self.scale_controller is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "scale control is unavailable")
        if self.board_control_only:
            raise ApiError(
                HTTPStatus.CONFLICT,
                "倍率由开发板 SW1/SW0 控制，网页仅显示当前倍率",
            )
        try:
            return self.scale_controller.set_browser_scale(int(scale))
        except (TypeError, ValueError) as error:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(error)) from error
        except ScaleLockedError as error:
            raise ApiError(HTTPStatus.CONFLICT, str(error)) from error

    def scale_status(self) -> Mapping[str, Any]:
        if self.scale_controller is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "scale control is unavailable")
        status = self.scale_controller.status()
        status["switch_monitor"] = (
            {"running": False, "available": False}
            if self.scale_switch_monitor is None
            else {"available": True, **self.scale_switch_monitor.status()}
        )
        return status

    def require_operating_mode(self, target: str) -> None:
        """Require the board-selected mode before accepting a media action."""

        if self.mode_controller is None or not self.board_control_only:
            return
        status = self.mode_controller.status()
        if status.get("state") != target or status.get("active_mode") != target:
            labels = {
                "image": "图片",
                "video": "视频",
                "camera": "摄像头",
                "idle": "空闲",
            }
            current = labels.get(str(status.get("active_mode")), "切换中")
            expected = labels.get(target, target)
            raise ApiError(
                HTTPStatus.CONFLICT,
                f"当前板卡处于{current}模式，请先按对应按键切换到{expected}模式",
            )

    def publish_roi_artifacts(
        self, result_png: bytes, hr_png: Optional[bytes]
    ) -> tuple[str, Mapping[str, Any]]:
        token = secrets.token_urlsafe(18)
        artifacts = {"result.png": bytes(result_png)}
        if hr_png is not None:
            artifacts["hr.png"] = bytes(hr_png)
        with self._roi_results_lock:
            self._roi_results[token] = artifacts
            while len(self._roi_results) > ROI_RESULT_CACHE_ITEMS:
                self._roi_results.popitem(last=False)
        base = f"/api/roi/results/{token}"
        return token, {
            "result_png_url": f"{base}/result.png",
            "hr_png_url": f"{base}/hr.png" if hr_png is not None else None,
        }

    def roi_artifact(self, token: str, name: str) -> bytes:
        with self._roi_results_lock:
            artifacts = self._roi_results.get(token)
            if artifacts is None or name not in artifacts:
                raise ApiError(HTTPStatus.NOT_FOUND, "ROI result is unavailable")
            self._roi_results.move_to_end(token)
            return artifacts[name]

    def refresh_mode_hardware(self) -> None:
        if self.mode_controller is None:
            return
        mode = self.mode_controller.status()
        if mode["state"] == "camera" and self.live_camera is not None:
            live = self.live_camera.status()
            if not live.get("active") and live.get("last_error"):
                self.mode_controller.report_error(
                    str(live["last_error"]), source="camera"
                )
                return
        self.mode_controller.refresh_leds()

    def set_source(
        self,
        name: str,
        rgb: np.ndarray,
        *,
        hr_rgb: Optional[np.ndarray] = None,
        sample: bool = False,
    ) -> SourceImage:
        if self.camera_only:
            raise ApiError(
                HTTPStatus.CONFLICT,
                "This diagnostic camera service does not provide image processing",
            )
        validate_source_image(rgb)
        normalized = np.ascontiguousarray(rgb, dtype=np.uint8)
        normalized.setflags(write=False)
        normalized_hr = None
        if hr_rgb is not None:
            candidate_hr = np.asarray(hr_rgb)
            expected_hr_shape = (
                normalized.shape[0] * 4,
                normalized.shape[1] * 4,
                3,
            )
            if candidate_hr.dtype != np.uint8 or candidate_hr.shape != expected_hr_shape:
                raise ValueError(
                    f"HR image must have shape {expected_hr_shape} and dtype uint8"
                )
            normalized_hr = np.ascontiguousarray(candidate_hr, dtype=np.uint8)
            normalized_hr.setflags(write=False)
        png, data_url = encode_png(normalized)
        with self._source_lock:
            self._generation += 1
            source = SourceImage(
                name=name[:128] or "image",
                rgb=normalized,
                png_data_url=data_url,
                sha256=hashlib.sha256(png).hexdigest().upper(),
                generation=self._generation,
                hr_rgb=normalized_hr,
                sample=bool(sample),
            )
            self._source = source
            return source

    def load_sample(self, name: str) -> SourceImage:
        sample = self.samples.get(name)
        if sample is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "unknown sample")
        if sha256_file(sample.path) != sample.sha256:
            raise ApiError(HTTPStatus.CONFLICT, "sample identity changed on disk")
        if sha256_file(sample.hr_path) != sample.hr_sha256:
            raise ApiError(HTTPStatus.CONFLICT, "sample HR identity changed on disk")
        with Image.open(sample.path) as image:
            rgb = np.asarray(image.convert("RGB"), dtype=np.uint8).copy()
        with Image.open(sample.hr_path) as image:
            hr_rgb = np.asarray(image.convert("RGB"), dtype=np.uint8).copy()
        if rgb.shape[:2] != (sample.height, sample.width):
            raise ApiError(HTTPStatus.CONFLICT, "sample dimensions changed on disk")
        try:
            return self.set_source(sample.name, rgb, hr_rgb=hr_rgb, sample=True)
        except ValueError as error:
            raise ApiError(HTTPStatus.CONFLICT, str(error)) from error

    def source(self) -> SourceImage:
        with self._source_lock:
            if self._source is None:
                raise ApiError(HTTPStatus.CONFLICT, "load or upload an image first")
            return self._source

    def status(self) -> Mapping[str, Any]:
        with self._source_lock:
            current = None if self._source is None else self._source.public()
        formal_fpga_result = (
            not self.processor.preview
            and not self.camera_only
            and not self.candidate
            and self.hardware_identity.get("identity_match") is True
            and self.hardware_identity.get("release_status") == "formal"
        )
        reported_stage = HARDWARE_STAGE
        return {
            "stage": reported_stage,
            "hardware_stage": reported_stage,
            "application_version": APPLICATION_VERSION,
            "candidate": self.candidate,
            "configuration": "Model-S native x2 4-bit / x4 5-bit",
            "execution_mode": self.processor.mode,
            "formal_fpga_result": formal_fpga_result,
            "hardware_identity": self.hardware_identity,
            "scales": [2, 4],
            "processing_window": {
                "minimum_width": MIN_PROCESS_WIDTH,
                "maximum_width": MAX_PROCESS_WIDTH,
                "minimum_height": MIN_PROCESS_HEIGHT,
                "maximum_height": MAX_PROCESS_HEIGHT,
            },
            "source_limits": {
                "minimum_width": 32,
                "maximum_width": MAX_SOURCE_WIDTH,
                "minimum_height": 32,
                "maximum_height": MAX_SOURCE_HEIGHT,
                "maximum_pixels": MAX_SOURCE_PIXELS,
            },
            "sample_count": len(self.samples),
            "source": current,
            "video": None if self.video_jobs is None else self.video_jobs.capabilities(),
            "live_camera": None if self.live_camera is None else self.live_camera.status(),
            "operating_mode": (
                None if self.mode_controller is None else self.mode_status()
            ),
            "scale_control": (
                None if self.scale_controller is None else self.scale_status()
            ),
            "control_source": "board" if self.board_control_only else "browser",
            "capabilities": {
                "camera_only": self.camera_only,
                "image_upload": not self.camera_only,
                "image_execution_mode": (
                    "unavailable" if self.camera_only else self.roi_processor.mode
                ),
                "video_upload": self.video_jobs is not None,
                "live_camera": self.live_camera is not None,
                "mode_control": self.mode_controller is not None,
                "scale_control": self.scale_controller is not None,
                "board_control_only": self.board_control_only,
            },
        }


class RoiRequestHandler(BaseHTTPRequestHandler):
    server_version = "SRLUTMedia/2.0"

    def __init__(
        self,
        *args: Any,
        app_state: ApplicationState,
        static_dir: Path,
        dashboard_dir: Optional[Path] = None,
        project_assets_dir: Optional[Path] = None,
        verbose: bool = False,
        **kwargs: Any,
    ) -> None:
        self.app_state = app_state
        self.static_dir = static_dir.resolve()
        self.dashboard_dir = None if dashboard_dir is None else dashboard_dir.resolve()
        self.project_assets_dir = (
            None if project_assets_dir is None else project_assets_dir.resolve()
        )
        self.verbose = verbose
        super().__init__(*args, **kwargs)

    def log_message(self, format_string: str, *args: Any) -> None:
        if self.verbose:
            super().log_message(format_string, *args)

    def _security_headers(self, *, nonce: Optional[str] = None) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        style_source = "'self'" if nonce is None else f"'self' 'nonce-{nonce}'"
        script_source = "'self'" if nonce is None else f"'self' 'nonce-{nonce}'"
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
            f"style-src {style_source}; script-src {script_source}; connect-src 'self'",
        )

    def _send_bytes(
        self,
        content: bytes,
        content_type: str,
        *,
        nonce: Optional[str] = None,
    ) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self._security_headers(nonce=nonce)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(content)

    def _send_json(self, status: int, payload: Mapping[str, Any]) -> None:
        content = json.dumps(strict_json(payload), ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self._security_headers()
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(content)
        except (BrokenPipeError, ConnectionResetError):
            # A browser can cancel a status poll during mode switching. The
            # client is already gone, so there is no response to send.
            return

    def _send_file(self, path: Path, content_type: str) -> None:
        size = path.stat().st_size
        start, end = 0, max(0, size - 1)
        response_status = HTTPStatus.OK
        range_header = self.headers.get("Range")
        if range_header:
            if not range_header.startswith("bytes=") or "," in range_header:
                raise ApiError(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE, "unsupported byte range")
            first, separator, last = range_header[6:].partition("-")
            try:
                if first:
                    start = int(first)
                    end = int(last) if separator and last else end
                elif last:
                    count = int(last)
                    start = max(0, size - count)
            except ValueError as error:
                raise ApiError(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE, "invalid byte range") from error
            if start < 0 or end < start or start >= size:
                raise ApiError(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE, "byte range is outside the file")
            end = min(end, size - 1)
            response_status = HTTPStatus.PARTIAL_CONTENT
        length = 0 if size == 0 else end - start + 1
        self.send_response(response_status)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if response_status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self._security_headers()
        self.end_headers()
        if self.command != "HEAD" and length:
            with path.open("rb") as handle:
                handle.seek(start)
                remaining = length
                while remaining:
                    chunk = handle.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)

    def _send_static(self, path: str) -> None:
        item = STATIC_FILES.get(path)
        if item is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "not found")
        relative, content_type = item
        candidate = self.static_dir / relative
        if not candidate.is_file():
            raise ApiError(HTTPStatus.NOT_FOUND, "static asset is missing")
        self._send_bytes(candidate.read_bytes(), content_type)

    @staticmethod
    def _content_type(path: Path) -> str:
        if path.suffix.lower() in TEXT_SUFFIXES:
            if path.suffix.lower() == ".json":
                return "application/json; charset=utf-8"
            return "text/plain; charset=utf-8"
        content_type, _ = mimetypes.guess_type(path.name)
        return content_type or "application/octet-stream"

    def _send_dashboard(self) -> None:
        if self.dashboard_dir is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "project overview is unavailable")
        candidate = self.dashboard_dir / "index.html"
        if not candidate.is_file():
            raise ApiError(HTTPStatus.NOT_FOUND, "project overview is unavailable")
        nonce = secrets.token_urlsafe(18)
        html = candidate.read_text(encoding="utf-8")
        if "__PORTAL_NONCE__" not in html:
            raise RuntimeError("project overview nonce placeholders are missing")
        content = html.replace("__PORTAL_NONCE__", nonce).encode("utf-8")
        self._send_bytes(content, "text/html; charset=utf-8", nonce=nonce)

    def _send_project_asset(self, request_path: str) -> None:
        if self.project_assets_dir is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "project asset is unavailable")
        relative = unquote(request_path.removeprefix("/project-assets/"))
        candidate = (self.project_assets_dir / relative).resolve()
        try:
            candidate.relative_to(self.project_assets_dir)
        except ValueError as error:
            raise ApiError(HTTPStatus.NOT_FOUND, "not found") from error
        if not candidate.is_file():
            raise ApiError(HTTPStatus.NOT_FOUND, "project asset is unavailable")
        self._send_bytes(candidate.read_bytes(), self._content_type(candidate))

    def _read_json(self) -> Mapping[str, Any]:
        if self.headers.get_content_type() != "application/json":
            raise ApiError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "expected application/json")
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ApiError(HTTPStatus.LENGTH_REQUIRED, "Content-Length is required")
        try:
            length = int(raw_length)
        except ValueError as error:
            raise ApiError(HTTPStatus.BAD_REQUEST, "invalid Content-Length") from error
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request body is too large")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ApiError(HTTPStatus.BAD_REQUEST, "invalid JSON body") from error
        if not isinstance(payload, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "JSON body must be an object")
        return payload

    def _content_length(self, maximum: int) -> int:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ApiError(HTTPStatus.LENGTH_REQUIRED, "Content-Length is required")
        try:
            length = int(raw_length)
        except ValueError as error:
            raise ApiError(HTTPStatus.BAD_REQUEST, "invalid Content-Length") from error
        if length < 0 or length > maximum:
            raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request body is too large")
        return length

    def _video_jobs(self) -> VideoJobManager:
        if self.app_state.video_jobs is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "video service is unavailable")
        return self.app_state.video_jobs

    @staticmethod
    def _video_job_route(path: str) -> tuple[str, Optional[str]]:
        parts = path.strip("/").split("/")
        if len(parts) not in (4, 5) or parts[:3] != ["api", "video", "jobs"]:
            raise ApiError(HTTPStatus.NOT_FOUND, "unknown video endpoint")
        return parts[3], parts[4] if len(parts) == 5 else None

    def _dispatch_get(self) -> None:
        path = urlsplit(self.path).path
        if path == "/api/status":
            self._send_json(HTTPStatus.OK, {"ok": True, "status": self.app_state.status()})
            return
        if path == "/api/samples":
            samples = [sample.public() for sample in self.app_state.samples.values()]
            self._send_json(HTTPStatus.OK, {"ok": True, "samples": samples})
            return
        if path == "/api/mode":
            if self.app_state.mode_controller is None:
                raise ApiError(HTTPStatus.NOT_FOUND, "mode control is unavailable")
            self._send_json(
                HTTPStatus.OK,
                {"ok": True, "mode": self.app_state.mode_status()},
            )
            return
        if path == "/api/scale":
            self._send_json(
                HTTPStatus.OK,
                {"ok": True, "scale": self.app_state.scale_status()},
            )
            return
        if path.startswith("/api/roi/results/"):
            parts = path.strip("/").split("/")
            if len(parts) != 5 or parts[:3] != ["api", "roi", "results"]:
                raise ApiError(HTTPStatus.NOT_FOUND, "ROI result is unavailable")
            content = self.app_state.roi_artifact(parts[3], parts[4])
            self._send_bytes(content, "image/png")
            return
        if path == "/api/live/status":
            if self.app_state.live_camera is None:
                raise ApiError(HTTPStatus.NOT_FOUND, "live camera service is unavailable")
            self._send_json(HTTPStatus.OK, {"ok": True, "live": self.app_state.live_camera.status()})
            return
        if path == "/api/live/stream":
            view = parse_qs(urlsplit(self.path).query).get("view", ["output"])[0]
            self._send_live_stream(view)
            return
        if path == "/api/live/frame":
            query = parse_qs(urlsplit(self.path).query)
            view = query.get("view", ["output"])[0]
            try:
                after_sequence = int(query.get("after", ["-1"])[0])
            except (TypeError, ValueError) as error:
                raise ApiError(HTTPStatus.BAD_REQUEST, "invalid frame sequence") from error
            self._send_live_frame(view, after_sequence)
            return
        if path == "/api/video/jobs/next":
            job = self._video_jobs().next_queued()
            self._send_json(HTTPStatus.OK, {"ok": True, "job": None if job is None else job.public()})
            return
        if path.startswith("/api/video/jobs/"):
            job_id, action = self._video_job_route(path)
            try:
                job = self._video_jobs().get(job_id)
            except KeyError as error:
                raise ApiError(HTTPStatus.NOT_FOUND, "unknown video job") from error
            if action is None:
                self._send_json(HTTPStatus.OK, {"ok": True, "job": job.public()})
                return
            if action == "report":
                self._send_json(HTTPStatus.OK, {"ok": True, "report": job.report()})
                return
            if action == "input":
                self._send_file(job.input_path, job.input_mime)
                return
            if action == "input-preview":
                if job.input_preview_path is None or job.input_preview_meta is None:
                    raise ApiError(
                        HTTPStatus.CONFLICT,
                        "browser input preview is not ready",
                    )
                self._send_file(job.input_preview_path, "video/mp4")
                return
            if action == "output":
                if job.output_path is None or job.output_meta is None:
                    raise ApiError(HTTPStatus.CONFLICT, "video output is not ready")
                self._send_file(job.output_path, str(job.output_meta["mime_type"]))
                return
            raise ApiError(HTTPStatus.NOT_FOUND, "unknown video endpoint")
        if path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self._security_headers()
            self.end_headers()
            return
        if path in {"/overview", "/overview/", "/overview/index.html"}:
            self._send_dashboard()
            return
        if path.startswith("/project-assets/"):
            self._send_project_asset(path)
            return
        self._send_static(path)

    def _dispatch_post(self) -> None:
        path = urlsplit(self.path).path
        if path == "/api/mode":
            payload = self._read_json()
            target = payload.get("mode")
            if not isinstance(target, str):
                raise ApiError(HTTPStatus.BAD_REQUEST, "mode must be a string")
            status = self.app_state.request_mode(target, source="browser")
            self._send_json(HTTPStatus.OK, {"ok": True, "mode": status})
            return
        if path == "/api/scale":
            payload = self._read_json()
            scale = payload.get("scale")
            if not isinstance(scale, int):
                raise ApiError(HTTPStatus.BAD_REQUEST, "scale must be an integer")
            status = self.app_state.request_scale(scale)
            self._send_json(HTTPStatus.OK, {"ok": True, "scale": status})
            return
        if path == "/api/live/start":
            if self.app_state.live_camera is None:
                raise ApiError(HTTPStatus.NOT_FOUND, "live camera service is unavailable")
            if self.app_state.board_control_only:
                raise ApiError(
                    HTTPStatus.CONFLICT,
                    "摄像头由开发板 BTN2 控制，网页仅显示实时画面",
                )
            if self.app_state.mode_controller is not None:
                self.app_state.request_mode("camera", source="browser")
                status = self.app_state.live_camera.status()
            else:
                status = self.app_state.live_camera.status()
                if status["active"]:
                    raise ApiError(HTTPStatus.CONFLICT, "摄像头实时模式已经运行")
                try:
                    status = self.app_state.live_camera.start()
                except LiveStreamBusy as error:
                    raise ApiError(HTTPStatus.CONFLICT, str(error)) from error
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "live": status,
                    "stream_url": "/api/live/stream",
                },
            )
            return
        if path == "/api/live/stop":
            if self.app_state.live_camera is None:
                raise ApiError(HTTPStatus.NOT_FOUND, "live camera service is unavailable")
            if self.app_state.board_control_only:
                raise ApiError(
                    HTTPStatus.CONFLICT,
                    "摄像头由开发板按键控制，请使用 BTN0/BTN1/BTN2；BTN3 为全局复位",
                )
            if self.app_state.mode_controller is not None:
                self.app_state.request_mode("image", source="browser")
                status = self.app_state.live_camera.status()
            else:
                self.app_state.live_camera.request_stop()
                status = self.app_state.live_camera.wait_for_stop(3.0)
            self._send_json(HTTPStatus.OK, {"ok": True, "live": status})
            return
        if path == "/api/video/jobs":
            self.app_state.require_operating_mode("video")
            if (
                self.app_state.mode_controller is not None
                and not self.app_state.board_control_only
            ):
                self.app_state.request_mode("video", source="upload")
            length = self._content_length(MAX_VIDEO_UPLOAD_BYTES)
            filename = unquote(self.headers.get("X-File-Name", "input.mp4"))
            mime_type = self.headers.get_content_type()
            try:
                requested_scale = int(self.headers.get("X-SR-Scale", "4"))
                scale = self.app_state.effective_scale(requested_scale)
                job = self._video_jobs().create(
                    self.rfile, length, filename, mime_type, scale=scale
                )
            except ValueError as error:
                raise ApiError(HTTPStatus.BAD_REQUEST, str(error)) from error
            self._send_json(HTTPStatus.ACCEPTED, {"ok": True, "job": job.public()})
            return
        if path.startswith("/api/video/jobs/"):
            job_id, action = self._video_job_route(path)
            if action == "output-upload":
                length = self._content_length(MAX_VIDEO_UPLOAD_BYTES)
                filename = unquote(self.headers.get("X-File-Name", "output_x4.mp4"))
                try:
                    job = self._video_jobs().accept_output(
                        job_id,
                        self.rfile,
                        length,
                        filename,
                        self.headers.get_content_type(),
                    )
                except KeyError as error:
                    raise ApiError(HTTPStatus.NOT_FOUND, "unknown video job") from error
                except (RuntimeError, ValueError) as error:
                    raise ApiError(HTTPStatus.CONFLICT, str(error)) from error
                self._send_json(HTTPStatus.OK, {"ok": True, "job": job.public()})
                return
            if action == "input-preview-upload":
                length = self._content_length(MAX_VIDEO_UPLOAD_BYTES)
                filename = unquote(
                    self.headers.get("X-File-Name", "input_preview.mp4")
                )
                try:
                    job = self._video_jobs().accept_input_preview(
                        job_id,
                        self.rfile,
                        length,
                        filename,
                        self.headers.get_content_type(),
                    )
                except KeyError as error:
                    raise ApiError(
                        HTTPStatus.NOT_FOUND,
                        "unknown video job",
                    ) from error
                except (RuntimeError, ValueError) as error:
                    raise ApiError(HTTPStatus.CONFLICT, str(error)) from error
                self._send_json(
                    HTTPStatus.OK,
                    {"ok": True, "job": job.public()},
                )
                return
        payload = self._read_json()
        if path.startswith("/api/video/jobs/"):
            job_id, action = self._video_job_route(path)
            try:
                if action == "claim":
                    job = self._video_jobs().claim(job_id, str(payload.get("receiver_host", "")), int(payload.get("receiver_port", 0)))
                elif action == "agent-report":
                    job = self._video_jobs().accept_agent_report(job_id, payload)
                elif action == "cancel":
                    job = self._video_jobs().cancel(job_id)
                else:
                    raise ApiError(HTTPStatus.NOT_FOUND, "unknown video endpoint")
            except KeyError as error:
                raise ApiError(HTTPStatus.NOT_FOUND, "unknown video job") from error
            except (RuntimeError, ValueError, TypeError) as error:
                raise ApiError(HTTPStatus.CONFLICT, str(error)) from error
            self._send_json(HTTPStatus.OK, {"ok": True, "job": job.public()})
            return
        if path == "/api/sample":
            name = payload.get("name")
            if not isinstance(name, str):
                raise ApiError(HTTPStatus.BAD_REQUEST, "sample name must be a string")
            self.app_state.require_operating_mode("image")
            if (
                self.app_state.mode_controller is not None
                and not self.app_state.board_control_only
            ):
                self.app_state.request_mode("image", source="sample")
            source = self.app_state.load_sample(name)
            self._send_json(HTTPStatus.OK, {"ok": True, "source": source.public(include_png=True)})
            return
        if path == "/api/image":
            name = payload.get("name", "uploaded image")
            if not isinstance(name, str):
                raise ApiError(HTTPStatus.BAD_REQUEST, "image name must be a string")
            try:
                rgb = decode_image_data_url(payload.get("data_url"))
            except ValueError as error:
                raise ApiError(HTTPStatus.BAD_REQUEST, str(error)) from error
            self.app_state.require_operating_mode("image")
            if (
                self.app_state.mode_controller is not None
                and not self.app_state.board_control_only
            ):
                self.app_state.request_mode("image", source="upload")
            source = self.app_state.set_source(Path(name).name, rgb)
            self._send_json(HTTPStatus.OK, {"ok": True, "source": source.public(include_png=True)})
            return
        if path == "/api/roi":
            self.app_state.require_operating_mode("image")
            if (
                self.app_state.mode_controller is not None
                and not self.app_state.board_control_only
            ):
                self.app_state.request_mode("image", source="roi")
            live_camera = self.app_state.live_camera
            if live_camera is not None and live_camera.status().get("active"):
                raise ApiError(
                    HTTPStatus.CONFLICT,
                    "摄像头实时模式正在占用 FPGA，请先停止实时视频再放大图片",
                )
            request_started = time.perf_counter()
            source = self.app_state.source()
            keys = ("x", "y", "width", "height")
            if any(key not in payload for key in keys):
                raise ApiError(HTTPStatus.BAD_REQUEST, "ROI requires x, y, width, and height")
            try:
                requested_scale = int(payload.get("scale", 4))
                scale = self.app_state.effective_scale(requested_scale)
                execution = self.app_state.roi_processor.process(
                    source.rgb,
                    payload["x"],
                    payload["y"],
                    payload["width"],
                    payload["height"],
                    scale=scale,
                )
            except ValueError as error:
                raise ApiError(HTTPStatus.BAD_REQUEST, str(error)) from error
            roi = execution.plan.roi
            source_roi = np.ascontiguousarray(
                source.rgb[roi.y : roi.bottom, roi.x : roi.right]
            )
            nearest_started = time.perf_counter()
            nearest = nearest_scaled(source_roi, scale)
            nearest_seconds = time.perf_counter() - nearest_started
            comparison: dict[str, Any] = {
                "width": execution.plan.output_width,
                "height": execution.plan.output_height,
                "has_hr": source.hr_rgb is not None,
                "quality": None,
            }
            quality_seconds = 0.0
            hr_png_seconds = 0.0
            hr_png: Optional[bytes] = None
            quality_started = time.perf_counter()
            if source.hr_rgb is not None:
                hr_crop = crop_hr_roi(
                    source.hr_rgb,
                    roi.x,
                    roi.y,
                    roi.width,
                    roi.height,
                    scale=scale,
                )
                hr_png_started = time.perf_counter()
                hr_png = encode_png_bytes(hr_crop)
                hr_png_seconds = time.perf_counter() - hr_png_started
                comparison["quality"] = compare_against_hr(
                    hr_crop,
                    execution.image,
                    nearest,
                )
            else:
                comparison["quality"] = compare_against_input(
                    nearest,
                    execution.image,
                    scale=scale,
                )
            quality_seconds = time.perf_counter() - quality_started
            result_png_started = time.perf_counter()
            result_png = encode_png_bytes(execution.image)
            result_png_seconds = time.perf_counter() - result_png_started
            _, artifact_urls = self.app_state.publish_roi_artifacts(
                result_png, hr_png
            )
            comparison["hr_png_url"] = artifact_urls["hr_png_url"]
            metrics = dict(execution.metrics)
            metrics["formal_fpga_result"] = bool(
                metrics.get("formal_fpga_result") and not self.app_state.candidate
                and self.app_state.hardware_identity.get("identity_match") is True
                and self.app_state.hardware_identity.get("release_status") == "formal"
            )
            metrics["server"] = {
                "nearest_seconds": nearest_seconds,
                "quality_seconds": quality_seconds,
                "hr_png_seconds": hr_png_seconds,
                "result_png_seconds": result_png_seconds,
                "result_png_bytes": len(result_png),
                "hr_png_bytes": 0 if hr_png is None else len(hr_png),
                "transport": "binary-url",
                "total_before_send_seconds": time.perf_counter() - request_started,
            }
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "source": source.public(),
                    "plan": execution.plan.as_dict(),
                    "metrics": metrics,
                    "comparison": comparison,
                    "result_png_url": artifact_urls["result_png_url"],
                },
            )
            return
        raise ApiError(HTTPStatus.NOT_FOUND, "unknown API endpoint")

    def _send_live_stream(self, view: str = "output") -> None:
        service = self.app_state.live_camera
        if service is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "live camera service is unavailable")
        if view not in {"input", "output"}:
            raise ApiError(HTTPStatus.BAD_REQUEST, "unknown live stream view")
        live_status = service.status()
        if (
            view == "input"
            and live_status.get("input_preview", {}).get("mode") == "disabled"
        ):
            raise ApiError(
                HTTPStatus.CONFLICT,
                "input preview is disabled for this live session",
            )
        try:
            self.send_response(HTTPStatus.OK)
            self._security_headers()
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                service.stream_to(self.wfile, view=view)
            except (BrokenPipeError, ConnectionResetError):
                # MJPEG viewers are independent subscribers. A refresh or a
                # replaced <img> connection must not stop the shared camera.
                pass
        except LiveStreamBusy as error:
            raise ApiError(HTTPStatus.CONFLICT, str(error)) from error

    def _send_live_frame(self, view: str = "output", after_sequence: int = -1) -> None:
        """Serve one JPEG frame for browsers that block multipart MJPEG."""
        service = self.app_state.live_camera
        if service is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "live camera service is unavailable")
        if view not in {"input", "output"}:
            raise ApiError(HTTPStatus.BAD_REQUEST, "unknown live stream view")
        live_status = service.status()
        if (
            view == "input"
            and live_status.get("input_preview", {}).get("mode") == "disabled"
        ):
            raise ApiError(
                HTTPStatus.CONFLICT,
                "input preview is disabled for this live session",
            )
        payload, sequence = service.snapshot(
            view=view,
            after_sequence=after_sequence,
            timeout_seconds=0.25,
        )
        if payload is None:
            if live_status.get("last_error"):
                raise ApiError(HTTPStatus.SERVICE_UNAVAILABLE, str(live_status["last_error"]))
            raise ApiError(HTTPStatus.CONFLICT, "live frame is not ready")
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("X-SR-Sequence", str(sequence))
            self._security_headers()
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            return

    def do_GET(self) -> None:
        try:
            self._dispatch_get()
        except ApiError as error:
            self._send_json(error.status, {"ok": False, "error": str(error)})
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            if self.verbose:
                traceback.print_exc()
            try:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "internal server error"})
            except (BrokenPipeError, ConnectionResetError):
                return

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_POST(self) -> None:
        try:
            self._dispatch_post()
        except ApiError as error:
            self._send_json(error.status, {"ok": False, "error": str(error)})
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            if self.verbose:
                traceback.print_exc()
            try:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "internal server error"})
            except (BrokenPipeError, ConnectionResetError):
                return


def build_http_server(
    host: str,
    port: int,
    state: ApplicationState,
    static_dir: Path,
    *,
    dashboard_dir: Optional[Path] = None,
    project_assets_dir: Optional[Path] = None,
    verbose: bool = False,
) -> ThreadingHTTPServer:
    handler = partial(
        RoiRequestHandler,
        app_state=state,
        static_dir=static_dir.resolve(),
        dashboard_dir=dashboard_dir,
        project_assets_dir=project_assets_dir,
        verbose=verbose,
    )
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def default_samples_dir() -> Optional[Path]:
    application_root = Path(__file__).resolve().parents[1]
    candidates = (
        application_root / "dynamic_data",
        application_root.parents[1] / "data" / "golden",
    )
    return next(
        (candidate for candidate in candidates if (candidate / "dynamic_x4_set5_manifest.json").is_file()),
        None,
    )


def default_dashboard_dir() -> Optional[Path]:
    application_root = Path(__file__).resolve().parents[1]
    candidates = (
        application_root / "dashboard",
        application_root.parents[1] / "report" / "docs" / "results_dashboard",
    )
    return next((path for path in candidates if (path / "index.html").is_file()), None)


def default_project_assets_dir() -> Optional[Path]:
    application_root = Path(__file__).resolve().parents[1]
    candidates = (
        application_root / "project_assets",
        application_root.parents[1],
    )
    return next((path for path in candidates if path.is_dir()), None)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SR-LUT image ROI and video portal")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument(
        "--live-mode",
        choices=("legacy", "stage38-mjpeg"),
        default="legacy",
        help="select the live camera backend",
    )
    parser.add_argument(
        "--stage38-overlay-dir",
        type=Path,
        default=Path("/opt/sr-lut/current/overlays/camera_x4"),
        help="directory containing the Stage 39 camera sr_lut_x4.bit/.hwh pair",
    )
    parser.add_argument(
        "--unified-modes",
        action="store_true",
        help="serve image, uploaded video, and Stage 39 camera from one portal",
    )
    parser.add_argument(
        "--media-x2-overlay-dir",
        type=Path,
        help="GPIO-enabled x2 media Overlay used by --unified-modes",
    )
    parser.add_argument(
        "--media-x4-overlay-dir",
        type=Path,
        help="GPIO-enabled x4 media Overlay used by --unified-modes",
    )
    parser.add_argument(
        "--mode-buttons",
        action="store_true",
        help="enable BTN0=image, BTN1=video, BTN2=camera, BTN3=global reset",
    )
    parser.add_argument(
        "--mode-gpio-base",
        type=lambda value: int(value, 0),
        default=BoardModeGPIO.BASE_ADDRESS,
    )
    parser.add_argument(
        "--scale-switches",
        action="store_true",
        help="enable physical SW1/SW0 x2/x4 scale sampling",
    )
    parser.add_argument(
        "--board-control-only",
        action="store_true",
        help="make physical BTN/SW controls the only mode and scale authority",
    )
    parser.add_argument(
        "--scale-gpio-base",
        type=lambda value: int(value, 0),
        default=BoardScaleGPIO.BASE_ADDRESS,
    )
    parser.add_argument("--demo-sample", default="baby")
    parser.add_argument(
        "--demo-video",
        type=Path,
        help="optional video submitted automatically when BTN1 selects video mode",
    )
    parser.add_argument("--live-camera-device", default="/dev/video0")
    parser.add_argument("--live-camera-fourcc", default="YUYV")
    parser.add_argument("--live-camera-fps", type=int, default=30)
    parser.add_argument(
        "--stage38-input-preview-mode",
        choices=("full", "decimated", "latest", "disabled"),
        default="full",
        help="Stage 39 input preview policy",
    )
    parser.add_argument(
        "--stage38-input-preview-interval",
        type=int,
        default=3,
        help="encode one input preview every N frames in decimated mode",
    )
    parser.add_argument(
        "--stage38-input-preview-quality",
        type=int,
        default=60,
        help="JPEG quality for the Stage 39 input preview",
    )
    parser.add_argument(
        "--stage38-input-preview-width",
        type=int,
        default=320,
        help="transport width for the Stage 39 input preview",
    )
    parser.add_argument(
        "--stage38-input-preview-height",
        type=int,
        default=180,
        help="transport height for the Stage 39 input preview",
    )
    parser.add_argument(
        "--stage38-output-buffer-policy",
        choices=("acp-cacheable", "hp0-noncoherent", "hp1-noncoherent"),
        default="acp-cacheable",
        help="must match the camera Overlay S2MM port",
    )
    parser.add_argument(
        "--stage38-capture-report-interval",
        type=float,
        default=1.0,
        help="seconds between expensive camera capture-stat snapshots",
    )
    parser.add_argument(
        "--stage38-output-sync-guard-delay",
        type=float,
        default=0.0,
        help="seconds to guard input/output MJPEG ordering after the input write",
    )
    parser.add_argument(
        "--stage38-max-reopen-attempts",
        type=int,
        default=0,
        help="maximum V4L2 reopen attempts; 0 retries until the session is stopped",
    )
    parser.add_argument(
        "--stage38-reopen-backoff-seconds",
        type=float,
        default=0.5,
        help="base delay between V4L2 reopen attempts",
    )
    parser.add_argument("--video-storage-dir", type=Path)
    parser.add_argument("--samples-dir", type=Path, default=default_samples_dir())
    parser.add_argument("--dashboard-dir", type=Path, default=default_dashboard_dir())
    parser.add_argument(
        "--project-assets-dir",
        type=Path,
        default=default_project_assets_dir(),
    )
    parser.add_argument(
        "--software-preview",
        action="store_true",
        help="run a local interface preview without FPGA hardware",
    )
    parser.add_argument(
        "--candidate",
        action="store_true",
        help="mark an isolated diagnostic deployment as non-formal",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    if not 1 <= args.port <= 65535:
        raise ValueError("port must be in 1..65535")
    if args.unified_modes and args.live_mode != "stage38-mjpeg":
        raise ValueError("--unified-modes requires --live-mode stage38-mjpeg")
    if args.mode_buttons and not args.unified_modes:
        raise ValueError("--mode-buttons requires --unified-modes")
    if args.scale_switches and not args.unified_modes:
        raise ValueError("--scale-switches requires --unified-modes")
    if args.board_control_only and not (args.unified_modes and args.mode_buttons and args.scale_switches):
        raise ValueError(
            "--board-control-only requires --unified-modes, --mode-buttons, and --scale-switches"
        )
    if args.unified_modes and (
        args.media_x2_overlay_dir is None or args.media_x4_overlay_dir is None
    ):
        raise ValueError(
            "--unified-modes requires both media x2 and media x4 Overlay directories"
        )
    samples = load_samples(args.samples_dir)
    if args.live_mode == "stage38-mjpeg" and not args.unified_modes:
        # The portal processor and the live MJPEG service must share the same
        # camera Overlay object; loading a second PYNQ Overlay invalidates
        # the first one.
        os.environ["SR_LUT_X4_OVERLAY_DIR"] = str(args.stage38_overlay_dir)
    hardware_lock = threading.RLock()
    reconfiguration_lock = threading.RLock()
    if args.unified_modes:
        os.environ["SR_LUT_X2_OVERLAY_DIR"] = str(args.media_x2_overlay_dir)
        os.environ["SR_LUT_X4_OVERLAY_DIR"] = str(args.media_x4_overlay_dir)
        hardware_identity = require_overlay_identity(
            {
                "media_x2": args.media_x2_overlay_dir,
                "media_x4": args.media_x4_overlay_dir,
                "camera_x4": args.stage38_overlay_dir,
            }
        )
    else:
        hardware_identity = None
    processor = RoiProcessor(
        preview=args.software_preview,
        hardware_lock=hardware_lock,
        reconfiguration_lock=reconfiguration_lock,
    )
    processor.prepare()
    storage_dir = args.video_storage_dir or (Path(__file__).resolve().parents[1] / "portal_jobs")
    video_jobs = None
    roi_processor = None
    mode_gpio = None
    mode_controller = None
    button_monitor = None
    scale_gpio = None
    scale_controller = None
    scale_switch_monitor = None
    if args.live_mode == "stage38-mjpeg":
        # The camera bitstream terminates in a hardware MJPEG encoder and
        # cannot service the portal's RGBX image or offline-video contracts.
        if args.unified_modes:
            from sr_lut_x4_driver import load_overlay

            def load_camera_overlay(**_options: Any) -> Any:
                return load_overlay(
                    overlay_dir=args.stage38_overlay_dir,
                    overlay_stem="sr_lut_x4",
                )

            camera_processor = RoiProcessor(
                preview=args.software_preview,
                overlay_loader=load_camera_overlay,
                hardware_lock=hardware_lock,
                reconfiguration_lock=reconfiguration_lock,
            )
            video_jobs = VideoJobManager(processor, storage_dir)
            roi_processor = processor
        else:
            camera_processor = processor
            roi_processor = RoiProcessor(preview=True)
        live_camera = Stage38MjpegCameraService(
            camera_processor,
            overlay_dir=args.stage38_overlay_dir,
            device=args.live_camera_device,
            camera_fps=args.live_camera_fps,
            camera_fourcc=args.live_camera_fourcc,
            max_reopen_attempts=args.stage38_max_reopen_attempts,
            reopen_backoff_seconds=args.stage38_reopen_backoff_seconds,
            input_preview_mode=args.stage38_input_preview_mode,
            input_preview_interval=args.stage38_input_preview_interval,
            input_preview_quality=args.stage38_input_preview_quality,
            input_preview_size=(
                args.stage38_input_preview_width,
                args.stage38_input_preview_height,
            ),
            output_buffer_policy=args.stage38_output_buffer_policy,
            capture_report_interval_seconds=args.stage38_capture_report_interval,
            output_sync_guard_delay_seconds=args.stage38_output_sync_guard_delay,
        )
    else:
        video_jobs = VideoJobManager(processor, storage_dir)
        live_camera = LiveCameraService(processor, device=args.live_camera_device)
    if args.unified_modes:
        if args.mode_buttons:
            mode_gpio = BoardModeGPIO.open(
                base_address=args.mode_gpio_base,
                access_lock=reconfiguration_lock,
            )
        mode_controller = ModeController(initial_mode="image", gpio=mode_gpio)
        if args.scale_switches:
            scale_gpio = BoardScaleGPIO.open(
                base_address=args.scale_gpio_base,
                access_lock=reconfiguration_lock,
            )
            scale_controller = ScaleController(
                gpio=scale_gpio,
                board_control_only=args.board_control_only,
            )
    state = ApplicationState(
        processor,
        samples,
        video_jobs,
        live_camera=live_camera,
        roi_processor=roi_processor,
        camera_only=args.live_mode == "stage38-mjpeg" and not args.unified_modes,
        candidate=args.candidate,
        mode_controller=mode_controller,
        scale_controller=scale_controller,
        demo_sample=args.demo_sample,
        demo_video=args.demo_video,
        board_control_only=args.board_control_only,
        hardware_identity=hardware_identity,
    )
    state.mode_gpio = mode_gpio
    if mode_gpio is not None:
        def handle_button_action(action: str) -> None:
            if action == "global_reset":
                state.reset_all(source="button")
            else:
                state.request_mode(action, source="button")

        button_monitor = ButtonModeMonitor(
            mode_gpio,
            handle_button_action,
            led_refresh=state.refresh_mode_hardware,
        )
        state.button_monitor = button_monitor
        button_monitor.start()
    if scale_gpio is not None and scale_controller is not None:
        scale_switch_monitor = ScaleSwitchMonitor(
            scale_gpio,
            scale_controller.update_switches,
        )
        state.scale_switch_monitor = scale_switch_monitor
        scale_switch_monitor.start()
    static_dir = Path(__file__).resolve().parent / "static"
    server = build_http_server(
        args.host,
        args.port,
        state,
        static_dir,
        dashboard_dir=args.dashboard_dir,
        project_assets_dir=args.project_assets_dir,
        verbose=args.verbose,
    )
    mode = "SOFTWARE PREVIEW (NON-FORMAL)" if args.software_preview else "PYNQ-Z2 FPGA"
    print(f"SR-LUT ROI magnifier: http://{args.host}:{args.port}/")
    print(f"Execution mode: {mode}")
    print(f"Native Set5 samples: {len(samples)}")
    print(f"Project overview: {'available' if args.dashboard_dir else 'unavailable'}")
    print(f"Unified image/video portal: application {APPLICATION_VERSION}")
    print(
        "Overlay identity: "
        + ("MATCH" if state.hardware_identity.get("identity_match") else "MISMATCH")
    )
    print(f"Live camera backend: {args.live_mode}")
    print(f"Unified mode control: {'board-only buttons' if args.board_control_only else ('buttons' if args.mode_buttons else ('browser' if args.unified_modes else 'disabled'))}")
    print(f"Scale control: {'board-only SW1/SW0' if args.board_control_only else ('SW1/SW0 + browser' if args.scale_switches else 'browser request')}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if button_monitor is not None:
            button_monitor.stop()
        if scale_switch_monitor is not None:
            scale_switch_monitor.stop()
        if live_camera.status().get("active"):
            live_camera.request_stop()
            live_camera.wait_for_stop(5.0)
        server.server_close()


if __name__ == "__main__":
    main()
