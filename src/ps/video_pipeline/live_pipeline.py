"""Pipelined USB-camera capture for the MB420 data path."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from fractions import Fraction
import math
import queue
import signal
import threading
import time
from typing import Any, Callable

import numpy as np

from sr_lut_x4_driver import load_overlay
from video_pipeline.frame_session import ReusableFrameSession
from video_pipeline.identity import APPLICATION_VERSION, HARDWARE_STAGE, VIDEO_PIPELINE
from video_pipeline.protocol import PIXEL_FORMAT_MB420, SessionHeader
from video_pipeline.sink import Yuv420FrameSink


DEFAULT_INPUT_SIZE = (480, 270)
DEFAULT_CAMERA_SIZE = (640, 360)
DEFAULT_CAMERA_FPS = 30
DEFAULT_CAMERA_FOURCC = "YUYV"
DEFAULT_BUFFER_COUNT = 4


class SampleStats:
    """Bounded timing samples with lifetime mean/min/max counters."""

    def __init__(self, sample_limit: int = 4096) -> None:
        self._lock = threading.Lock()
        self.samples: deque[float] = deque(maxlen=sample_limit)
        self.count = 0
        self.total = 0.0
        self.minimum = math.inf
        self.maximum = 0.0

    def add(self, value: float) -> None:
        value = float(value)
        if not math.isfinite(value) or value < 0:
            return
        with self._lock:
            self.samples.append(value)
            self.count += 1
            self.total += value
            self.minimum = min(self.minimum, value)
            self.maximum = max(self.maximum, value)

    def summary(self) -> dict[str, float | int] | None:
        with self._lock:
            if not self.count:
                return None
            values = np.asarray(self.samples, dtype=np.float64)
            return {
                "count": self.count,
                "sample_count": int(values.size),
                "minimum": self.minimum,
                "mean": self.total / self.count,
                "p95": float(np.percentile(values, 95)),
                "p99": float(np.percentile(values, 99)),
                "maximum": self.maximum,
            }


@dataclass(frozen=True)
class CapturedFrame:
    source_sequence: int
    image: np.ndarray
    captured_at: float
    prepare_seconds: float
    frame_age_seconds: float = 0.0


def prepare_frame(frame: np.ndarray, width: int, height: int, cv2_module: Any) -> np.ndarray:
    """Take a centered aspect-ratio crop and resize it to the FPGA input."""

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
    return cv2_module.resize(
        frame,
        (int(width), int(height)),
        interpolation=cv2_module.INTER_LINEAR,
    )


class LatestFrameCapture:
    """Capture and prepare frames independently while retaining only the latest."""

    def __init__(
        self,
        capture: Any,
        prepare: Callable[[np.ndarray], np.ndarray],
        *,
        clock: Callable[[], float] = time.perf_counter,
        max_consecutive_read_failures: int = 3,
        read_retry_delay_seconds: float = 0.02,
    ) -> None:
        if (
            isinstance(max_consecutive_read_failures, bool)
            or int(max_consecutive_read_failures) < 0
        ):
            raise ValueError(
                "max_consecutive_read_failures must be a non-negative integer"
            )
        if float(read_retry_delay_seconds) < 0:
            raise ValueError("read_retry_delay_seconds must be non-negative")
        self.capture = capture
        self.prepare = prepare
        self.clock = clock
        self.max_consecutive_read_failures = int(max_consecutive_read_failures)
        self.read_retry_delay_seconds = float(read_retry_delay_seconds)
        self.condition = threading.Condition()
        self.raw_condition = threading.Condition()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(
            target=self._capture_loop,
            name="live-camera-capture",
            daemon=True,
        )
        self.prepare_thread = threading.Thread(
            target=self._prepare_loop,
            name="live-camera-prepare",
            daemon=True,
        )
        self._release_lock = threading.Lock()
        self._capture_released = False
        self.latest_raw: tuple[int, np.ndarray, float] | None = None
        self.latest: CapturedFrame | None = None
        self.failure: BaseException | None = None
        self.raw_sequence = -1
        self.last_prepared_raw_sequence = -1
        self.sequence = -1
        self.last_delivered = -1
        self.frames_read = 0
        self.frames_captured = 0
        self.raw_frames_overwritten = 0
        self.frames_overwritten = 0
        self.read_failures = 0
        self.read_seconds = SampleStats()
        self.prepare_seconds = SampleStats()
        self.frame_age_seconds = SampleStats()
        self.capture_intervals = SampleStats()
        self._previous_capture_time: float | None = None

    def start(self) -> None:
        self.prepare_thread.start()
        self.thread.start()

    def _set_failure(self, error: BaseException) -> None:
        with self.condition:
            if self.failure is None:
                self.failure = error
            self.condition.notify_all()
        with self.raw_condition:
            self.raw_condition.notify_all()

    def _capture_loop(self) -> None:
        consecutive_read_failures = 0
        try:
            while not self.stop_event.is_set():
                read_started = self.clock()
                ok, frame = self.capture.read()
                captured_at = self.clock()
                self.read_seconds.add(captured_at - read_started)
                if not ok or frame is None:
                    if self.stop_event.is_set():
                        return
                    self.read_failures += 1
                    consecutive_read_failures += 1
                    if (
                        consecutive_read_failures
                        <= self.max_consecutive_read_failures
                    ):
                        self.stop_event.wait(self.read_retry_delay_seconds)
                        continue
                    raise RuntimeError(
                        "camera stopped producing frames after "
                        f"{self.read_failures} read failures"
                    )
                consecutive_read_failures = 0
                if self._previous_capture_time is not None:
                    self.capture_intervals.add(captured_at - self._previous_capture_time)
                self._previous_capture_time = captured_at
                with self.raw_condition:
                    self.raw_sequence += 1
                    if (
                        self.latest_raw is not None
                        and self.latest_raw[0] > self.last_prepared_raw_sequence
                    ):
                        self.raw_frames_overwritten += 1
                    self.latest_raw = (self.raw_sequence, frame, captured_at)
                    self.frames_read += 1
                    self.raw_condition.notify_all()
        except BaseException as exc:
            self._set_failure(exc)
        finally:
            # OpenCV V4L2 backends can crash when release() races with read()
            # on another thread. Let the capture thread own normal teardown.
            self._release_capture()
            with self.raw_condition:
                self.raw_condition.notify_all()

    def _prepare_loop(self) -> None:
        raw_sequence = -1
        try:
            while not self.stop_event.is_set():
                with self.raw_condition:
                    while (
                        self.raw_sequence <= raw_sequence
                        and self.failure is None
                        and not self.stop_event.is_set()
                    ):
                        self.raw_condition.wait(timeout=0.5)
                    if self.failure is not None:
                        return
                    if self.stop_event.is_set():
                        return
                    assert self.latest_raw is not None
                    raw_sequence, frame, captured_at = self.latest_raw
                prepare_started = self.clock()
                prepared = np.ascontiguousarray(self.prepare(frame))
                prepare_elapsed = self.clock() - prepare_started
                frame_age = self.clock() - captured_at
                self.prepare_seconds.add(prepare_elapsed)
                self.frame_age_seconds.add(frame_age)
                self.last_prepared_raw_sequence = raw_sequence
                with self.condition:
                    if self.latest is not None and self.latest.source_sequence > self.last_delivered:
                        self.frames_overwritten += 1
                    self.latest = CapturedFrame(
                        raw_sequence,
                        prepared,
                        captured_at,
                        prepare_elapsed,
                        frame_age,
                    )
                    self.sequence = raw_sequence
                    self.frames_captured += 1
                    self.condition.notify_all()
        except BaseException as exc:
            self.stop_event.set()
            self._set_failure(exc)

    def _release_capture(self) -> None:
        with self._release_lock:
            if self._capture_released:
                return
            try:
                self.capture.release()
            except (AttributeError, OSError):
                pass
            self._capture_released = True

    def get_after(self, source_sequence: int, timeout_seconds: float) -> CapturedFrame:
        deadline = self.clock() + timeout_seconds
        with self.condition:
            while self.sequence <= source_sequence and self.failure is None:
                remaining = deadline - self.clock()
                if remaining <= 0:
                    raise TimeoutError("camera did not publish a new frame")
                self.condition.wait(timeout=remaining)
            if self.failure is not None:
                raise self.failure
            assert self.latest is not None
            self.last_delivered = self.latest.source_sequence
            return self.latest

    def stop(self) -> None:
        self.stop_event.set()
        with self.condition:
            self.condition.notify_all()
        with self.raw_condition:
            self.raw_condition.notify_all()
        self.thread.join(timeout=5.0)
        if self.thread.is_alive():
            # A backend may block read() until the next frame. Release only
            # after the normal join window as a last-resort unblock.
            self._release_capture()
            with self.condition:
                self.condition.notify_all()
            with self.raw_condition:
                self.raw_condition.notify_all()
            self.thread.join(timeout=5.0)
        if self.thread.is_alive():
            raise RuntimeError("camera capture thread did not stop")
        self.prepare_thread.join(timeout=5.0)
        if self.prepare_thread.is_alive():
            raise RuntimeError("camera prepare thread did not stop")
        self._release_capture()

    def report(self) -> dict[str, Any]:
        interval = self.capture_intervals.summary()
        measured_fps = None
        if interval is not None and float(interval["mean"]) > 0:
            measured_fps = 1.0 / float(interval["mean"])
        return {
            "frames_read": self.frames_read,
            "frames_captured": self.frames_captured,
            "raw_frames_overwritten": self.raw_frames_overwritten,
            "frames_overwritten": self.frames_overwritten,
            "read_failures": self.read_failures,
            "last_raw_sequence": self.raw_sequence,
            "last_source_sequence": self.sequence,
            "measured_fps": measured_fps,
            "read_seconds": self.read_seconds.summary(),
            "prepare_seconds": self.prepare_seconds.summary(),
            "frame_age_seconds": self.frame_age_seconds.summary(),
            "capture_interval_seconds": interval,
        }


def _fourcc_name(value: float) -> str:
    raw = max(0, int(value))
    return "".join(chr((raw >> (8 * index)) & 0xFF) for index in range(4)).rstrip("\x00")


def _configure_capture(
    capture: Any,
    cv2_module: Any,
    camera_width: int,
    camera_height: int,
    camera_fps: int,
    camera_fourcc: str,
) -> dict[str, Any]:
    if len(camera_fourcc) != 4 or not camera_fourcc.isascii():
        raise ValueError("camera FOURCC must contain four ASCII characters")
    capture.set(
        cv2_module.CAP_PROP_FOURCC,
        int.from_bytes(camera_fourcc.encode("ascii"), "little"),
    )
    capture.set(cv2_module.CAP_PROP_FRAME_WIDTH, camera_width)
    capture.set(cv2_module.CAP_PROP_FRAME_HEIGHT, camera_height)
    capture.set(cv2_module.CAP_PROP_FPS, camera_fps)
    buffer_property = getattr(cv2_module, "CAP_PROP_BUFFERSIZE", None)
    if buffer_property is not None:
        capture.set(buffer_property, 1)
    return {
        "requested_width": int(camera_width),
        "requested_height": int(camera_height),
        "requested_fps": int(camera_fps),
        "requested_fourcc": camera_fourcc,
        "negotiated_width": int(capture.get(cv2_module.CAP_PROP_FRAME_WIDTH)),
        "negotiated_height": int(capture.get(cv2_module.CAP_PROP_FRAME_HEIGHT)),
        "negotiated_fps": float(capture.get(cv2_module.CAP_PROP_FPS)),
        "fourcc": _fourcc_name(capture.get(cv2_module.CAP_PROP_FOURCC)),
    }


def stream_camera(
    device: str,
    receiver_host: str,
    receiver_port: int,
    *,
    input_size: tuple[int, int] = DEFAULT_INPUT_SIZE,
    camera_size: tuple[int, int] = DEFAULT_CAMERA_SIZE,
    camera_fps: int = DEFAULT_CAMERA_FPS,
    camera_fourcc: str = DEFAULT_CAMERA_FOURCC,
    duration_seconds: float = 0.0,
    frame_limit: int = 0,
    buffer_count: int = DEFAULT_BUFFER_COUNT,
    timeout_seconds: float = 10.0,
    cv2_module: Any = None,
    capture_factory: Callable[..., Any] | None = None,
    overlay_loader: Callable[[], Any] = load_overlay,
    session_factory: Any = ReusableFrameSession,
    sink_factory: Any = Yuv420FrameSink,
    clock: Callable[[], float] = time.perf_counter,
    install_signal_handlers: bool = True,
) -> dict[str, Any]:
    """Run capture, FPGA MB420 and TCP sending as a bounded pipeline."""

    width, height = map(int, input_size)
    camera_width, camera_height = map(int, camera_size)
    if not 32 <= width <= 480 or not 32 <= height <= 320:
        raise ValueError("input size must be within 32..480 x 32..320")
    if width % 2 or height % 2:
        raise ValueError("input size must be even for x4 YUV420 output")
    if not 1 <= int(buffer_count) <= 8:
        raise ValueError("buffer_count must be in 1..8")
    if duration_seconds < 0 or frame_limit < 0 or timeout_seconds <= 0:
        raise ValueError("duration/frame limit must be non-negative and timeout positive")
    if cv2_module is None:
        import cv2 as cv2_module
    if capture_factory is None:
        capture_factory = cv2_module.VideoCapture
    capture = capture_factory(device, cv2_module.CAP_V4L2)
    if not capture.isOpened():
        raise RuntimeError(f"camera cannot be opened: {device}")
    camera_report = _configure_capture(
        capture,
        cv2_module,
        camera_width,
        camera_height,
        int(camera_fps),
        camera_fourcc,
    )
    negotiated_fps = float(camera_report["negotiated_fps"])
    stream_fps = negotiated_fps if math.isfinite(negotiated_fps) and negotiated_fps > 0 else float(camera_fps)
    fps_fraction = Fraction(stream_fps).limit_denominator(1_000_000)
    output_width, output_height = width * 4, height * 4
    bytes_per_frame = output_width * output_height * 3 // 2
    wire = SessionHeader(
        width,
        height,
        output_width,
        output_height,
        fps_fraction.numerator,
        fps_fraction.denominator,
        -1,
        PIXEL_FORMAT_MB420,
        bytes_per_frame,
    )

    overlay = session = sink = capture_worker = None
    sender_thread: threading.Thread | None = None
    sender_queue: queue.Queue[Any] = queue.Queue(maxsize=int(buffer_count))
    free_slots: queue.Queue[int] = queue.Queue(maxsize=int(buffer_count))
    for slot_index in range(int(buffer_count)):
        free_slots.put(slot_index)
    stop_event = threading.Event()
    sender_errors: list[BaseException] = []
    pack_stats = SampleStats()
    dma_stats = SampleStats()
    session_stats = SampleStats()
    sender_stats = SampleStats()
    latency_stats = SampleStats()
    initialization_started = clock()
    started = initialization_started
    processed_frames = 0
    last_source_sequence = -1

    def stop_handler(_signum: int, _frame: Any) -> None:
        stop_event.set()

    old_sigint = old_sigterm = None
    if install_signal_handlers:
        old_sigint = signal.signal(signal.SIGINT, stop_handler)
        old_sigterm = signal.signal(signal.SIGTERM, stop_handler)

    def sender_worker() -> None:
        try:
            while True:
                item = sender_queue.get()
                if item is None:
                    return
                output_index, captured, result, slot_index = item
                send_started = clock()
                try:
                    sink.send_frame(
                        output_index,
                        result.image,
                        hardware_status=result.hardware_status,
                        hardware_error_code=result.hardware_error_code,
                        input_word_count=result.input_word_count,
                        output_word_count=result.output_word_count,
                        payload_crc32=result.hardware_crc32,
                    )
                    sender_stats.add(clock() - send_started)
                    latency_stats.add(clock() - captured.captured_at)
                finally:
                    free_slots.put(slot_index)
        except BaseException as exc:
            sender_errors.append(exc)
            stop_event.set()

    def put_sender(item: Any) -> None:
        while True:
            if sender_errors:
                raise sender_errors[0]
            try:
                sender_queue.put(item, timeout=0.1)
                return
            except queue.Full:
                if stop_event.is_set():
                    raise RuntimeError("live sender stopped while its queue was full")

    try:
        overlay = overlay_loader()
        session = session_factory(
            overlay,
            width,
            height,
            timeout_seconds=timeout_seconds,
            top_byte_validation="off",
            buffer_count=int(buffer_count),
            output_mode="mb420",
            output_buffer_policy="acp-cacheable",
            scale=4,
        )
        sink = sink_factory(
            receiver_host,
            int(receiver_port),
            wire,
            connect_timeout_seconds=timeout_seconds,
            io_timeout_seconds=timeout_seconds,
        )
        capture_worker = LatestFrameCapture(
            capture,
            lambda frame: prepare_frame(frame, width, height, cv2_module),
            clock=clock,
        )
        sender_thread = threading.Thread(target=sender_worker, name="live-mb420-sender", daemon=True)
        sender_thread.start()
        started = clock()
        initialization_seconds = started - initialization_started
        capture_worker.start()
        while not stop_event.is_set():
            if frame_limit and processed_frames >= frame_limit:
                break
            if duration_seconds and clock() - started >= duration_seconds:
                break
            captured = capture_worker.get_after(last_source_sequence, timeout_seconds)
            last_source_sequence = captured.source_sequence
            while True:
                if sender_errors:
                    raise sender_errors[0]
                try:
                    slot_index = free_slots.get(timeout=0.1)
                    break
                except queue.Empty:
                    if stop_event.is_set():
                        raise RuntimeError("live pipeline stopped while waiting for a DMA slot")
            try:
                result = session.run_mb420(captured.image, buffer_index=slot_index)
            except BaseException:
                free_slots.put(slot_index)
                raise
            pack_stats.add(result.pack_seconds)
            dma_stats.add(result.dma_and_pl_seconds)
            session_stats.add(result.total_seconds)
            put_sender((processed_frames, captured, result, slot_index))
            processed_frames += 1
            if processed_frames % 30 == 0:
                elapsed = clock() - started
                print(
                    f"LIVE_PIPELINE_FRAME frame={processed_frames} "
                    f"source={last_source_sequence + 1} "
                    f"fps={processed_frames / max(elapsed, 1e-9):.2f}",
                    flush=True,
                )
        put_sender(None)
        assert sender_thread is not None
        sender_thread.join(timeout=timeout_seconds)
        if sender_thread.is_alive():
            raise TimeoutError("live sender thread did not stop")
        if sender_errors:
            raise sender_errors[0]
        elapsed = clock() - started
        capture_report = capture_worker.report()
        report = {
            "schema_version": 2,
            "status": "pass",
            "application_version": APPLICATION_VERSION,
            "hardware_stage": HARDWARE_STAGE,
            "video_pipeline": VIDEO_PIPELINE,
            "source": {"device": device, **camera_report, **capture_report},
            "session": {
                "input_width": width,
                "input_height": height,
                "scale": 4,
                "output_width": output_width,
                "output_height": output_height,
                "pixel_format": "mb420",
                "bytes_per_frame": bytes_per_frame,
                "buffer_count": int(buffer_count),
                "latest_frame_policy": True,
            },
            "performance": {
                "processed_frames": processed_frames,
                "initialization_seconds": initialization_seconds,
                "elapsed_seconds": elapsed,
                "end_to_end_fps": processed_frames / max(elapsed, 1e-9),
                "network_payload_bytes": int(getattr(sink, "total_payload_bytes", 0)),
                "network_payload_megabytes_per_second": (
                    float(getattr(sink, "total_payload_bytes", 0)) / max(elapsed, 1e-9) / 1_000_000
                ),
                "input_pack_seconds": pack_stats.summary(),
                "dma_and_pl_seconds": dma_stats.summary(),
                "frame_session_seconds": session_stats.summary(),
                "network_send_seconds": sender_stats.summary(),
                "capture_to_send_seconds": latency_stats.summary(),
            },
            "stopped_by_signal": stop_event.is_set(),
        }
        sink.finish(report)
        return report
    except BaseException:
        stop_event.set()
        if sink is not None:
            sink.fail()
        raise
    finally:
        stop_event.set()
        if capture_worker is not None:
            capture_worker.stop()
        else:
            try:
                capture.release()
            except (AttributeError, OSError):
                pass
        if sender_thread is not None and sender_thread.is_alive():
            try:
                sender_queue.put_nowait(None)
            except queue.Full:
                pass
            sender_thread.join(timeout=1.0)
        if session is not None:
            session.close()
        if install_signal_handlers:
            assert old_sigint is not None and old_sigterm is not None
            signal.signal(signal.SIGINT, old_sigint)
            signal.signal(signal.SIGTERM, old_sigterm)


__all__ = [
    "CapturedFrame",
    "DEFAULT_BUFFER_COUNT",
    "DEFAULT_CAMERA_FPS",
    "DEFAULT_CAMERA_FOURCC",
    "DEFAULT_CAMERA_SIZE",
    "DEFAULT_INPUT_SIZE",
    "LatestFrameCapture",
    "SampleStats",
    "prepare_frame",
    "stream_camera",
]
