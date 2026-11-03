"""Pipelined YUV420P sender for full-frame FPGA video processing."""

from __future__ import annotations

import argparse
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import sys
import threading
import time
from typing import Any, Callable, Optional, Sequence

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
PYNQ_DIR = SCRIPT_DIR.parent
if str(PYNQ_DIR) not in sys.path:
    sys.path.insert(0, str(PYNQ_DIR))

from sr_lut_x4_driver import BITFILE, HWHFILE, SCALE, load_overlay
from video_pipeline.frame_session import OUTPUT_BUFFER_POLICIES, ReusableFrameSession
from video_pipeline.identity import APPLICATION_VERSION, HARDWARE_STAGE, VIDEO_PIPELINE
from video_pipeline.protocol import PIXEL_FORMAT_YUV420P, PIXEL_FORMAT_MB420
from video_pipeline.protocol import SessionHeader
from video_pipeline.runtime_tuning import NetworkIrqAffinity
from video_pipeline.sink import Yuv420FrameSink
from video_pipeline.yuv420 import BACKENDS, Yuv420Converter


RGBX_HANDOFF_MODES = ("zero-copy", "cached-copy")
FPGA_OUTPUT_MODES = ("rgbx", "mb420")
OUTPUT_BUFFER_POLICY_MODES = ("auto",) + OUTPUT_BUFFER_POLICIES


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def finite_summary(values: list[float]) -> dict[str, float]:
    samples = np.asarray(values, dtype=np.float64)
    if samples.size == 0 or not np.all(np.isfinite(samples)):
        raise ValueError("summary requires finite samples")
    return {
        "minimum": float(np.min(samples)),
        "mean": float(np.mean(samples)),
        "p95": float(np.percentile(samples, 95)),
        "p99": float(np.percentile(samples, 99)),
        "maximum": float(np.max(samples)),
    }


def optional_summary(values: Any) -> dict[str, float] | None:
    samples = list(values) if values is not None else []
    return finite_summary(samples) if samples else None


def _available_cpus() -> list[int]:
    getter = getattr(os, "sched_getaffinity", None)
    if not callable(getter):
        return []
    try:
        return sorted(int(item) for item in getter(0))
    except (OSError, TypeError, ValueError):
        return []


def _set_current_thread_affinity(cpu: int | None) -> dict[str, Any]:
    result: dict[str, Any] = {"requested_cpu": cpu, "effective_cpus": None}
    if cpu is None:
        result["status"] = "disabled"
        return result
    setter = getattr(os, "sched_setaffinity", None)
    getter = getattr(os, "sched_getaffinity", None)
    if not callable(setter) or not callable(getter):
        result["status"] = "unsupported"
        return result
    try:
        setter(0, {int(cpu)})
        result["effective_cpus"] = sorted(int(item) for item in getter(0))
        result["status"] = "applied"
    except (OSError, TypeError, ValueError) as exc:
        result["status"] = "failed"
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


class _Prefetcher:
    def __init__(
        self,
        capture: Any,
        cv2_module: Any,
        depth: int,
        repeat_count: int,
        cpu: int | None = None,
    ) -> None:
        self.capture = capture
        self.cv2 = cv2_module
        self.items: queue.Queue[Any] = queue.Queue(maxsize=depth)
        self.remaining_restarts = repeat_count - 1
        self.error: BaseException | None = None
        self.stop_event = threading.Event()
        self.cpu = cpu
        self.affinity: dict[str, Any] | None = None
        self.thread = threading.Thread(target=self._run, name="video-decode", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        try:
            self.affinity = _set_current_thread_affinity(self.cpu)
            while not self.stop_event.is_set():
                ok, frame = self.capture.read()
                if not ok and self.remaining_restarts:
                    if not self.capture.set(self.cv2.CAP_PROP_POS_FRAMES, 0):
                        raise RuntimeError("video decoder could not restart at frame zero")
                    self.remaining_restarts -= 1
                    continue
                while not self.stop_event.is_set():
                    try:
                        self.items.put(frame if ok else None, timeout=0.1)
                        break
                    except queue.Full:
                        continue
                if not ok:
                    return
        except BaseException as exc:
            self.error = exc
            while not self.stop_event.is_set():
                try:
                    self.items.put(None, timeout=0.1)
                    break
                except queue.Full:
                    continue

    def get(self) -> Any:
        item = self.items.get()
        if self.error:
            raise self.error
        return item

    def join(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=10)
        if self.thread.is_alive():
            raise RuntimeError("decode prefetch thread did not stop")


def process_video_yuv420(
    input_path: Path,
    report_path: Path,
    receiver_host: str,
    receiver_port: int,
    *,
    timeout_seconds: float = 30.0,
    queue_depth: int = 3,
    overlay: Any = None,
    overlay_dir: Optional[Path] = None,
    allocator: Optional[Callable[..., Any]] = None,
    cv2_module: Any = None,
    session_factory: Any = ReusableFrameSession,
    sink_factory: Any = Yuv420FrameSink,
    yuv_backend: str = "auto",
    yuv_converter_factory: Any = Yuv420Converter,
    repeat_count: int = 1,
    rgbx_handoff: str = "zero-copy",
    cpu_affinity: bool = False,
    network_irq_affinity: bool = False,
    network_interface: str = "eth0",
    fpga_output: str = "rgbx",
    output_buffer_policy: str = "auto",
    irq_tuner_factory: Any = NetworkIrqAffinity,
    scale: int = SCALE,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> dict[str, Any]:
    input_path = Path(input_path).resolve()
    report_path = Path(report_path).resolve()
    if not input_path.is_file():
        raise FileNotFoundError(input_path)
    if int(scale) not in (2, 4):
        raise ValueError("scale must be 2 or 4")
    scale = int(scale)
    if fpga_output == "mb420" and scale != 4:
        raise ValueError("native x2 video must use RGBX FPGA output")
    if queue_depth < 2:
        raise ValueError("queue_depth must be at least 2")
    if (
        isinstance(repeat_count, bool)
        or int(repeat_count) != repeat_count
        or not 1 <= int(repeat_count) <= 1000
    ):
        raise ValueError("repeat_count must be an integer in 1..1000")
    repeat_count = int(repeat_count)
    if rgbx_handoff not in RGBX_HANDOFF_MODES:
        raise ValueError(
            f"rgbx_handoff must be one of {RGBX_HANDOFF_MODES}, got {rgbx_handoff}"
        )
    if fpga_output not in FPGA_OUTPUT_MODES:
        raise ValueError(
            f"fpga_output must be one of {FPGA_OUTPUT_MODES}, got {fpga_output}"
        )
    if output_buffer_policy not in OUTPUT_BUFFER_POLICY_MODES:
        raise ValueError(
            "output_buffer_policy must be one of "
            f"{OUTPUT_BUFFER_POLICY_MODES}, got {output_buffer_policy}"
        )
    resolved_output_buffer_policy = output_buffer_policy
    if output_buffer_policy == "auto":
        resolved_output_buffer_policy = (
            "acp-cacheable"
            if HARDWARE_STAGE >= 35 and fpga_output == "mb420"
            else "default"
        )
    if network_irq_affinity and not cpu_affinity:
        raise ValueError("network_irq_affinity requires cpu_affinity")
    if cv2_module is None:
        import cv2 as cv2_module
    capture = cv2_module.VideoCapture(str(input_path))
    if not capture.isOpened():
        raise RuntimeError(f"input video cannot be opened: {input_path}")
    report: dict[str, Any] = {
        "schema_version": 2,
        "application": "SR-LUT FPGA video pipeline",
        "application_version": APPLICATION_VERSION,
        "hardware_stage": HARDWARE_STAGE,
        "video_pipeline": VIDEO_PIPELINE,
        "status": "running",
        "input_path": str(input_path),
        "receiver": {"host": receiver_host, "port": int(receiver_port)},
    }
    session = sink = None
    prefetch = None
    converter_thread: threading.Thread | None = None
    sender: threading.Thread | None = None
    rgbx_queue: queue.Queue[Any] | None = None
    send_queue: queue.Queue[Any] | None = None
    stop_workers = threading.Event()
    total_started = time.perf_counter()
    stream_started: float | None = None
    original_main_affinity: set[int] | None = None
    available_cpus = _available_cpus() if cpu_affinity else []
    primary_cpu = available_cpus[0] if available_cpus else None
    secondary_cpu = available_cpus[-1] if len(available_cpus) >= 2 else None
    conversion_cpu = secondary_cpu
    sender_cpu = secondary_cpu
    affinity_report: dict[str, Any] = {
        "enabled": bool(cpu_affinity),
        "available_cpus": available_cpus,
        "role_map": {
            "dma_and_decode": primary_cpu,
            "network_sender": sender_cpu,
            "rgbx_converter": conversion_cpu,
        },
    }
    if cpu_affinity and available_cpus:
        try:
            original_main_affinity = set(os.sched_getaffinity(0))
        except (AttributeError, OSError, TypeError, ValueError):
            original_main_affinity = None
        affinity_report["main"] = _set_current_thread_affinity(primary_cpu)
    irq_tuner = irq_tuner_factory(
        interface=network_interface,
        target_cpu=sender_cpu if network_irq_affinity else None,
    )
    network_irq_report = irq_tuner.apply()
    network_irq_report["requested"] = bool(network_irq_affinity)
    report["runtime_tuning"] = {
        "cpu_affinity": affinity_report,
        "network_irq_affinity": network_irq_report,
    }
    try:
        fps = float(capture.get(cv2_module.CAP_PROP_FPS))
        if not math.isfinite(fps) or fps <= 0:
            raise ValueError(f"invalid input FPS: {fps}")
        fps_fraction = Fraction(fps).limit_denominator(1_000_000)
        declared_frames = int(capture.get(cv2_module.CAP_PROP_FRAME_COUNT))
        total_declared_frames = (
            declared_frames * repeat_count if declared_frames > 0 else -1
        )
        first_ok, first = capture.read()
        if not first_ok or first is None:
            raise RuntimeError("video contains no decodable frames")
        if first.dtype != np.uint8 or first.ndim != 3 or first.shape[2] != 3:
            raise ValueError(f"decoded frame must be uint8 HxWx3, got {first.shape}")
        height, width = first.shape[:2]
        output_width, output_height = width * scale, height * scale
        bytes_per_frame = output_width * output_height * 3 // 2
        if overlay is None:
            overlay = load_overlay(overlay_dir=overlay_dir) if overlay_dir else load_overlay()
        session_options = {
            "allocator": allocator,
            "timeout_seconds": timeout_seconds,
            "top_byte_validation": "off",
            "buffer_count": queue_depth,
            "output_buffer_policy": resolved_output_buffer_policy,
            "scale": scale,
        }
        if fpga_output == "mb420":
            session_options["output_mode"] = "mb420"
        session = session_factory(overlay, width, height, **session_options)
        yuv_converter = (
            yuv_converter_factory(backend=yuv_backend, cv2_module=cv2_module)
            if fpga_output == "rgbx"
            else None
        )
        wire = SessionHeader(
            width,
            height,
            output_width,
            output_height,
            fps_fraction.numerator,
            fps_fraction.denominator,
            total_declared_frames,
            PIXEL_FORMAT_MB420 if fpga_output == "mb420" else PIXEL_FORMAT_YUV420P,
            bytes_per_frame,
        )
        sink = sink_factory(receiver_host, receiver_port, wire, connect_timeout_seconds=timeout_seconds, io_timeout_seconds=timeout_seconds)
        prefetch = _Prefetcher(
            capture,
            cv2_module,
            queue_depth,
            repeat_count,
            cpu=primary_cpu,
        )
        first_pending = first
        rgbx_queue = queue.Queue(maxsize=queue_depth)
        send_queue = queue.Queue(maxsize=queue_depth)
        free_dma_slots: queue.Queue[int] = queue.Queue(maxsize=queue_depth)
        free_yuv_slots: queue.Queue[int] = queue.Queue(maxsize=queue_depth)
        free_cached_slots: queue.Queue[int] | None = None
        cached_rgbx_slots: list[np.ndarray] = []
        if fpga_output == "rgbx" and rgbx_handoff == "cached-copy":
            free_cached_slots = queue.Queue(maxsize=queue_depth)
            cached_rgbx_slots = [
                np.empty((output_height, output_width, 4), dtype=np.uint8)
                for _ in range(queue_depth)
            ]
        yuv_slots = (
            [
                np.empty((bytes_per_frame,), dtype=np.uint8)
                for _ in range(queue_depth)
            ]
            if fpga_output == "rgbx"
            else []
        )
        for slot_index in range(queue_depth):
            free_dma_slots.put(slot_index)
            if fpga_output == "rgbx":
                free_yuv_slots.put(slot_index)
            if free_cached_slots is not None:
                free_cached_slots.put(slot_index)
        worker_error: list[BaseException] = []
        send_seconds: list[float] = []
        convert_seconds: list[float] = []
        session_total_seconds: list[float] = []
        pack_seconds: list[float] = []
        dma_seconds: list[float] = []
        sync_from_device_seconds: list[float] = []
        output_validation_seconds: list[float] = []
        output_copy_seconds: list[float] = []
        decode_prefetch_wait: list[float] = []
        pipeline_latency_seconds: list[float] = []
        frame_index = 0

        def raise_worker_error() -> None:
            if worker_error:
                raise worker_error[0]

        def acquire_slot(slots: queue.Queue[int]) -> int:
            while True:
                raise_worker_error()
                if stop_workers.is_set():
                    raise RuntimeError("video pipeline workers stopped")
                try:
                    return slots.get(timeout=0.1)
                except queue.Empty:
                    continue

        def put_work(items: queue.Queue[Any], item: Any) -> None:
            while True:
                raise_worker_error()
                if stop_workers.is_set():
                    raise RuntimeError("video pipeline workers stopped")
                try:
                    items.put(item, timeout=0.1)
                    return
                except queue.Full:
                    continue

        def converter_worker() -> None:
            rgbx_slot: int | None = None
            cached_slot: int | None = None
            yuv_slot: int | None = None
            try:
                affinity_report["converter"] = _set_current_thread_affinity(
                    conversion_cpu
                )
                while not stop_workers.is_set():
                    try:
                        item = rgbx_queue.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    if item is None:
                        put_work(send_queue, None)
                        return
                    index, result, rgbx_slot, cached_slot, frame_started = item
                    yuv_slot = acquire_slot(free_yuv_slots)
                    convert_started = time.perf_counter()
                    assert yuv_converter is not None
                    yuv = yuv_converter.convert(
                        result.image,
                        destination=yuv_slots[yuv_slot],
                    )
                    convert_seconds.append(time.perf_counter() - convert_started)
                    if rgbx_slot is not None:
                        free_dma_slots.put(rgbx_slot)
                        rgbx_slot = None
                    if cached_slot is not None:
                        assert free_cached_slots is not None
                        free_cached_slots.put(cached_slot)
                        cached_slot = None
                    put_work(
                        send_queue,
                        (index, result, yuv, yuv_slot, frame_started, None),
                    )
                    yuv_slot = None
            except BaseException as exc:
                worker_error.append(exc)
                stop_workers.set()
            finally:
                if rgbx_slot is not None:
                    free_dma_slots.put(rgbx_slot)
                if cached_slot is not None:
                    assert free_cached_slots is not None
                    free_cached_slots.put(cached_slot)
                if yuv_slot is not None:
                    free_yuv_slots.put(yuv_slot)

        def sender_worker() -> None:
            yuv_slot: int | None = None
            dma_slot: int | None = None
            try:
                affinity_report["sender"] = _set_current_thread_affinity(
                    sender_cpu
                )
                while not stop_workers.is_set():
                    try:
                        item = send_queue.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    if item is None:
                        return
                    index, result, yuv, yuv_slot, frame_started, dma_slot = item
                    send_started = time.perf_counter()
                    sink.send_frame(
                        index,
                        yuv,
                        hardware_status=result.hardware_status,
                        hardware_error_code=result.hardware_error_code,
                        input_word_count=result.input_word_count,
                        output_word_count=result.output_word_count,
                        payload_crc32=(
                            result.hardware_crc32
                            if fpga_output == "mb420"
                            else None
                        ),
                    )
                    send_seconds.append(time.perf_counter() - send_started)
                    pipeline_latency_seconds.append(
                        time.perf_counter() - frame_started
                    )
                    if yuv_slot is not None:
                        free_yuv_slots.put(yuv_slot)
                        yuv_slot = None
                    if dma_slot is not None:
                        free_dma_slots.put(dma_slot)
                        dma_slot = None
            except BaseException as exc:
                worker_error.append(exc)
                stop_workers.set()
            finally:
                if yuv_slot is not None:
                    free_yuv_slots.put(yuv_slot)
                if dma_slot is not None:
                    free_dma_slots.put(dma_slot)

        converter_thread = threading.Thread(
            target=converter_worker,
            name="video-yuv-converter",
            daemon=True,
        )
        sender = threading.Thread(
            target=sender_worker,
            name="video-yuv-sender",
            daemon=True,
        )
        if fpga_output == "rgbx":
            converter_thread.start()
        sender.start()
        stream_started = time.perf_counter()

        while first_pending is not None:
            raise_worker_error()
            if cancel_check is not None and cancel_check():
                raise InterruptedError("video job cancellation requested")
            slot_index = acquire_slot(free_dma_slots)
            cached_slot_index: int | None = None
            if free_cached_slots is not None:
                cached_slot_index = acquire_slot(free_cached_slots)
            frame_started = time.perf_counter()
            try:
                if fpga_output == "mb420":
                    result = session.run_mb420(
                        first_pending,
                        buffer_index=slot_index,
                    )
                else:
                    result = session.run_rgbx(
                        first_pending,
                        output=(
                            cached_rgbx_slots[cached_slot_index]
                            if cached_slot_index is not None
                            else None
                        ),
                        buffer_index=slot_index,
                        zero_copy=rgbx_handoff == "zero-copy",
                    )
            except BaseException:
                free_dma_slots.put(slot_index)
                if cached_slot_index is not None:
                    assert free_cached_slots is not None
                    free_cached_slots.put(cached_slot_index)
                raise
            pack_seconds.append(result.pack_seconds)
            dma_seconds.append(result.dma_and_pl_seconds)
            sync_from_device_seconds.append(result.sync_from_device_seconds)
            output_validation_seconds.append(result.output_validation_seconds)
            output_copy_seconds.append(result.color_convert_seconds)
            session_total_seconds.append(result.total_seconds)
            if cached_slot_index is not None:
                free_dma_slots.put(slot_index)
                slot_index = -1
            if fpga_output == "mb420":
                put_work(
                    send_queue,
                    (
                        frame_index,
                        result,
                        result.image,
                        None,
                        frame_started,
                        slot_index,
                    ),
                )
            else:
                put_work(
                    rgbx_queue,
                    (
                        frame_index,
                        result,
                        None if slot_index < 0 else slot_index,
                        cached_slot_index,
                        frame_started,
                    ),
                )
            frame_index += 1
            if cancel_check is not None and cancel_check():
                raise InterruptedError("video job cancellation requested")
            wait_started = time.perf_counter()
            first_pending = prefetch.get()
            decode_prefetch_wait.append(time.perf_counter() - wait_started)
            if frame_index % 25 == 0:
                print(f"Video pipeline processed {frame_index} frames", flush=True)
        if fpga_output == "rgbx":
            put_work(rgbx_queue, None)
            converter_thread.join(timeout=timeout_seconds)
            if converter_thread.is_alive():
                raise RuntimeError("video converter thread did not stop")
        else:
            put_work(send_queue, None)
        sender.join(timeout=timeout_seconds)
        if sender.is_alive():
            raise RuntimeError("video sender thread did not stop")
        raise_worker_error()
        network_irq_report = irq_tuner.restore()
        network_irq_report["requested"] = bool(network_irq_affinity)
        report["runtime_tuning"]["network_irq_affinity"] = network_irq_report
        stream_finished = time.perf_counter()
        assert stream_started is not None
        stream_elapsed = stream_finished - stream_started
        total_elapsed = stream_finished - total_started
        affinity_report["decode"] = prefetch.affinity
        identity_dir = Path(overlay_dir).resolve() if overlay_dir else Path(BITFILE).resolve().parent
        identity_stem = f"sr_lut_x{scale}" if overlay_dir else Path(BITFILE).stem
        report.update(
            {
                "status": "pass",
                "input": {
                    "sha256": sha256_file(input_path),
                    "width": width,
                    "height": height,
                    "fps": fps,
                    "source_declared_frames": declared_frames,
                    "repeat_count": repeat_count,
                    "declared_frames": total_declared_frames,
                    "processed_frames": frame_index,
                },
                "output": {
                    "width": output_width,
                    "height": output_height,
                    "pixel_format": "yuv420p",
                    "bytes_per_frame": bytes_per_frame,
                    "scale": scale,
                    "payload_bytes": sink.total_payload_bytes,
                },
                "pipeline": {
                    "queue_depth": queue_depth,
                    "preallocated_dma_input_buffers": queue_depth,
                    "preallocated_dma_output_buffers": queue_depth,
                    "preallocated_cached_rgbx_buffers": len(cached_rgbx_slots),
                    "preallocated_yuv420p_buffers": len(yuv_slots),
                    "fpga_output": fpga_output,
                    "dma_buffers": (
                        session.buffer_policy_report()
                        if callable(getattr(session, "buffer_policy_report", None))
                        else {"policy": resolved_output_buffer_policy}
                    ),
                    "rgbx_handoff": rgbx_handoff,
                    "zero_copy_rgbx": rgbx_handoff == "zero-copy",
                    "cached_rgbx_for_arm_conversion": rgbx_handoff == "cached-copy",
                    "top_byte_validation": "off",
                    "yuv420_backend_requested": yuv_backend,
                    "yuv420_backend": (
                        yuv_converter.name if yuv_converter is not None
                        else "fpga-bt601-mb420"
                    ),
                    "separate_converter_and_sender": fpga_output == "rgbx",
                    "ordered_single_sender": True,
                    "socket_send_buffer_bytes": getattr(
                        sink,
                        "socket_send_buffer_bytes",
                        None,
                    ),
                    "socket_send_buffer_policy": getattr(
                        sink,
                        "socket_send_buffer_policy",
                        None,
                    ),
                    "socket_send_api": getattr(sink, "socket_send_api", None),
                    "frame_header_send_calls": getattr(
                        sink,
                        "frame_header_send_calls",
                        None,
                    ),
                    "payload_send_calls": getattr(
                        sink,
                        "payload_send_calls",
                        None,
                    ),
                    "cpu_affinity": affinity_report,
                    "network_irq_affinity": network_irq_report,
                },
                "frozen_identity": {
                    "bit_sha256": sha256_file(identity_dir / f"{identity_stem}.bit"),
                    "hwh_sha256": sha256_file(identity_dir / f"{identity_stem}.hwh"),
                },
                "performance": {
                    "initialization_seconds": stream_started - total_started,
                    "wall_seconds": stream_elapsed,
                    "stream_wall_seconds": stream_elapsed,
                    "total_wall_seconds": total_elapsed,
                    "end_to_end_fps": frame_index / stream_elapsed,
                    "decode_prefetch_wait_seconds": finite_summary(decode_prefetch_wait),
                    "frame_session_total_seconds": finite_summary(session_total_seconds),
                    "input_pack_and_sync_seconds": finite_summary(pack_seconds),
                    "dma_and_pl_seconds": finite_summary(dma_seconds),
                    "output_sync_from_device_seconds": finite_summary(sync_from_device_seconds),
                    "output_validation_seconds": finite_summary(output_validation_seconds),
                    "cached_rgbx_copy_seconds": finite_summary(output_copy_seconds),
                    "rgbx_to_yuv420p_seconds": optional_summary(convert_seconds),
                    "sender_stage_seconds": finite_summary(send_seconds),
                    "frame_crc32_seconds": optional_summary(
                        getattr(sink, "crc_seconds", None)
                    ),
                    "network_send_seconds": optional_summary(
                        getattr(sink, "send_seconds", None)
                    ),
                    "hardware_crc_frames": int(
                        getattr(sink, "hardware_crc_frames", 0)
                    ),
                    "pipeline_latency_seconds": finite_summary(
                        pipeline_latency_seconds
                    ),
                    "network_payload_megabytes_per_second": (
                        sink.total_payload_bytes / stream_elapsed / 1_000_000.0
                    ),
                },
                "dma_idle_after": {
                    "send": bool(getattr(session.dma.sendchannel, "idle", True)),
                    "receive": bool(getattr(session.dma.recvchannel, "idle", True)),
                },
            }
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
        sink.finish(report); sink = None
        print(f"VIDEO_YUV420P_BOARD_PASS frames={frame_index} fps={frame_index / stream_elapsed:.6f}", flush=True)
        return report
    except BaseException as exc:
        network_irq_report = irq_tuner.restore()
        network_irq_report["requested"] = bool(network_irq_affinity)
        report["runtime_tuning"]["network_irq_affinity"] = network_irq_report
        report["status"] = "fail"
        report["error"] = f"{type(exc).__name__}: {exc}"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        if sink is not None:
            sink.fail()
        raise
    finally:
        irq_tuner.restore()
        stop_workers.set()
        for items in (rgbx_queue, send_queue):
            if items is not None:
                try:
                    items.put_nowait(None)
                except queue.Full:
                    pass
        for worker in (converter_thread, sender):
            if worker is not None and worker.is_alive():
                worker.join(timeout=2)
        if prefetch is not None: prefetch.join()
        capture.release()
        if sink is not None: sink.close()
        if session is not None: session.close()
        if original_main_affinity is not None:
            try:
                os.sched_setaffinity(0, original_main_affinity)
            except (AttributeError, OSError, TypeError, ValueError):
                pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--receiver-host", required=True)
    parser.add_argument("--receiver-port", type=int, default=19037)
    parser.add_argument("--overlay-dir", type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--queue-depth", type=int, default=3)
    parser.add_argument("--yuv-backend", choices=BACKENDS, default="auto")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--scale", type=int, choices=(2, 4), default=SCALE)
    parser.add_argument(
        "--fpga-output",
        choices=FPGA_OUTPUT_MODES,
        default="rgbx",
    )
    parser.add_argument(
        "--output-buffer-policy",
        choices=OUTPUT_BUFFER_POLICY_MODES,
        default="auto",
    )
    parser.add_argument(
        "--rgbx-handoff",
        choices=RGBX_HANDOFF_MODES,
        default="zero-copy",
    )
    parser.add_argument(
        "--cpu-affinity",
        action="store_true",
        help="pin DMA/decode to CPU0 and network/conversion workers to CPU1",
    )
    parser.add_argument(
        "--network-irq-affinity",
        action="store_true",
        help="temporarily route the network IRQ to the sender CPU",
    )
    parser.add_argument("--network-interface", default="eth0")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    process_video_yuv420(
        args.input,
        args.report,
        args.receiver_host,
        args.receiver_port,
        overlay_dir=args.overlay_dir,
        timeout_seconds=args.timeout_seconds,
        queue_depth=args.queue_depth,
        yuv_backend=args.yuv_backend,
        repeat_count=args.repeat,
        scale=args.scale,
        rgbx_handoff=args.rgbx_handoff,
        cpu_affinity=args.cpu_affinity,
        network_irq_affinity=args.network_irq_affinity,
        network_interface=args.network_interface,
        fpga_output=args.fpga_output,
        output_buffer_policy=args.output_buffer_policy,
    )


if __name__ == "__main__":
    main()
