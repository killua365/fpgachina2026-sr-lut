"""Exclusive USB-camera capture with synchronized raw/FPGA MJPEG fan-out."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import threading
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

try:
    import cv2
except ImportError:  # Portal and protocol tests do not require camera extras.
    cv2 = None  # type: ignore[assignment]
import numpy as np

try:
    from video_pipeline.frame_session import ReusableFrameSession
    from video_pipeline.live_pipeline import SampleStats
except ImportError:  # pragma: no cover - package import fallback
    from ..video_pipeline.frame_session import ReusableFrameSession
    from ..video_pipeline.live_pipeline import SampleStats


class LiveStreamBusy(RuntimeError):
    """Raised when another live stream or FPGA operation owns the hardware."""


NETWORK_BLOCKED_WRITE_THRESHOLD_SECONDS = 0.05
STATUS_SUMMARY_CACHE_SECONDS = 5.0
OUTPUT_INPUT_SYNC_WAIT_TIMEOUT_SECONDS = 0.5
# A browser normally opens the input and output viewers together.  Keep the
# output-only startup grace period bounded so a stale/replaced connection
# cannot hold a worker thread for the full synchronization timeout.
OUTPUT_INPUT_SUBSCRIBER_STARTUP_TIMEOUT_SECONDS = 0.25
DEFAULT_OUTPUT_INPUT_SYNC_GUARD_DELAY_SECONDS = 0.02
MAX_OUTPUT_INPUT_SYNC_GUARD_DELAY_SECONDS = 0.5


class LiveCameraService:
    """Capture one camera stream and broadcast raw and x4 JPEG frames."""

    def __init__(self, processor: Any, *, device: str = "/dev/video0", input_size: tuple[int, int] = (320, 180), camera_size: tuple[int, int] = (640, 480), camera_fps: int = 30, jpeg_quality: int = 80) -> None:
        self.processor = processor
        self.device = device
        self.width, self.height = map(int, input_size)
        self.camera_width, self.camera_height = map(int, camera_size)
        self.camera_fps = int(camera_fps)
        self.jpeg_quality = int(jpeg_quality)
        self._active_lock = threading.Lock()
        self._state_condition = threading.Condition(threading.Lock())
        self._stop_event = threading.Event()
        self._active = False
        self._frames = 0
        self._started = 0.0
        self._last_error: str | None = None
        self._sequence = 0
        self._raw_sequence = 0
        self._raw_jpeg: bytes | None = None
        self._output_jpeg: bytes | None = None
        self._worker: threading.Thread | None = None
        self._timing_stats: dict[str, SampleStats] = {}
        self._network_stats = SampleStats()
        self._network_bytes = 0
        self._network_frames_sent = 0
        self._network_frames_overwritten = 0
        self._network_blocked_writes = 0
        self._network_blocked_seconds = 0.0
        self._input_network_sequence = -1
        self._input_subscribers = 0
        self._input_subscriber_seen = False
        self._next_input_subscriber_id = 0
        self._input_subscriber_sequences: dict[int, int] = {}
        self._next_stream_subscriber_id = 0
        self._active_stream_subscribers: dict[str, int] = {}
        self._stream_subscriber_replacements = 0
        self._output_sync_waits = 0
        self._output_sync_wait_seconds = 0.0

    def status(self) -> dict[str, Any]:
        with self._state_condition:
            elapsed = time.perf_counter() - self._started if self._started else 0.0
            return {"active": self._active, "device": self.device, "input": {"width": self.width, "height": self.height}, "output": {"width": self.width * 4, "height": self.height * 4}, "camera": {"width": self.camera_width, "height": self.camera_height, "fps": self.camera_fps}, "camera_display": {"width": self.width * 2, "height": self.height * 2}, "scale": 4, "frames": self._frames, "fps": self._frames / elapsed if elapsed > 0 else 0.0, "last_error": self._last_error}

    def start(self) -> dict[str, Any]:
        if not self._active_lock.acquire(blocking=False):
            raise LiveStreamBusy("摄像头实时模式正在运行或 FPGA 正忙")
        with self._state_condition:
            self._active = True
            self._frames = 0
            self._started = time.perf_counter()
            self._last_error = None
            self._sequence = 0
            self._raw_sequence = 0
            self._raw_jpeg = None
            self._output_jpeg = None
            self._network_stats = SampleStats()
            self._network_bytes = 0
            self._network_frames_sent = 0
            self._network_frames_overwritten = 0
            self._network_blocked_writes = 0
            self._network_blocked_seconds = 0.0
            self._input_network_sequence = -1
            self._input_subscribers = 0
            self._input_subscriber_seen = False
            self._next_input_subscriber_id = 0
            self._input_subscriber_sequences = {}
            self._next_stream_subscriber_id = 0
            self._active_stream_subscribers = {}
            self._stream_subscriber_replacements = 0
            self._output_sync_waits = 0
            self._output_sync_wait_seconds = 0.0
            self._timing_stats = {
                name: SampleStats() for name in self._timing_stats
            }
        self._stop_event.clear()
        self._worker = threading.Thread(target=self._run, name="live-camera", daemon=True)
        self._worker.start()
        return self.status()

    def request_stop(self) -> None:
        self._stop_event.set()
        with self._state_condition:
            self._state_condition.notify_all()

    def wait_for_stop(self, timeout_seconds: float = 3.0) -> dict[str, Any]:
        """Wait briefly for the worker to release the camera and FPGA lock."""

        worker = self._worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=max(0.0, float(timeout_seconds)))
        return self.status()

    @staticmethod
    def _prepare_frame(frame: np.ndarray, width: int, height: int) -> np.ndarray:
        if cv2 is None:
            raise RuntimeError("OpenCV is required for camera frame preparation")
        source_height, source_width = frame.shape[:2]
        target_ratio = width / height
        source_ratio = source_width / source_height
        if source_ratio > target_ratio:
            crop_width = int(source_height * target_ratio) & ~1
            left = max(0, (source_width - crop_width) // 2) & ~1
            frame = frame[:, left : left + crop_width]
        elif source_ratio < target_ratio:
            crop_height = int(source_width / target_ratio) & ~1
            top = max(0, (source_height - crop_height) // 2) & ~1
            frame = frame[top : top + crop_height, :]
        return cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)

    def _encode(self, frame: np.ndarray, *, quality: int | None = None) -> bytes:
        if cv2 is None:
            raise RuntimeError("OpenCV is required for camera JPEG encoding")
        jpeg_quality = self.jpeg_quality if quality is None else int(quality)
        ok, encoded = cv2.imencode(
            ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality]
        )
        if not ok:
            raise RuntimeError("实时画面 JPEG 编码失败")
        return encoded.tobytes()

    def _encode_input_preview(self, frame: np.ndarray) -> tuple[bytes, float]:
        """Encode an input preview and report only the encoder wall time."""

        started = time.perf_counter()
        preview = frame
        preview_size = (
            getattr(self, "input_preview_width", frame.shape[1]),
            getattr(self, "input_preview_height", frame.shape[0]),
        )
        if frame.shape[1::-1] != preview_size:
            source_width, source_height = frame.shape[1::-1]
            preview_width, preview_height = preview_size
            horizontal_step = source_width // preview_width
            vertical_step = source_height // preview_height
            if (
                horizontal_step == vertical_step
                and horizontal_step > 1
                and preview_width * horizontal_step == source_width
                and preview_height * vertical_step == source_height
            ):
                preview = np.ascontiguousarray(
                    frame[::vertical_step, ::horizontal_step]
                )
            else:
                if cv2 is None:
                    raise RuntimeError("OpenCV is required for input preview resizing")
                # This browser reference is outside the FPGA input path.
                preview = cv2.resize(
                    frame,
                    preview_size,
                    interpolation=cv2.INTER_NEAREST,
                )
        payload = self._encode(preview, quality=self.input_preview_quality)
        return payload, time.perf_counter() - started

    @staticmethod
    def _snapshot_jpeg(payload: Any) -> bytes:
        """Keep hardware output alive after the reusable DMA buffer is reused."""

        return payload if isinstance(payload, bytes) else bytes(payload)

    def _run(self) -> None:
        if cv2 is None:
            self._finish(RuntimeError("OpenCV is required for live camera mode"))
            return
        capture = cv2.VideoCapture(self.device, cv2.CAP_V4L2)
        if not capture.isOpened():
            self._finish(RuntimeError(f"摄像头无法打开: {self.device}"))
            return
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.camera_width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.camera_height)
        capture.set(cv2.CAP_PROP_FPS, self.camera_fps)
        output_frame = np.empty((self.height * 4, self.width * 4, 3), dtype=np.uint8)
        frames = 0
        try:
            with self.processor.hardware_session(scale=4) as overlay:
                session = ReusableFrameSession(overlay, self.width, self.height, timeout_seconds=10.0, top_byte_validation="off", buffer_count=2, output_mode="rgbx", output_buffer_policy="acp-cacheable", scale=4)
                try:
                    while not self._stop_event.is_set():
                        ok, frame = capture.read()
                        if not ok or frame is None:
                            raise RuntimeError("摄像头停止输出画面")
                        raw_display = self._prepare_frame(frame, self.width * 2, self.height * 2)
                        prepared = cv2.resize(raw_display, (self.width, self.height), interpolation=cv2.INTER_AREA)
                        result = session.run_bgr(prepared, output=output_frame, buffer_index=frames % 2)
                        raw_payload = self._encode(raw_display)
                        output_payload = self._encode(result.image)
                        frames += 1
                        with self._state_condition:
                            self._raw_jpeg = raw_payload
                            self._output_jpeg = output_payload
                            self._frames = frames
                            self._sequence += 1
                            self._raw_sequence += 1
                            self._state_condition.notify_all()
                finally:
                    session.close()
        except BaseException as exc:
            self._finish(exc)
        else:
            self._finish(None)
        finally:
            capture.release()

    def _finish(self, error: BaseException | None) -> None:
        with self._state_condition:
            if error is not None and not self._stop_event.is_set():
                self._last_error = f"{type(error).__name__}: {error}"
            self._active = False
            self._state_condition.notify_all()
        if self._active_lock.locked():
            self._active_lock.release()

    def _wait_for_input_delivery(self, sequence: int) -> None:
        """Keep a matching output MJPEG part behind the input part on the wire."""

        # Decimated and disabled previews do not provide a matching input part
        # for every output sequence, so only full preview participates.
        if getattr(self, "input_preview_mode", "full") != "full":
            return
        started = time.perf_counter()
        waited = False
        sync_required = False
        with self._state_condition:
            # Give the browser a short startup window to establish its input
            # subscription before the first output part is sent.
            if not self._input_subscriber_seen:
                deadline = (
                    time.perf_counter()
                    + OUTPUT_INPUT_SUBSCRIBER_STARTUP_TIMEOUT_SECONDS
                )
                while (
                    self._active
                    and not self._stop_event.is_set()
                    and not self._input_subscriber_seen
                ):
                    remaining = deadline - time.perf_counter()
                    if remaining <= 0:
                        break
                    waited = True
                    self._state_condition.wait(timeout=min(remaining, 0.05))
            if self._input_subscribers <= 0:
                return
            sync_required = True
            deadline = time.perf_counter() + OUTPUT_INPUT_SYNC_WAIT_TIMEOUT_SECONDS
            while (
                self._active
                and not self._stop_event.is_set()
                and self._input_subscribers > 0
                and self._input_network_sequence < sequence
            ):
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    break
                waited = True
                self._state_condition.wait(timeout=min(remaining, 0.05))
            if waited:
                self._output_sync_waits += 1
                self._output_sync_wait_seconds += time.perf_counter() - started
        if sync_required and not self._stop_event.is_set():
            # TCP streams have independent kernel buffers; a short guard after
            # the input write prevents cross-connection packet reordering at
            # the browser while leaving the FPGA worker fully asynchronous.
            guard_delay = getattr(
                self,
                "output_sync_guard_delay_seconds",
                DEFAULT_OUTPUT_INPUT_SYNC_GUARD_DELAY_SECONDS,
            )
            if guard_delay > 0:
                time.sleep(guard_delay)

    def snapshot(
        self,
        *,
        view: str = "output",
        after_sequence: int = -1,
        timeout_seconds: float = 0.25,
    ) -> tuple[bytes | None, int]:
        """Return one current JPEG frame for clients that cannot consume MJPEG.

        ``after_sequence`` lets a polling browser wait briefly for a fresh
        frame, avoiding duplicate requests while keeping the capture worker
        completely independent from HTTP clients.
        """
        if view not in {"input", "output"}:
            raise ValueError("unknown live stream view")
        deadline = time.perf_counter() + max(0.0, float(timeout_seconds))
        with self._state_condition:
            while self._active and not self._stop_event.is_set():
                sequence = self._raw_sequence if view == "input" else self._sequence
                payload = self._raw_jpeg if view == "input" else self._output_jpeg
                if payload is not None and sequence > int(after_sequence):
                    return payload, sequence
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    break
                self._state_condition.wait(timeout=min(remaining, 0.05))
            sequence = self._raw_sequence if view == "input" else self._sequence
            payload = self._raw_jpeg if view == "input" else self._output_jpeg
            if payload is None or sequence <= int(after_sequence):
                return None, sequence
            return payload, sequence

    def stream_to(self, output: Any, *, view: str = "output", boundary: bytes = b"frame") -> None:
        """Write raw or FPGA MJPEG frames from the shared worker."""
        if view not in {"input", "output"}:
            raise ValueError("unknown live stream view")
        preview_mode = getattr(self, "input_preview_mode", "full")
        is_input = view == "input"
        input_subscriber_id: int | None = None
        with self._state_condition:
            self._next_stream_subscriber_id += 1
            stream_subscriber_id = self._next_stream_subscriber_id
            if view in self._active_stream_subscribers:
                self._stream_subscriber_replacements += 1
            self._active_stream_subscribers[view] = stream_subscriber_id
            if is_input:
                self._next_input_subscriber_id += 1
                input_subscriber_id = self._next_input_subscriber_id
                self._input_subscriber_seen = True
                self._input_subscriber_sequences.clear()
                self._input_subscriber_sequences[input_subscriber_id] = -1
                self._input_subscribers = len(self._input_subscriber_sequences)
            self._state_condition.notify_all()
        sequence = -1
        try:
            while True:
                with self._state_condition:
                    if (
                        self._active_stream_subscribers.get(view)
                        != stream_subscriber_id
                    ):
                        return
                    current_sequence = (
                        self._raw_sequence if view == "input" else self._sequence
                    )
                    while (
                        sequence == current_sequence
                        and self._active
                        and not self._stop_event.is_set()
                        and self._active_stream_subscribers.get(view)
                        == stream_subscriber_id
                    ):
                        self._state_condition.wait(timeout=1.0)
                        current_sequence = (
                            self._raw_sequence if view == "input" else self._sequence
                        )
                    if (
                        self._active_stream_subscribers.get(view)
                        != stream_subscriber_id
                    ):
                        return
                    if self._stop_event.is_set():
                        return
                    payload = self._raw_jpeg if is_input else self._output_jpeg
                    if is_input and payload is None and preview_mode == "disabled":
                        return
                    active = self._active
                    if sequence == current_sequence and not active:
                        if self._last_error:
                            raise RuntimeError(self._last_error)
                        return
                    skipped = 0 if sequence < 0 else max(0, current_sequence - sequence - 1)
                    sequence = current_sequence
                    if payload is None and not active:
                        if self._last_error:
                            raise RuntimeError(self._last_error)
                        return
                if payload is None:
                    continue
                if not is_input:
                    self._wait_for_input_delivery(sequence)
                    if self._stop_event.is_set():
                        return
                    with self._state_condition:
                        if (
                            self._active_stream_subscribers.get(view)
                            != stream_subscriber_id
                        ):
                            return
                write_started = time.perf_counter()
                header = (
                    b"--"
                    + boundary
                    + b"\r\nContent-Type: image/jpeg\r\n"
                    + f"Content-Length: {len(payload)}\r\n".encode("ascii")
                    + f"X-SR-Sequence: {sequence}\r\n\r\n".encode("ascii")
                )
                output.write(header)
                output.write(payload)
                output.write(b"\r\n")
                output.flush()
                # A reset/stop can be requested by the writer's flush hook
                # (and by BTN3 through the shared stop event).  Exit before
                # taking another condition wait so subscriber teardown is
                # deterministic even when the worker remains marked active
                # for a few milliseconds during final cleanup.
                if self._stop_event.is_set():
                    return
                write_elapsed = time.perf_counter() - write_started
                with self._state_condition:
                    self._network_stats.add(write_elapsed)
                    self._network_bytes += len(payload)
                    self._network_frames_sent += 1
                    self._network_frames_overwritten += skipped
                    if is_input:
                        self._input_network_sequence = max(
                            self._input_network_sequence, sequence
                        )
                        if input_subscriber_id is not None:
                            self._input_subscriber_sequences[input_subscriber_id] = max(
                                self._input_subscriber_sequences.get(
                                    input_subscriber_id, -1
                                ),
                                sequence,
                            )
                        self._state_condition.notify_all()
                    # A slow browser must be visible in diagnostics, while the
                    # latest-frame policy keeps it from blocking FPGA processing.
                    if write_elapsed >= NETWORK_BLOCKED_WRITE_THRESHOLD_SECONDS:
                        self._network_blocked_writes += 1
                        self._network_blocked_seconds += write_elapsed
                if not active and self._stop_event.is_set():
                    return
        finally:
            with self._state_condition:
                if (
                    self._active_stream_subscribers.get(view)
                    == stream_subscriber_id
                ):
                    self._active_stream_subscribers.pop(view, None)
                if is_input:
                    if input_subscriber_id is not None:
                        self._input_subscriber_sequences.pop(input_subscriber_id, None)
                    self._input_subscribers = len(self._input_subscriber_sequences)
                self._state_condition.notify_all()


class Stage38MjpegCameraService(LiveCameraService):
    """Serve the Stage 39 hardware-JPEG stream directly to the portal."""

    def __init__(
        self,
        processor: Any,
        *,
        overlay_dir: Path,
        device: str = "/dev/video0",
        camera_size: tuple[int, int] = (640, 360),
        camera_fps: int = 30,
        camera_fourcc: str = "YUYV",
        buffer_count: int = 4,
        max_reopen_attempts: int = 3,
        reopen_backoff_seconds: float = 0.5,
        input_preview_mode: str = "full",
        input_preview_interval: int = 3,
        input_preview_quality: int = 60,
        input_preview_size: tuple[int, int] = (320, 180),
        output_buffer_policy: str = "acp-cacheable",
        capture_report_interval_seconds: float = 1.0,
        output_sync_guard_delay_seconds: float = DEFAULT_OUTPUT_INPUT_SYNC_GUARD_DELAY_SECONDS,
    ) -> None:
        super().__init__(
            processor,
            device=device,
            input_size=(480, 270),
            camera_size=camera_size,
            camera_fps=camera_fps,
            jpeg_quality=80,
        )
        self.overlay_dir = Path(overlay_dir)
        self.camera_fourcc = camera_fourcc
        self.buffer_count = int(buffer_count)
        if isinstance(max_reopen_attempts, bool) or int(max_reopen_attempts) < 0:
            raise ValueError("max_reopen_attempts must be a non-negative integer")
        if float(reopen_backoff_seconds) < 0:
            raise ValueError("reopen_backoff_seconds must be non-negative")
        if input_preview_mode not in {"full", "decimated", "latest", "disabled"}:
            raise ValueError(
                "input_preview_mode must be full, decimated, latest, or disabled"
            )
        if isinstance(input_preview_interval, bool) or int(input_preview_interval) < 1:
            raise ValueError("input_preview_interval must be a positive integer")
        if not 1 <= int(input_preview_quality) <= 100:
            raise ValueError("input_preview_quality must be within 1..100")
        input_preview_width, input_preview_height = map(int, input_preview_size)
        if (
            input_preview_width <= 0
            or input_preview_height <= 0
            or input_preview_width > self.width
            or input_preview_height > self.height
        ):
            raise ValueError(
                "input_preview_size must be positive and no larger than 480x270"
            )
        if output_buffer_policy not in {
            "acp-cacheable",
            "hp0-noncoherent",
            "hp1-noncoherent",
        }:
            raise ValueError(
                "output_buffer_policy must be acp-cacheable, hp0-noncoherent, "
                "or hp1-noncoherent"
            )
        if float(capture_report_interval_seconds) <= 0:
            raise ValueError("capture_report_interval_seconds must be positive")
        if not 0 <= float(output_sync_guard_delay_seconds) <= MAX_OUTPUT_INPUT_SYNC_GUARD_DELAY_SECONDS:
            raise ValueError(
                "output_sync_guard_delay_seconds must be within 0..0.5 seconds"
            )
        self.max_reopen_attempts = int(max_reopen_attempts)
        self.reopen_backoff_seconds = float(reopen_backoff_seconds)
        self.input_preview_mode = input_preview_mode
        self.input_preview_interval = int(input_preview_interval)
        self.input_preview_quality = int(input_preview_quality)
        self.input_preview_width = input_preview_width
        self.input_preview_height = input_preview_height
        self.output_buffer_policy = output_buffer_policy
        self.capture_report_interval_seconds = float(capture_report_interval_seconds)
        self.output_sync_guard_delay_seconds = float(output_sync_guard_delay_seconds)
        self._camera_negotiated: dict[str, Any] | None = None
        self._last_jpeg_bytes: int | None = None
        self._last_hardware_seconds: float | None = None
        self._reopen_count = 0
        self._capture_report: dict[str, Any] | None = None
        self._capture_report_updates = 0
        self._next_capture_report_at = 0.0
        self._hardware_metrics: Any = None
        self._last_sr_status: int | None = None
        self._last_mjpeg_status: int | None = None
        self._preview_submitted = 0
        self._preview_published = 0
        self._preview_overwritten = 0
        self._preview_failures = 0
        self._preview_last_error: str | None = None
        self._timing_stats = {
            "capture_wait_seconds": SampleStats(),
            "capture_frame_age_seconds": SampleStats(),
            "input_jpeg_seconds": SampleStats(),
            "hardware_pack_seconds": SampleStats(),
            "hardware_dma_pl_seconds": SampleStats(),
            "hardware_sync_seconds": SampleStats(),
            "hardware_seconds": SampleStats(),
            "output_snapshot_seconds": SampleStats(),
            "frame_wall_seconds": SampleStats(),
            "producer_interval_seconds": SampleStats(),
            "total_seconds": SampleStats(),
        }
        self._status_summary_lock = threading.Lock()
        self._status_summary_cache: dict[str, Any] | None = None
        self._status_summary_expires_at = 0.0

    def start(self) -> dict[str, Any]:
        with self._status_summary_lock:
            self._status_summary_cache = None
            self._status_summary_expires_at = 0.0
        with self._state_condition:
            self._reopen_count = 0
            self._capture_report = None
            self._capture_report_updates = 0
            self._next_capture_report_at = 0.0
            self._hardware_metrics = None
            self._last_sr_status = None
            self._last_mjpeg_status = None
            self._camera_negotiated = None
            self._last_jpeg_bytes = None
            self._last_hardware_seconds = None
            self._preview_submitted = 0
            self._preview_published = 0
            self._preview_overwritten = 0
            self._preview_failures = 0
            self._preview_last_error = None
        return super().start()

    def _status_summaries(self) -> dict[str, Any]:
        now = time.perf_counter()
        with self._status_summary_lock:
            if (
                self._status_summary_cache is not None
                and now < self._status_summary_expires_at
            ):
                return self._status_summary_cache
            with self._state_condition:
                timing_stats = tuple(self._timing_stats.items())
                network_stats = self._network_stats
            # Percentiles are relatively expensive on the board. Compute them
            # without the producer's condition lock and reuse the snapshot for
            # frequent portal status polls.
            summary = {
                "timing": {
                    name: stats.summary() for name, stats in timing_stats
                },
                "network_write_seconds": network_stats.summary(),
            }
            self._status_summary_cache = summary
            self._status_summary_expires_at = (
                time.perf_counter() + STATUS_SUMMARY_CACHE_SECONDS
            )
            return summary

    def status(self) -> dict[str, Any]:
        result = super().status()
        summaries = self._status_summaries()
        with self._state_condition:
            result.update(
                {
                    "pipeline": "stage38-mjpeg-hardware",
                    "format": "MJPEG",
                    "overlay_dir": str(self.overlay_dir),
                    "camera_fourcc": self.camera_fourcc,
                    "camera_negotiated": self._camera_negotiated,
                    "last_jpeg_bytes": self._last_jpeg_bytes,
                    "last_hardware_frame_seconds": self._last_hardware_seconds,
                    "timing": summaries["timing"],
                    "capture_report": self._capture_report,
                    "hardware_metrics": (
                        asdict(self._hardware_metrics)
                        if is_dataclass(self._hardware_metrics)
                        else self._hardware_metrics
                    ),
                    "last_sr_status": self._last_sr_status,
                    "last_mjpeg_status": self._last_mjpeg_status,
                    "reopen_count": self._reopen_count,
                    "max_reopen_attempts": self.max_reopen_attempts,
                    "input_preview": {
                        "mode": self.input_preview_mode,
                        "interval": (
                            1
                            if self.input_preview_mode in {"full", "latest"}
                            else self.input_preview_interval
                        ),
                        "quality": self.input_preview_quality,
                        "source": {"width": 480, "height": 270},
                        "transport": {
                            "width": self.input_preview_width,
                            "height": self.input_preview_height,
                        },
                        "display": {"width": 480, "height": 270},
                        "publish_order": (
                            "async_input_first"
                            if self.input_preview_mode != "disabled"
                            else "disabled"
                        ),
                        "submitted": self._preview_submitted,
                        "published": self._preview_published,
                        "overwritten": self._preview_overwritten,
                        "failures": self._preview_failures,
                        "last_error": self._preview_last_error,
                    },
                    "output_buffer_policy": self.output_buffer_policy,
                    "network": {
                        "bytes": self._network_bytes,
                        "frames_sent": self._network_frames_sent,
                        "frames_overwritten": self._network_frames_overwritten,
                        "blocked_writes": self._network_blocked_writes,
                        "blocked_seconds": self._network_blocked_seconds,
                        "blocked_write_threshold_seconds": NETWORK_BLOCKED_WRITE_THRESHOLD_SECONDS,
                        "write_seconds": summaries["network_write_seconds"],
                        "input_network_sequence": self._input_network_sequence,
                        "input_network_sequence_min": min(
                            self._input_subscriber_sequences.values(),
                            default=-1,
                        ),
                        "input_subscribers": self._input_subscribers,
                        "active_stream_views": sorted(self._active_stream_subscribers),
                        "stream_subscriber_replacements": self._stream_subscriber_replacements,
                        "output_sync_waits": self._output_sync_waits,
                        "output_sync_wait_seconds": self._output_sync_wait_seconds,
                        "output_sync_guard_delay_seconds": self.output_sync_guard_delay_seconds,
                    },
                    "capture_report_updates": self._capture_report_updates,
                    "capture_report_interval_seconds": self.capture_report_interval_seconds,
                    "input": {"width": 480, "height": 270},
                    "output": {"width": 1920, "height": 1080},
                    "camera_display": {"width": 480, "height": 270},
                    "scale": 4,
                }
            )
        return result

    def _publish_input_preview(
        self,
        future: Future[tuple[bytes, float]],
        sequence: int,
    ) -> None:
        try:
            payload, encode_elapsed = future.result()
        except BaseException as error:
            with self._state_condition:
                self._preview_failures += 1
                self._preview_last_error = f"{type(error).__name__}: {error}"
                self._state_condition.notify_all()
            return
        with self._state_condition:
            if sequence <= self._raw_sequence:
                return
            self._raw_jpeg = payload
            # Input and output headers now carry the same processed-frame id.
            # The browser can enforce causal presentation without blocking FPGA.
            self._raw_sequence = sequence
            self._preview_published += 1
            self._timing_stats["input_jpeg_seconds"].add(encode_elapsed)
            self._state_condition.notify_all()

    def _run(self) -> None:
        # Keep board-only imports out of the software preview path.
        if cv2 is None:
            self._finish(RuntimeError("OpenCV is required for Stage 39 camera mode"))
            return
        latest: Any = None
        capture: Any = None
        preview_executor: ThreadPoolExecutor | None = None
        preview_future: Future[tuple[bytes, float]] | None = None
        error: BaseException | None = None
        try:
            from video_pipeline.live_pipeline import LatestFrameCapture
            from video_pipeline.live_pipeline import _configure_capture, prepare_frame
            from video_pipeline.mjpeg_session import MjpegFrameSession

            # Percentile calculation in LatestFrameCapture.report() is useful
            # for diagnostics but too expensive to perform for every frame.
            # Keep a cheap, bounded snapshot during streaming and publish a
            # final complete report when the worker stops.
            self._next_capture_report_at = (
                time.perf_counter() + self.capture_report_interval_seconds
            )

            preflight_capture = cv2.VideoCapture(self.device, cv2.CAP_V4L2)
            try:
                if not preflight_capture.isOpened():
                    raise RuntimeError(f"摄像头无法打开: {self.device}")
                self._camera_negotiated = _configure_capture(
                    preflight_capture,
                    cv2,
                    self.camera_width,
                    self.camera_height,
                    self.camera_fps,
                    self.camera_fourcc,
                )
                ok, frame = preflight_capture.read()
                if not ok or frame is None:
                    raise RuntimeError("摄像头预检未获取到第一帧")
            finally:
                preflight_capture.release()

            # Reuse the processor's loaded camera Overlay and lock it for
            # the whole live session, preventing ROI/video jobs from changing
            # the bitstream while DMA buffers are active.
            with self.processor.hardware_session(scale=4) as overlay:
                if self.input_preview_mode != "disabled":
                    preview_executor = ThreadPoolExecutor(
                        max_workers=1,
                        thread_name_prefix="camera-input-preview",
                    )
                with MjpegFrameSession(
                    overlay,
                    # Bound DMA waits so Stop can release a wedged transaction.
                    timeout_seconds=2.5,
                    buffer_count=self.buffer_count,
                    coherent_output=self.output_buffer_policy == "acp-cacheable",
                ) as session:
                    frames = 0
                    reopen_attempts = 0
                    while not self._stop_event.is_set():
                        try:
                            capture = cv2.VideoCapture(self.device, cv2.CAP_V4L2)
                            if not capture.isOpened():
                                raise RuntimeError(f"摄像头无法打开: {self.device}")
                            self._camera_negotiated = _configure_capture(
                                capture,
                                cv2,
                                self.camera_width,
                                self.camera_height,
                                self.camera_fps,
                                self.camera_fourcc,
                            )
                            latest = LatestFrameCapture(
                                capture,
                                lambda candidate: prepare_frame(
                                    candidate, 480, 270, cv2
                                ),
                                max_consecutive_read_failures=5,
                                read_retry_delay_seconds=0.02,
                            )
                            latest.start()
                            last_sequence = -1
                            previous_frame_started: float | None = None
                            while not self._stop_event.is_set():
                                frame_started = time.perf_counter()
                                if previous_frame_started is not None:
                                    self._timing_stats[
                                        "producer_interval_seconds"
                                    ].add(frame_started - previous_frame_started)
                                previous_frame_started = frame_started
                                wait_started = time.perf_counter()
                                captured = latest.get_after(last_sequence, 1.0)
                                wait_elapsed = time.perf_counter() - wait_started
                                last_sequence = captured.source_sequence
                                should_encode_preview = (
                                    self.input_preview_mode in {"full", "latest"}
                                    or (
                                        self.input_preview_mode == "decimated"
                                        and frames % self.input_preview_interval == 0
                                    )
                                )
                                if should_encode_preview:
                                    if preview_executor is None:
                                        raise RuntimeError(
                                            "input preview executor is unavailable"
                                        )
                                    if preview_future is None or preview_future.done():
                                        preview_sequence = frames + 1
                                        preview_future = preview_executor.submit(
                                            self._encode_input_preview,
                                            captured.image,
                                        )
                                        preview_future.add_done_callback(
                                            lambda completed, sequence=preview_sequence: (
                                                self._publish_input_preview(
                                                    completed,
                                                    sequence,
                                                )
                                            )
                                        )
                                        with self._state_condition:
                                            self._preview_submitted += 1
                                    else:
                                        # Latest-only scheduling keeps a slow
                                        # preview encoder off the FPGA critical path.
                                        with self._state_condition:
                                            self._preview_overwritten += 1
                                result = session.run_bgr(
                                    captured.image,
                                    buffer_index=frames % self.buffer_count,
                                )
                                snapshot_started = time.perf_counter()
                                output_payload = self._snapshot_jpeg(result.jpeg)
                                snapshot_elapsed = time.perf_counter() - snapshot_started
                                frame_elapsed = time.perf_counter() - frame_started
                                frames += 1
                                with self._state_condition:
                                    self._timing_stats[
                                        "capture_wait_seconds"
                                    ].add(wait_elapsed)
                                    self._timing_stats[
                                        "capture_frame_age_seconds"
                                    ].add(captured.frame_age_seconds)
                                    self._timing_stats[
                                        "hardware_pack_seconds"
                                    ].add(result.pack_seconds)
                                    self._timing_stats[
                                        "hardware_dma_pl_seconds"
                                    ].add(result.dma_and_pl_seconds)
                                    self._timing_stats[
                                        "hardware_sync_seconds"
                                    ].add(result.sync_seconds)
                                    self._timing_stats["hardware_seconds"].add(
                                        result.total_seconds
                                    )
                                    self._timing_stats[
                                        "output_snapshot_seconds"
                                    ].add(snapshot_elapsed)
                                    self._timing_stats["frame_wall_seconds"].add(
                                        frame_elapsed
                                    )
                                    self._timing_stats["total_seconds"].add(
                                        frame_elapsed
                                    )
                                    self._hardware_metrics = result.hardware_metrics
                                    self._last_sr_status = result.sr_status
                                    self._last_mjpeg_status = result.mjpeg_status
                                    self._output_jpeg = output_payload
                                    self._frames = frames
                                    self._last_jpeg_bytes = result.jpeg_length
                                    self._last_hardware_seconds = result.total_seconds
                                    self._sequence = frames
                                    self._state_condition.notify_all()
                                now = time.perf_counter()
                                if (
                                    frames == 1
                                    or now >= self._next_capture_report_at
                                ):
                                    report = latest.report()
                                    with self._state_condition:
                                        self._capture_report = report
                                        self._capture_report_updates += 1
                                        self._next_capture_report_at = (
                                            now + self.capture_report_interval_seconds
                                        )
                            break
                        except BaseException:
                            if latest is not None:
                                try:
                                    latest.stop()
                                finally:
                                    report = latest.report()
                                    with self._state_condition:
                                        self._capture_report = report
                                        self._capture_report_updates += 1
                            elif capture is not None:
                                capture.release()
                            latest = None
                            capture = None
                            # A zero limit means keep the camera session alive
                            # and retry until the user stops it. This covers
                            # USB re-enumeration that takes longer than the
                            # short finite retry window used for diagnostics.
                            if (
                                self._stop_event.is_set()
                                or (
                                    self.max_reopen_attempts > 0
                                    and reopen_attempts >= self.max_reopen_attempts
                                )
                            ):
                                raise
                            reopen_attempts += 1
                            with self._state_condition:
                                self._reopen_count += 1
                                self._state_condition.notify_all()
                            self._stop_event.wait(
                                self.reopen_backoff_seconds * reopen_attempts
                            )
        except BaseException as exc:
            error = exc
        finally:
            if latest is not None:
                try:
                    latest.stop()
                except BaseException as stop_error:
                    if error is None:
                        error = stop_error
            elif capture is not None:
                try:
                    capture.release()
                except BaseException:
                    pass
            if preview_executor is not None:
                preview_executor.shutdown(wait=True)
            # Mark inactive only after V4L2 and the capture thread are down.
            self._finish(error)


__all__ = [
    "LiveCameraService",
    "LiveStreamBusy",
    "NETWORK_BLOCKED_WRITE_THRESHOLD_SECONDS",
    "Stage38MjpegCameraService",
]
