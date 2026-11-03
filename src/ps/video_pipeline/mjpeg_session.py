"""Reusable DMA session for the fixed 480x270 to 1080P Stage 39 overlay."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Callable, Optional

import numpy as np

from sr_lut_x4_driver import CONTROL_SOFT_RESET, CONTROL_START, DriverError
from sr_lut_x4_driver import OUTPUT_MODE_RGBX
from sr_lut_x4_driver import REG_CONTROL, REG_ERROR_CODE, REG_FRAME_HEIGHT
from sr_lut_x4_driver import REG_FRAME_WIDTH, REG_INPUT_WORDS, REG_OUTPUT_MODE
from sr_lut_x4_driver import REG_OUTPUT_WORDS, REG_STATUS
from sr_lut_x4_driver import STATUS_DONE, STATUS_ERROR, find_control, find_dma
from sr_lut_x4_driver import read_hardware_metrics, wait_idle
from video_pipeline.frame_session import _stop_dma, _sync_to_device, _wait_channel
from video_pipeline.input_pack import BgrxInputPacker
from video_pipeline.mjpeg_protocol import MAX_JPEG_BYTES


INPUT_WIDTH = 480
INPUT_HEIGHT = 270
OUTPUT_WIDTH = 1920
OUTPUT_HEIGHT = 1080
INPUT_PIXELS = INPUT_WIDTH * INPUT_HEIGHT
SR_OUTPUT_WORDS = OUTPUT_WIDTH * OUTPUT_HEIGHT
MJPEG_STATUS_GEOMETRY_ERROR = 1 << 0
MJPEG_STATUS_ENCODER_OVERFLOW = 1 << 1
MJPEG_STATUS_PACKER_OVERFLOW = 1 << 2
MJPEG_STATUS_ERROR_MASK = (
    MJPEG_STATUS_GEOMETRY_ERROR
    | MJPEG_STATUS_ENCODER_OVERFLOW
    | MJPEG_STATUS_PACKER_OVERFLOW
)


@dataclass(frozen=True)
class MjpegFrameResult:
    jpeg: memoryview
    jpeg_length: int
    pack_seconds: float
    dma_and_pl_seconds: float
    sync_seconds: float
    total_seconds: float
    sr_status: int
    sr_error_code: int
    mjpeg_status: int
    input_word_count: int
    sr_output_word_count: int
    hardware_metrics: Any = None


def _default_allocator() -> Callable[..., Any]:
    try:
        from pynq import allocate
    except ImportError as exc:
        raise DriverError("PYNQ allocate() is required on the board") from exc
    return allocate


def _allocate(
    allocator: Callable[..., Any],
    *,
    shape: tuple[int, ...],
    dtype: Any,
    cacheable: bool,
) -> Any:
    try:
        return allocator(shape=shape, dtype=dtype, cacheable=cacheable)
    except TypeError:
        if cacheable:
            raise DriverError("ACP MJPEG output requires cacheable PYNQ buffers")
        return allocator(shape=shape, dtype=dtype)


def _free(buffer: Any) -> None:
    method = getattr(buffer, "freebuffer", None)
    if callable(method):
        method()


def _sync_from_device(buffer: Any) -> None:
    method = getattr(buffer, "sync_from_device", None)
    if not callable(method):
        method = getattr(buffer, "invalidate", None)
    if callable(method):
        method()


def _channel_transferred(channel: Any, dma: Any) -> int:
    value = getattr(channel, "transferred", None)
    if callable(value):
        value = value()
    if value is not None and int(value) > 0:
        return int(value)
    register_map = getattr(dma, "register_map", None)
    if register_map is not None:
        value = getattr(register_map, "S2MM_LENGTH", None)
        if value is not None and int(value) > 0:
            return int(value)
    mmio = getattr(dma, "mmio", None)
    if mmio is not None and callable(getattr(mmio, "read", None)):
        value = int(mmio.read(0x58))
        if value > 0:
            return value
    raise DriverError("AXI DMA did not report the received JPEG byte count")


def read_mjpeg_status(overlay: Any) -> int:
    gpio = getattr(overlay, "axi_gpio_mjpeg_status", None)
    if gpio is None or not callable(getattr(gpio, "read", None)):
        raise DriverError("Stage 39 MJPEG status GPIO was not found")
    return int(gpio.read(0x00)) & 0xFFFFFFFF


class MjpegFrameSession:
    """Process fixed-size BGR frames without reallocating DMA buffers."""

    def __init__(
        self,
        overlay: Any,
        *,
        allocator: Optional[Callable[..., Any]] = None,
        timeout_seconds: float = 10.0,
        buffer_count: int = 2,
        max_jpeg_bytes: int = MAX_JPEG_BYTES,
        coherent_output: bool = True,
    ) -> None:
        if allocator is None:
            allocator = _default_allocator()
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if isinstance(buffer_count, bool) or not 1 <= int(buffer_count) <= 8:
            raise ValueError("buffer_count must be in 1..8")
        if not 64 * 1024 <= int(max_jpeg_bytes) <= MAX_JPEG_BYTES:
            raise ValueError(
                f"max_jpeg_bytes must be in 65536..{MAX_JPEG_BYTES}"
            )
        self.overlay = overlay
        self.dma = find_dma(overlay)
        self.control = find_control(overlay)
        self.timeout_seconds = float(timeout_seconds)
        self.buffer_count = int(buffer_count)
        self.max_jpeg_bytes = int(max_jpeg_bytes)
        self.coherent_output = bool(coherent_output)
        self.input_buffers: list[Any] = []
        self.output_buffers: list[Any] = []
        self.frame_index = 0
        self.closed = False
        self.packer = BgrxInputPacker()
        try:
            for _ in range(self.buffer_count):
                self.input_buffers.append(
                    _allocate(
                        allocator,
                        shape=(INPUT_PIXELS,),
                        dtype=np.uint32,
                        cacheable=False,
                    )
                )
                self.output_buffers.append(
                    _allocate(
                        allocator,
                        shape=(self.max_jpeg_bytes,),
                        dtype=np.uint8,
                        cacheable=self.coherent_output,
                    )
                )
        except BaseException:
            self.close()
            raise
        wait_idle(self.control, self.timeout_seconds)
        self.control.write(REG_FRAME_WIDTH, INPUT_WIDTH)
        self.control.write(REG_FRAME_HEIGHT, INPUT_HEIGHT)
        self.control.write(REG_OUTPUT_MODE, OUTPUT_MODE_RGBX)

    def __enter__(self) -> "MjpegFrameSession":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def close(self) -> None:
        if self.closed:
            return
        for buffer in self.input_buffers:
            _free(buffer)
        for buffer in self.output_buffers:
            _free(buffer)
        self.closed = True

    def _recover(self) -> None:
        _stop_dma(self.dma)
        try:
            self.control.write(REG_CONTROL, CONTROL_SOFT_RESET)
        except BaseException:
            pass

    def run_bgr(self, frame: np.ndarray, *, buffer_index: int = 0) -> MjpegFrameResult:
        if self.closed:
            raise RuntimeError("MJPEG frame session is closed")
        if isinstance(buffer_index, bool) or not 0 <= int(buffer_index) < self.buffer_count:
            raise ValueError(f"buffer_index must be in 0..{self.buffer_count - 1}")
        array = np.asarray(frame)
        if array.shape != (INPUT_HEIGHT, INPUT_WIDTH, 3) or array.dtype != np.uint8:
            raise ValueError(
                "Stage 39 frame must be uint8 BGR with shape (270, 480, 3)"
            )
        index = int(buffer_index)
        input_buffer = self.input_buffers[index]
        output_buffer = self.output_buffers[index]
        started = time.perf_counter()
        target = np.asarray(input_buffer).view(np.uint8).reshape(
            INPUT_HEIGHT, INPUT_WIDTH, 4
        )
        self.packer.pack(array, target)
        _sync_to_device(input_buffer)
        packed = time.perf_counter()
        try:
            wait_idle(self.control, self.timeout_seconds)
            self.dma.recvchannel.transfer(
                output_buffer, nbytes=self.max_jpeg_bytes
            )
            self.control.write(REG_CONTROL, CONTROL_START)
            self.dma.sendchannel.transfer(input_buffer)
            _wait_channel(self.dma.sendchannel, self.timeout_seconds)
            _wait_channel(self.dma.recvchannel, self.timeout_seconds)
            dma_finished = time.perf_counter()

            sr_status = int(self.control.read(REG_STATUS))
            sr_error = int(self.control.read(REG_ERROR_CODE))
            mjpeg_status = read_mjpeg_status(self.overlay)
            if sr_status & STATUS_ERROR or sr_error:
                raise DriverError(
                    f"SR-LUT failed: status=0x{sr_status:08x} "
                    f"error=0x{sr_error:08x}"
                )
            if not sr_status & STATUS_DONE:
                raise DriverError(f"SR-LUT did not report DONE: 0x{sr_status:08x}")
            if mjpeg_status & MJPEG_STATUS_ERROR_MASK:
                raise DriverError(f"MJPEG hardware failed: 0x{mjpeg_status:08x}")
            input_words = int(self.control.read(REG_INPUT_WORDS))
            output_words = int(self.control.read(REG_OUTPUT_WORDS))
            if (input_words, output_words) != (INPUT_PIXELS, SR_OUTPUT_WORDS):
                raise DriverError(
                    "SR-LUT word count mismatch: "
                    f"input={input_words}/{INPUT_PIXELS}, "
                    f"output={output_words}/{SR_OUTPUT_WORDS}"
                )
            # The status GPIO carries the exact accepted byte count in bits
            # [31:3]. Fall back to a DMA-side count only for simulation stubs.
            reported_jpeg_length = mjpeg_status >> 3
            jpeg_length = (
                reported_jpeg_length
                if reported_jpeg_length
                else _channel_transferred(self.dma.recvchannel, self.dma)
            )
            if not 4 <= jpeg_length <= self.max_jpeg_bytes:
                raise DriverError(f"invalid JPEG DMA length {jpeg_length}")
            hardware_metrics = read_hardware_metrics(self.overlay)
        except BaseException:
            self._recover()
            raise

        sync_started = time.perf_counter()
        if not self.coherent_output:
            _sync_from_device(output_buffer)
        sync_finished = time.perf_counter()
        jpeg = memoryview(np.asarray(output_buffer)).cast("B")[:jpeg_length]
        if bytes(jpeg[:2]) != b"\xff\xd8" or bytes(jpeg[-2:]) != b"\xff\xd9":
            self._recover()
            raise DriverError("DMA payload is not a complete JPEG SOI-to-EOI frame")
        finished = time.perf_counter()
        self.frame_index += 1
        return MjpegFrameResult(
            jpeg=jpeg,
            jpeg_length=jpeg_length,
            pack_seconds=packed - started,
            dma_and_pl_seconds=dma_finished - packed,
            sync_seconds=sync_finished - sync_started,
            total_seconds=finished - started,
            sr_status=sr_status,
            sr_error_code=sr_error,
            mjpeg_status=mjpeg_status,
            input_word_count=input_words,
            sr_output_word_count=output_words,
            hardware_metrics=hardware_metrics,
        )


__all__ = [
    "INPUT_HEIGHT",
    "INPUT_WIDTH",
    "MJPEG_STATUS_ERROR_MASK",
    "MjpegFrameResult",
    "MjpegFrameSession",
    "OUTPUT_HEIGHT",
    "OUTPUT_WIDTH",
    "read_mjpeg_status",
]
