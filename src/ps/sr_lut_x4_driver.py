"""PYNQ-Z2 host-side driver for the dynamic x4 5-bit SR-LUT overlay.

The hardware contract is one 32-bit DMA word per RGB pixel:
bits [7:0] = R, [15:8] = G, [23:16] = B, and [31:24] = 0.
This module keeps the PYNQ import lazy so its transport helpers can be tested
on a normal development PC.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import threading
import time
from typing import Any, Callable, Mapping, Optional

import numpy as np


SCALE = 4
MIN_FRAME_WIDTH = 32
MIN_FRAME_HEIGHT = 32
MAX_FRAME_WIDTH = 480
MAX_FRAME_HEIGHT = 320
PL_CLOCK_HZ = 100_000_000.0

REG_CONTROL = 0x00
REG_FRAME_WIDTH = 0x04
REG_FRAME_HEIGHT = 0x08
REG_STATUS = 0x0C
REG_INPUT_WORDS = 0x10
REG_OUTPUT_WORDS = 0x14
REG_ERROR_CODE = 0x18
REG_OUTPUT_MODE = 0x1C
REG_OUTPUT_CRC32 = 0x30
REG_SCALE_ID = 0x34
OUTPUT_MODE_RGBX = 0
OUTPUT_MODE_MB420 = 1
CONTROL_START = 1 << 0
CONTROL_SOFT_RESET = 1 << 1
STATUS_IDLE = 1 << 0
STATUS_BUSY = 1 << 1
STATUS_DONE = 1 << 2
STATUS_ERROR = 1 << 3

_MODULE_ROOT = Path(__file__).resolve().parent
_PACKAGED_OVERLAY_DIR = _MODULE_ROOT / "overlays" / "media_x4"
_REPOSITORY_OVERLAY_DIR = _MODULE_ROOT.parents[1] / "build" / "artifacts" / "pynq_x4"
OVERLAY_DIR = (
    _PACKAGED_OVERLAY_DIR
    if _PACKAGED_OVERLAY_DIR.is_dir()
    else _REPOSITORY_OVERLAY_DIR
)
BITFILE = OVERLAY_DIR / "sr_lut_x4.bit"
HWHFILE = OVERLAY_DIR / "sr_lut_x4.hwh"


class DriverError(RuntimeError):
    """Raised when the overlay or DMA contract is not usable."""


@dataclass(frozen=True)
class TransferMetrics:
    """Host-observed timings; these are not a substitute for PL counters."""

    prepare_seconds: float
    dma_and_pl_wait_seconds: float
    mm2s_wait_seconds: float
    s2mm_wait_seconds: float
    reconstruct_seconds: float
    total_seconds: float

    @property
    def fps(self) -> float:
        return math.inf if self.total_seconds <= 0 else 1.0 / self.total_seconds

    def as_dict(self) -> Mapping[str, float]:
        return {
            "prepare_seconds": self.prepare_seconds,
            "dma_and_pl_wait_seconds": self.dma_and_pl_wait_seconds,
            "mm2s_wait_seconds": self.mm2s_wait_seconds,
            "s2mm_wait_seconds": self.s2mm_wait_seconds,
            "reconstruct_seconds": self.reconstruct_seconds,
            "total_seconds": self.total_seconds,
            "host_observed_fps": self.fps,
        }


@dataclass(frozen=True)
class HardwareMetrics:
    """Counters measured in the PL clock domain; no Python time is included."""

    frame_cycles: int
    input_backpressure_cycles: int
    output_backpressure_cycles: int
    input_starvation_cycles: int
    clock_hz: float = PL_CLOCK_HZ

    @property
    def frame_seconds(self) -> float:
        return self.frame_cycles / self.clock_hz

    @property
    def fps(self) -> float:
        return math.inf if self.frame_cycles <= 0 else 1.0 / self.frame_seconds

    def as_dict(self) -> Mapping[str, float]:
        return {
            "pl_clock_hz": self.clock_hz,
            "pl_frame_cycles": self.frame_cycles,
            "pl_frame_seconds": self.frame_seconds,
            "pl_hardware_path_fps": self.fps,
            "pl_input_backpressure_cycles": self.input_backpressure_cycles,
            "pl_output_backpressure_cycles": self.output_backpressure_cycles,
            "pl_input_starvation_cycles": self.input_starvation_cycles,
        }


@dataclass(frozen=True)
class FrameResult:
    image: np.ndarray
    raw_output_words: np.ndarray
    metrics: TransferMetrics
    hardware_metrics: Optional[HardwareMetrics] = None


def frame_geometry(rgb: np.ndarray) -> tuple[int, int]:
    """Validate an HxWx3 frame and return (height, width)."""

    array = np.asarray(rgb)
    if array.ndim != 3 or array.shape[2] != 3:
        raise ValueError(f"expected HxWx3 RGB frame, got {array.shape}")
    if array.dtype != np.uint8:
        raise ValueError(f"expected dtype uint8, got {array.dtype}")
    height, width = array.shape[:2]
    if not MIN_FRAME_WIDTH <= width <= MAX_FRAME_WIDTH:
        raise ValueError(f"width must be in {MIN_FRAME_WIDTH}..{MAX_FRAME_WIDTH}")
    if not MIN_FRAME_HEIGHT <= height <= MAX_FRAME_HEIGHT:
        raise ValueError(f"height must be in {MIN_FRAME_HEIGHT}..{MAX_FRAME_HEIGHT}")
    return height, width


def pack_rgb_frame(rgb: np.ndarray) -> np.ndarray:
    """Pack a valid dynamic-size RGB frame into uint32 DMA words."""

    array = np.asarray(rgb)
    height, width = frame_geometry(array)
    array = np.ascontiguousarray(array)
    red = array[..., 0].astype(np.uint32)
    green = array[..., 1].astype(np.uint32)
    blue = array[..., 2].astype(np.uint32)
    words = red | (green << 8) | (blue << 16)
    return np.ascontiguousarray(words.reshape(height * width), dtype=np.uint32)


def unpack_rgb_frame(words: Any, width: int, height: int) -> np.ndarray:
    """Unpack x4 output DMA words and require a zero top byte."""

    raw = np.asarray(words)
    return unpack_rgb_frame_scaled(words, width, height, SCALE)


def unpack_rgb_frame_scaled(
    words: Any, width: int, height: int, scale: int
) -> np.ndarray:
    """Unpack an RGBX frame for the selected fixed Overlay scale."""

    if scale not in (2, 4):
        raise ValueError("scale must be 2 or 4")
    raw = np.asarray(words)
    output_pixels = width * height * scale * scale
    if raw.size != output_pixels:
        raise ValueError(f"expected {output_pixels} output words, got {raw.size}")
    if raw.dtype.kind not in "ui":
        raise ValueError(f"expected an unsigned/integer word buffer, got {raw.dtype}")
    values = raw.astype(np.uint64, copy=False).reshape(-1)
    if np.any(values > 0xFFFFFFFF):
        raise ValueError("output contains a value wider than 32 bits")
    if np.any(values & 0xFF000000):
        raise ValueError("output DMA word has a non-zero top byte")
    packed = values.astype(np.uint32, copy=False)
    rgb = np.empty((output_pixels, 3), dtype=np.uint8)
    rgb[:, 0] = packed & 0xFF
    rgb[:, 1] = (packed >> 8) & 0xFF
    rgb[:, 2] = (packed >> 16) & 0xFF
    return rgb.reshape(height * scale, width * scale, 3)


def overlay_scale(control: Any) -> int:
    """Read the compile-time scale identity exposed by the active wrapper."""

    for address in (REG_SCALE_ID,):
        try:
            value = int(control.read(address))
        except BaseException:
            continue
        if value in (2, 4):
            return value
    return SCALE


def compare_rgb_frames(actual: np.ndarray, expected: np.ndarray) -> Mapping[str, float]:
    """Return exact-pixel, error, and PSNR statistics for two RGB frames."""

    actual_array = np.asarray(actual)
    expected_array = np.asarray(expected)
    if actual_array.shape != expected_array.shape:
        raise ValueError(
            f"image shapes differ: actual={actual_array.shape}, expected={expected_array.shape}"
        )
    if actual_array.ndim != 3 or actual_array.shape[-1] != 3:
        raise ValueError("comparison requires HxWx3 RGB images")
    if actual_array.dtype != np.uint8 or expected_array.dtype != np.uint8:
        raise ValueError("comparison requires uint8 RGB images")
    difference = actual_array.astype(np.int16) - expected_array.astype(np.int16)
    squared_error = difference.astype(np.float64) ** 2
    mse = float(np.mean(squared_error))
    maximum_error = int(np.max(np.abs(difference))) if difference.size else 0
    changed_pixels = int(np.any(difference != 0, axis=-1).sum())
    total_pixels = int(actual_array.shape[0] * actual_array.shape[1])
    psnr = math.inf if mse == 0 else 10.0 * math.log10((255.0 * 255.0) / mse)
    return {
        "total_pixels": total_pixels,
        "exact_pixels": total_pixels - changed_pixels,
        "changed_pixels": changed_pixels,
        "max_abs_error": maximum_error,
        "mse": mse,
        "psnr_db": psnr,
    }


def load_overlay(
    overlay_cls: Optional[Callable[..., Any]] = None,
    overlay_dir: Path = OVERLAY_DIR,
    overlay_stem: Optional[str] = None,
) -> Any:
    """Load a fixed-scale bitstream and its adjacent HWH with PYNQ Overlay."""

    overlay_dir = Path(overlay_dir)
    stem = overlay_stem
    if stem is None:
        stem = "sr_lut_x2" if "x2" in overlay_dir.name.lower() else "sr_lut_x4"
    bitfile = overlay_dir / f"{stem}.bit"
    hwhfile = overlay_dir / f"{stem}.hwh"
    if not bitfile.is_file() or not hwhfile.is_file():
        # Preserve compatibility with the formal x4 directory naming.
        bitfile = overlay_dir / "sr_lut_x4.bit"
        hwhfile = overlay_dir / "sr_lut_x4.hwh"
    if not bitfile.is_file() or not hwhfile.is_file():
        raise DriverError(f"overlay requires both {bitfile} and {hwhfile}")
    if overlay_cls is None:
        try:
            from pynq import Overlay
        except ImportError as exc:
            raise DriverError("PYNQ is required to load an overlay on the board") from exc
        overlay_cls = Overlay
    return overlay_cls(str(bitfile))


def find_dma(overlay: Any) -> Any:
    """Find the DMA IP using the generated HWH name, with a small fallback."""

    for name in ("axi_dma_0", "axi_dma", "dma"):
        dma = getattr(overlay, name, None)
        if dma is not None:
            return dma
    ip_dict = getattr(overlay, "ip_dict", {})
    for name in ip_dict:
        if "axi_dma" in str(name).lower():
            dma = getattr(overlay, str(name), None)
            if dma is not None:
                return dma
    raise DriverError("AXI DMA was not found; expected overlay.axi_dma_0")


def find_control(overlay: Any) -> Any:
    """Find the dynamic frame AXI-Lite register block."""

    for name in ("sr_lut_x4_dynamic_0", "sr_lut_scale_dynamic_0", "sr_lut_dynamic", "sr_lut_x4_0"):
        control = getattr(overlay, name, None)
        if control is not None and callable(getattr(control, "write", None)):
            return control
    raise DriverError("dynamic SR-LUT AXI-Lite control block was not found")


def wait_idle(control: Any, timeout_seconds: float) -> None:
    deadline = time.perf_counter() + timeout_seconds
    while time.perf_counter() < deadline:
        status = int(control.read(REG_STATUS))
        if status & STATUS_ERROR:
            raise DriverError(f"hardware error before start: 0x{control.read(REG_ERROR_CODE):08x}")
        if status & STATUS_IDLE:
            return
        time.sleep(0.0001)
    raise TimeoutError("dynamic SR-LUT did not become idle")


def read_hardware_metrics(
    overlay: Any,
    *,
    clock_hz: float = PL_CLOCK_HZ,
) -> Optional[HardwareMetrics]:
    """Read the latched PL counters exposed by the two AXI GPIO blocks."""

    perf_0 = getattr(overlay, "axi_gpio_perf_0", None)
    perf_1 = getattr(overlay, "axi_gpio_perf_1", None)
    if perf_0 is None or perf_1 is None:
        return None
    if not callable(getattr(perf_0, "read", None)) or not callable(
        getattr(perf_1, "read", None)
    ):
        raise DriverError("performance GPIO blocks do not expose read()")
    if clock_hz <= 0:
        raise ValueError("clock_hz must be positive")
    return HardwareMetrics(
        frame_cycles=int(perf_0.read(0x00)),
        input_backpressure_cycles=int(perf_0.read(0x08)),
        output_backpressure_cycles=int(perf_1.read(0x00)),
        input_starvation_cycles=int(perf_1.read(0x08)),
        clock_hz=float(clock_hz),
    )


def _default_allocator() -> Callable[..., Any]:
    try:
        from pynq import allocate
    except ImportError as exc:
        raise DriverError("PYNQ allocate() is required on the board") from exc
    return allocate


def _sync_buffer(buffer: Any, method_name: str) -> None:
    method = getattr(buffer, method_name, None)
    if callable(method):
        method()


def _free_buffer(buffer: Any) -> None:
    method = getattr(buffer, "freebuffer", None)
    if callable(method):
        method()


def _stop_dma_channels(dma: Any) -> None:
    for channel_name in ("sendchannel", "recvchannel"):
        channel = getattr(dma, channel_name, None)
        stop_method = getattr(channel, "stop", None)
        if callable(stop_method):
            stop_method()


def _wait_with_timeout(channel: Any, timeout_seconds: float) -> None:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    wait_method = getattr(channel, "wait", None)
    if not callable(wait_method):
        raise DriverError("DMA channel has no wait() method")
    errors = []
    finished = threading.Event()

    def wait_target() -> None:
        try:
            wait_method()
        except BaseException as exc:
            errors.append(exc)
        finally:
            finished.set()

    worker = threading.Thread(target=wait_target, daemon=True)
    worker.start()
    if not finished.wait(timeout_seconds):
        raise TimeoutError(f"DMA channel did not finish within {timeout_seconds:.3f}s")
    if errors:
        raise errors[0]


def run_frame(
    rgb: np.ndarray,
    overlay: Any,
    *,
    allocator: Optional[Callable[..., Any]] = None,
    timeout_seconds: float = 10.0,
    clock: Callable[[], float] = time.perf_counter,
    scale: Optional[int] = None,
) -> FrameResult:
    """Run one valid dynamic-size frame through the x4 overlay."""

    if allocator is None:
        allocator = _default_allocator()
    dma = find_dma(overlay)
    control = find_control(overlay)
    if not hasattr(dma, "sendchannel") or not hasattr(dma, "recvchannel"):
        raise DriverError("AXI DMA must expose sendchannel and recvchannel")

    height, width = frame_geometry(rgb)
    active_scale = int(scale) if scale is not None else overlay_scale(control)
    if active_scale not in (2, 4):
        raise DriverError(f"unsupported Overlay scale {active_scale}")
    input_pixels = width * height
    output_pixels = input_pixels * active_scale * active_scale
    start = clock()
    input_words = pack_rgb_frame(rgb)
    input_buffer = None
    output_buffer = None
    try:
        input_buffer = allocator(shape=(input_pixels,), dtype=np.uint32)
        output_buffer = allocator(shape=(output_pixels,), dtype=np.uint32)
        input_buffer[:] = input_words
        _sync_buffer(input_buffer, "flush")
        _sync_buffer(output_buffer, "flush")
        prepared = clock()

        wait_idle(control, timeout_seconds)
        control.write(REG_FRAME_WIDTH, width)
        control.write(REG_FRAME_HEIGHT, height)
        # Stage 34-V retains its output format across frames. Image and ROI
        # calls always restore the byte-exact RGBX contract before START.
        control.write(REG_OUTPUT_MODE, OUTPUT_MODE_RGBX)
        # Arm S2MM before START so the first output word always has somewhere to go.
        dma.recvchannel.transfer(output_buffer)
        dma_started = clock()
        control.write(REG_CONTROL, CONTROL_START)
        dma.sendchannel.transfer(input_buffer)

        send_wait_started = clock()
        _wait_with_timeout(dma.sendchannel, timeout_seconds)
        send_finished = clock()
        recv_wait_started = clock()
        _wait_with_timeout(dma.recvchannel, timeout_seconds)
        recv_finished = clock()

        status = int(control.read(REG_STATUS))
        if status & STATUS_ERROR:
            raise DriverError(f"hardware error: 0x{control.read(REG_ERROR_CODE):08x}")
        if not (status & STATUS_DONE):
            raise DriverError(f"hardware did not report DONE: status=0x{status:08x}")
        actual_input_words = int(control.read(REG_INPUT_WORDS))
        actual_output_words = int(control.read(REG_OUTPUT_WORDS))
        if (actual_input_words, actual_output_words) != (
            input_pixels,
            output_pixels,
        ):
            raise DriverError(
                "hardware DMA length mismatch: "
                f"input={actual_input_words}/{input_pixels}, "
                f"output={actual_output_words}/{output_pixels}"
            )

        hardware_metrics = read_hardware_metrics(overlay)

        _sync_buffer(output_buffer, "invalidate")
        raw_output = np.array(output_buffer, dtype=np.uint32, copy=True)
        reconstruct_started = clock()
        image = unpack_rgb_frame_scaled(raw_output, width, height, active_scale)
        finished = clock()
        metrics = TransferMetrics(
            prepare_seconds=prepared - start,
            dma_and_pl_wait_seconds=recv_finished - dma_started,
            mm2s_wait_seconds=send_finished - send_wait_started,
            s2mm_wait_seconds=recv_finished - recv_wait_started,
            reconstruct_seconds=finished - reconstruct_started,
            total_seconds=finished - start,
        )
        return FrameResult(
            image=image,
            raw_output_words=raw_output,
            metrics=metrics,
            hardware_metrics=hardware_metrics,
        )
    except BaseException:
        _stop_dma_channels(dma)
        try:
            control.write(REG_CONTROL, CONTROL_SOFT_RESET)
        except BaseException:
            pass
        raise
    finally:
        if input_buffer is not None:
            _free_buffer(input_buffer)
        if output_buffer is not None:
            _free_buffer(output_buffer)


__all__ = [
    "BITFILE",
    "HWHFILE",
    "MIN_FRAME_WIDTH",
    "MIN_FRAME_HEIGHT",
    "MAX_FRAME_WIDTH",
    "MAX_FRAME_HEIGHT",
    "REG_CONTROL",
    "REG_FRAME_WIDTH",
    "REG_FRAME_HEIGHT",
    "REG_STATUS",
    "REG_INPUT_WORDS",
    "REG_OUTPUT_WORDS",
    "REG_OUTPUT_MODE",
    "REG_OUTPUT_CRC32",
    "REG_SCALE_ID",
    "REG_ERROR_CODE",
    "CONTROL_START",
    "CONTROL_SOFT_RESET",
    "STATUS_IDLE",
    "OUTPUT_MODE_RGBX",
    "OUTPUT_MODE_MB420",
    "STATUS_BUSY",
    "STATUS_DONE",
    "STATUS_ERROR",
    "PL_CLOCK_HZ",
    "SCALE",
    "DriverError",
    "FrameResult",
    "HardwareMetrics",
    "TransferMetrics",
    "compare_rgb_frames",
    "find_dma",
    "find_control",
    "frame_geometry",
    "load_overlay",
    "pack_rgb_frame",
    "read_hardware_metrics",
    "run_frame",
    "unpack_rgb_frame",
    "unpack_rgb_frame_scaled",
    "overlay_scale",
]
