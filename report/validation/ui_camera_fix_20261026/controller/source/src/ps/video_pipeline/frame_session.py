"""Reusable DMA buffers for frame-by-frame video processing on PYNQ-Z2."""

from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Any, Callable, Optional

import numpy as np

from sr_lut_x4_driver import CONTROL_SOFT_RESET
from sr_lut_x4_driver import CONTROL_START
from sr_lut_x4_driver import DriverError
from sr_lut_x4_driver import HardwareMetrics
from sr_lut_x4_driver import MAX_FRAME_HEIGHT
from sr_lut_x4_driver import MAX_FRAME_WIDTH
from sr_lut_x4_driver import MIN_FRAME_HEIGHT
from sr_lut_x4_driver import MIN_FRAME_WIDTH
from sr_lut_x4_driver import REG_CONTROL
from sr_lut_x4_driver import REG_ERROR_CODE
from sr_lut_x4_driver import REG_FRAME_HEIGHT
from sr_lut_x4_driver import REG_FRAME_WIDTH
from sr_lut_x4_driver import REG_INPUT_WORDS
from sr_lut_x4_driver import REG_OUTPUT_WORDS
from sr_lut_x4_driver import REG_OUTPUT_MODE
from sr_lut_x4_driver import REG_OUTPUT_CRC32
from sr_lut_x4_driver import REG_STATUS
from sr_lut_x4_driver import SCALE
from sr_lut_x4_driver import STATUS_DONE
from sr_lut_x4_driver import STATUS_ERROR
from sr_lut_x4_driver import OUTPUT_MODE_MB420
from sr_lut_x4_driver import OUTPUT_MODE_RGBX
from sr_lut_x4_driver import find_control
from sr_lut_x4_driver import find_dma
from sr_lut_x4_driver import read_hardware_metrics
from sr_lut_x4_driver import wait_idle
from video_pipeline.input_pack import BgrxInputPacker


TOP_BYTE_VALIDATION_MODES = ("full", "sampled", "off")
BGR_CONVERTERS = ("opencv", "mixchannels", "numpy")
OUTPUT_MODES = ("rgbx", "mb420")
OUTPUT_BUFFER_POLICIES = ("default", "acp-cacheable")


@dataclass(frozen=True)
class ReusableFrameResult:
    """One frame result with host stages separated from PL counters."""

    image: np.ndarray
    pack_seconds: float
    dma_and_pl_seconds: float
    unpack_seconds: float
    total_seconds: float
    hardware_metrics: Optional[HardwareMetrics]
    sync_from_device_seconds: float = 0.0
    output_validation_seconds: float = 0.0
    color_convert_seconds: float = 0.0
    output_validation_words: int = 0
    output_validation_full_frame: bool = False
    bgr_converter: str = "not-applicable"
    hardware_status: int = 0
    hardware_error_code: int = 0
    input_word_count: int = 0
    output_word_count: int = 0
    hardware_crc32: Optional[int] = None


def _default_allocator() -> Callable[..., Any]:
    try:
        from pynq import allocate
    except ImportError as exc:
        raise DriverError("PYNQ allocate() is required on the board") from exc
    return allocate


def _sync_to_device(buffer: Any) -> None:
    method = getattr(buffer, "sync_to_device", None)
    if not callable(method):
        method = getattr(buffer, "flush", None)
    if callable(method):
        method()


def _sync_from_device(buffer: Any) -> None:
    method = getattr(buffer, "sync_from_device", None)
    if not callable(method):
        method = getattr(buffer, "invalidate", None)
    if callable(method):
        method()


def _free_buffer(buffer: Any) -> None:
    method = getattr(buffer, "freebuffer", None)
    if callable(method):
        method()


def _buffer_flag(buffer: Any, name: str) -> Optional[bool]:
    value = getattr(buffer, name, None)
    if value is None:
        return None
    return bool(value)


def _allocate_buffer(
    allocator: Callable[..., Any],
    *,
    shape: tuple[int, ...],
    dtype: Any,
    cacheable: bool,
) -> Any:
    if not cacheable:
        try:
            return allocator(shape=shape, dtype=dtype, cacheable=False)
        except TypeError:
            return allocator(shape=shape, dtype=dtype)
    try:
        return allocator(shape=shape, dtype=dtype, cacheable=True)
    except TypeError as exc:
        raise DriverError(
            "The ACP media output requires an allocator that accepts "
            "cacheable=True"
        ) from exc


def _stop_dma(dma: Any) -> None:
    for name in ("sendchannel", "recvchannel"):
        channel = getattr(dma, name, None)
        method = getattr(channel, "stop", None)
        if callable(method):
            method()


def _wait_channel(channel: Any, timeout_seconds: float) -> None:
    """Use DMA idle polling when available and retain a portable fallback."""

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    try:
        has_idle = isinstance(channel.idle, (bool, np.bool_))
    except (AttributeError, RuntimeError):
        has_idle = False

    wait_method = getattr(channel, "wait", None)
    if has_idle:
        deadline = time.perf_counter() + timeout_seconds
        while not bool(channel.idle):
            if time.perf_counter() >= deadline:
                raise TimeoutError(
                    f"DMA channel did not become idle within {timeout_seconds:.3f}s"
                )
            time.sleep(0.0001)
        if callable(wait_method):
            wait_method()
        return

    if not callable(wait_method):
        raise DriverError("DMA channel exposes neither idle nor wait()")

    errors: list[BaseException] = []
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
        raise TimeoutError(
            f"DMA channel did not finish within {timeout_seconds:.3f}s"
        )
    if errors:
        raise errors[0]


def _validate_dimensions(width: int, height: int) -> tuple[int, int]:
    width = int(width)
    height = int(height)
    if not MIN_FRAME_WIDTH <= width <= MAX_FRAME_WIDTH:
        raise ValueError(
            f"width must be in {MIN_FRAME_WIDTH}..{MAX_FRAME_WIDTH}, got {width}"
        )
    if not MIN_FRAME_HEIGHT <= height <= MAX_FRAME_HEIGHT:
        raise ValueError(
            f"height must be in {MIN_FRAME_HEIGHT}..{MAX_FRAME_HEIGHT}, got {height}"
        )
    return width, height


class ReusableFrameSession:
    """Run fixed-size frames without allocating DMA buffers per frame."""

    def __init__(
        self,
        overlay: Any,
        width: int,
        height: int,
        *,
        allocator: Optional[Callable[..., Any]] = None,
        input_allocator: Optional[Callable[..., Any]] = None,
        output_allocator: Optional[Callable[..., Any]] = None,
        timeout_seconds: float = 10.0,
        verify_top_byte: Optional[bool] = None,
        top_byte_validation: str = "full",
        validation_sample_words: int = 4096,
        validation_full_interval: int = 30,
        bgr_converter: str = "opencv",
        buffer_count: int = 1,
        output_mode: str = "rgbx",
        output_buffer_policy: str = "default",
        scale: int = SCALE,
        external_output_buffers=None,
    ) -> None:
        self.width, self.height = _validate_dimensions(width, height)
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if allocator is None:
            allocator = _default_allocator()
        if input_allocator is None:
            input_allocator = allocator
        if output_allocator is None:
            output_allocator = allocator

        self.overlay = overlay
        self.dma = find_dma(overlay)
        self.control = find_control(overlay)
        if not hasattr(self.dma, "sendchannel") or not hasattr(
            self.dma, "recvchannel"
        ):
            raise DriverError("AXI DMA must expose sendchannel and recvchannel")

        if verify_top_byte is not None:
            top_byte_validation = "full" if verify_top_byte else "off"
        if top_byte_validation not in TOP_BYTE_VALIDATION_MODES:
            raise ValueError(
                "top_byte_validation must be one of "
                f"{TOP_BYTE_VALIDATION_MODES}, got {top_byte_validation}"
            )
        if validation_sample_words < 1:
            raise ValueError("validation_sample_words must be positive")
        if validation_full_interval < 1:
            raise ValueError("validation_full_interval must be positive")
        if bgr_converter not in BGR_CONVERTERS:
            raise ValueError(
                f"bgr_converter must be one of {BGR_CONVERTERS}, "
                f"got {bgr_converter}"
            )
        if isinstance(buffer_count, bool) or int(buffer_count) != buffer_count:
            raise ValueError("buffer_count must be an integer")
        if not 1 <= int(buffer_count) <= 8:
            raise ValueError("buffer_count must be in 1..8")
        if output_mode not in OUTPUT_MODES:
            raise ValueError(
                f"output_mode must be one of {OUTPUT_MODES}, got {output_mode}"
            )
        if output_buffer_policy not in OUTPUT_BUFFER_POLICIES:
            raise ValueError(
                "output_buffer_policy must be one of "
                f"{OUTPUT_BUFFER_POLICIES}, got {output_buffer_policy}"
            )

        if int(scale) not in (2, 4):
            raise ValueError("scale must be 2 or 4")
        self.scale = int(scale)
        self.timeout_seconds = float(timeout_seconds)
        self.top_byte_validation = top_byte_validation
        self.validation_sample_words = int(validation_sample_words)
        self.validation_full_interval = int(validation_full_interval)
        self.bgr_converter = bgr_converter
        self.selected_bgr_converter = "not-used"
        self.input_pixels = self.width * self.height
        self.output_pixels = self.input_pixels * self.scale * self.scale
        self.output_mode = output_mode
        self.output_buffer_policy = output_buffer_policy
        self.output_transfer_coherency = (
            "acp-hardware"
            if output_buffer_policy == "acp-cacheable"
            else "software-sync"
        )
        try:
            import cv2 as input_cv2
        except ImportError:
            input_cv2 = None
        self._input_cv2 = input_cv2
        self._bgrx_input_packer = BgrxInputPacker(cv2_module=input_cv2)
        self.selected_input_packer = "not-used"
        self.output_words = (
            self.output_pixels
            if output_mode == "rgbx"
            else self.input_pixels * self.scale * self.scale * 3 // 8
        )
        if output_mode == "mb420" and self.scale != 4:
            raise ValueError("native x2 video must use RGBX output mode")
        self.output_width = self.width * self.scale
        self.output_height = self.height * self.scale
        self.buffer_count = int(buffer_count)
        self.input_buffers: list[Any] = []
        self.output_buffers: list[Any] = []
        self.owns_output_buffers = external_output_buffers is None
        if external_output_buffers is not None and len(external_output_buffers) != self.buffer_count:
            raise ValueError('External output count must match buffer_count')
        try:
            for _ in range(self.buffer_count):
                input_buffer = _allocate_buffer(
                    input_allocator,
                    shape=(self.input_pixels,),
                    dtype=np.uint32,
                    cacheable=False,
                )
                np.asarray(input_buffer).fill(0)
                self.input_buffers.append(input_buffer)
            for index in range(self.buffer_count):
                output_buffer = (_allocate_buffer(
                    output_allocator,
                    shape=(self.output_words,),
                    dtype=np.uint32,
                    cacheable=output_buffer_policy == "acp-cacheable",
                ) if external_output_buffers is None else external_output_buffers[index])
                if (np.asarray(output_buffer).nbytes != self.output_words*4
                        or not np.asarray(output_buffer).flags.c_contiguous):
                    raise DriverError('External output size/contiguity mismatch')
                self.output_buffers.append(output_buffer)
                if (
                    output_buffer_policy == "acp-cacheable"
                    and _buffer_flag(output_buffer, "cacheable") is not True
                ):
                    raise DriverError(
                        "The ACP media output allocator did not return a "
                        "cacheable PynqBuffer"
                    )
        except BaseException:
            for buffer in self.input_buffers:
                _free_buffer(buffer)
            if self.owns_output_buffers:
                for buffer in self.output_buffers:
                    _free_buffer(buffer)
            raise
        # Backward-compatible aliases for code that inspects a one-buffer session.
        self.input_buffer = self.input_buffers[0]
        self.output_buffer = self.output_buffers[0]
        self.frame_index = 0
        self.closed = False

        try:
            wait_idle(self.control, self.timeout_seconds)
            self.control.write(REG_FRAME_WIDTH, self.width)
            self.control.write(REG_FRAME_HEIGHT, self.height)
            self.control.write(
                REG_OUTPUT_MODE,
                OUTPUT_MODE_RGBX if output_mode == "rgbx" else OUTPUT_MODE_MB420,
            )
        except BaseException:
            self.close()
            raise

    def __enter__(self) -> "ReusableFrameSession":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def close(self) -> None:
        if self.closed:
            return
        for buffer in getattr(self, "input_buffers", []):
            _free_buffer(buffer)
        if self.owns_output_buffers:
            for buffer in getattr(self, "output_buffers", []):
                _free_buffer(buffer)
        self.closed = True

    def buffer_policy_report(self) -> dict[str, Any]:
        """Describe the DMA mappings used by the active frame session."""

        input_buffer = self.input_buffers[0]
        output_buffer = self.output_buffers[0]
        return {
            "policy": self.output_buffer_policy,
            "input": {
                "allocation_cacheable_requested": False,
                "cacheable": _buffer_flag(input_buffer, "cacheable"),
                "pynq_cacheable_property": _buffer_flag(
                    input_buffer, "cacheable"
                ),
                "pynq_cacheable_property_semantics": "not-coherent",
                "coherent": _buffer_flag(input_buffer, "coherent"),
                "packer": self.selected_input_packer,
            },
            "output": {
                "allocation_cacheable_requested": (
                    self.output_buffer_policy == "acp-cacheable"
                ),
                "cacheable": _buffer_flag(output_buffer, "cacheable"),
                "pynq_cacheable_property": _buffer_flag(
                    output_buffer, "cacheable"
                ),
                "pynq_cacheable_property_semantics": "not-coherent",
                "coherent": _buffer_flag(output_buffer, "coherent"),
                "transfer_coherency": self.output_transfer_coherency,
                "explicit_sync_from_device": (
                    self.output_transfer_coherency != "acp-hardware"
                ),
            },
            "buffer_count": self.buffer_count,
        }

    def _recover(self) -> None:
        _stop_dma(self.dma)
        try:
            self.control.write(REG_CONTROL, CONTROL_SOFT_RESET)
        except BaseException:
            pass

    def _wait_transfer(self, channel) -> None:
        _wait_channel(channel, self.timeout_seconds)

    def _check_frame(self, frame: np.ndarray) -> np.ndarray:
        array = np.asarray(frame)
        if array.shape != (self.height, self.width, 3):
            raise ValueError(
                "frame geometry changed: "
                f"expected {(self.height, self.width, 3)}, got {array.shape}"
            )
        if array.dtype != np.uint8:
            raise ValueError(f"frame dtype must be uint8, got {array.dtype}")
        return array

    def _pack(self, frame: np.ndarray, order: str, input_buffer: Any) -> None:
        target_words = np.asarray(input_buffer)
        target_bytes = target_words.view(np.uint8).reshape(
            self.height, self.width, 4
        )
        if order == "rgb":
            target_bytes[..., :3] = frame
            self.selected_input_packer = "numpy-rgb-copy"
        elif order == "bgr":
            self._bgrx_input_packer.pack(frame, target_bytes)
            self.selected_input_packer = self._bgrx_input_packer.name
        else:
            raise ValueError(f"unsupported channel order: {order}")

    def _unpack(
        self,
        output_buffer: Any,
        order: str,
        destination: Optional[np.ndarray] = None,
        *,
        zero_copy: bool = False,
    ) -> tuple[np.ndarray, float, float, int, bool, str]:
        output_words = np.asarray(output_buffer)
        source = output_words.view(np.uint8).reshape(
            self.output_height, self.output_width, 4
        )
        validation_started = time.perf_counter()
        validation_words = 0
        validation_full_frame = False
        if self.top_byte_validation != "off":
            alpha_bytes = output_words.view(np.uint8)[3::4]
            validation_full_frame = (
                self.top_byte_validation == "full"
                or self.frame_index % self.validation_full_interval == 0
            )
            if validation_full_frame:
                invalid_top_byte = bool(np.any(alpha_bytes))
                validation_words = self.output_pixels
            else:
                stride = max(
                    1, self.output_pixels // self.validation_sample_words
                )
                invalid_top_byte = bool(np.any(alpha_bytes[::stride]))
                if alpha_bytes.size:
                    invalid_top_byte = invalid_top_byte or bool(alpha_bytes[-1])
                validation_words = min(
                    self.output_pixels,
                    (self.output_pixels + stride - 1) // stride + 1,
                )
            if invalid_top_byte:
                raise DriverError("output DMA word has a non-zero top byte")
        validation_finished = time.perf_counter()
        if zero_copy:
            if order != "rgbx" or destination is not None:
                raise ValueError(
                    "zero_copy is only valid for an RGBX view without a destination"
                )
            image = source
        elif destination is None:
            channels = 4 if order == "rgbx" else 3
            image = np.empty(
                (self.output_height, self.output_width, channels), dtype=np.uint8
            )
        else:
            image = np.asarray(destination)
            expected_shape = (
                self.output_height,
                self.output_width,
                4 if order == "rgbx" else 3,
            )
            if image.shape != expected_shape or image.dtype != np.uint8:
                raise ValueError(
                    f"output destination must be uint8 {expected_shape}, got "
                    f"{image.dtype} {image.shape}"
                )
            if not image.flags.c_contiguous:
                raise ValueError("output destination must be C-contiguous")

        convert_started = time.perf_counter()
        if zero_copy:
            selected_converter = "rgbx-zero-copy"
        elif order == "rgb":
            image[...] = source[..., :3]
            selected_converter = "rgb-copy"
        elif order == "rgbx":
            image[...] = source
            selected_converter = "rgbx-copy"
        elif order == "bgr":
            selected_converter = self.bgr_converter
            if self.bgr_converter != "numpy":
                try:
                    import cv2
                except ImportError:
                    selected_converter = "numpy-fallback"
                else:
                    try:
                        if self.bgr_converter == "mixchannels":
                            cv2.mixChannels(
                                [source], [image], [2, 0, 1, 1, 0, 2]
                            )
                        else:
                            cv2.cvtColor(source, cv2.COLOR_RGBA2BGR, image)
                    except (AttributeError, cv2.error):
                        selected_converter = "numpy-fallback"
            if selected_converter in ("numpy", "numpy-fallback"):
                image[..., 0] = source[..., 2]
                image[..., 1] = source[..., 1]
                image[..., 2] = source[..., 0]
        else:
            raise ValueError(f"unsupported channel order: {order}")
        self.selected_bgr_converter = selected_converter
        convert_finished = time.perf_counter()
        return (
            image,
            validation_finished - validation_started,
            convert_finished - convert_started,
            validation_words,
            validation_full_frame,
            selected_converter,
        )

    def run_rgb(
        self,
        frame: np.ndarray,
        output: Optional[np.ndarray] = None,
        *,
        buffer_index: int = 0,
    ) -> ReusableFrameResult:
        return self._run(frame, "rgb", output, buffer_index=buffer_index)

    def run_bgr(
        self,
        frame: np.ndarray,
        output: Optional[np.ndarray] = None,
        *,
        buffer_index: int = 0,
    ) -> ReusableFrameResult:
        return self._run(frame, "bgr", output, buffer_index=buffer_index)

    def run_rgbx(
        self,
        frame: np.ndarray,
        output: Optional[np.ndarray] = None,
        *,
        buffer_index: int = 0,
        zero_copy: bool = False,
    ) -> ReusableFrameResult:
        """Run a BGR video frame and retain the DMA RGBX32 byte layout."""

        return self._run(
            frame,
            "bgr",
            output,
            output_order="rgbx",
            buffer_index=buffer_index,
            zero_copy=zero_copy,
        )

    def run_mb420(
        self,
        frame: np.ndarray,
        *,
        buffer_index: int = 0,
        zero_copy: bool = True,
    ) -> ReusableFrameResult:
        """Run one BGR frame and return the FPGA MB420 DMA byte layout."""

        if self.output_mode != "mb420":
            raise RuntimeError("run_mb420 requires output_mode='mb420'")
        if not zero_copy:
            raise ValueError("MB420 is exposed only as a zero-copy DMA byte view")
        return self._run(
            frame,
            "bgr",
            output_order="mb420",
            buffer_index=buffer_index,
            zero_copy=True,
        )

    def _run(
        self,
        frame: np.ndarray,
        order: str,
        output: Optional[np.ndarray] = None,
        *,
        output_order: Optional[str] = None,
        buffer_index: int = 0,
        zero_copy: bool = False,
        overlap: Optional[Callable[[], None]] = None,
    ) -> ReusableFrameResult:
        if self.closed:
            raise RuntimeError("video frame session is closed")
        if (
            isinstance(buffer_index, bool)
            or not 0 <= int(buffer_index) < self.buffer_count
        ):
            raise ValueError(f"buffer_index must be in 0..{self.buffer_count - 1}")
        buffer_index = int(buffer_index)
        input_buffer = self.input_buffers[buffer_index]
        output_buffer = self.output_buffers[buffer_index]
        array = self._check_frame(frame)
        started = time.perf_counter()
        self._pack(array, order, input_buffer)
        _sync_to_device(input_buffer)
        packed = time.perf_counter()

        try:
            wait_idle(self.control, self.timeout_seconds)
            self.dma.recvchannel.transfer(output_buffer)
            self.control.write(REG_CONTROL, CONTROL_START)
            self.dma.sendchannel.transfer(input_buffer)
            # The same owner thread may paint/submit the previous camera
            # result while PL writes a different output allocation. Exceptions
            # still drain/reset DMA through the normal recovery path.
            if overlap is not None:
                overlap()
            self._wait_transfer(self.dma.sendchannel)
            self._wait_transfer(self.dma.recvchannel)

            status = int(self.control.read(REG_STATUS))
            error_code = int(self.control.read(REG_ERROR_CODE))
            if status & STATUS_ERROR or error_code:
                raise DriverError(
                    f"hardware error after frame {self.frame_index}: "
                    f"status=0x{status:08x} error=0x{error_code:08x}"
                )
            if not status & STATUS_DONE:
                raise DriverError(
                    f"hardware did not report DONE after frame {self.frame_index}: "
                    f"status=0x{status:08x}"
                )

            actual_input = int(self.control.read(REG_INPUT_WORDS))
            actual_output = int(self.control.read(REG_OUTPUT_WORDS))
            if (actual_input, actual_output) != (
                self.input_pixels,
                self.output_words,
            ):
                raise DriverError(
                    f"frame {self.frame_index} word count mismatch: "
                    f"input={actual_input}/{self.input_pixels}, "
                    f"output={actual_output}/{self.output_words}"
                )
            hardware_crc32 = (
                int(self.control.read(REG_OUTPUT_CRC32))
                if self.output_mode == "mb420"
                else None
            )
            hardware_metrics = read_hardware_metrics(self.overlay)
        except BaseException:
            self._recover()
            raise

        dma_finished = time.perf_counter()
        sync_started = time.perf_counter()
        if self.output_transfer_coherency != "acp-hardware":
            _sync_from_device(output_buffer)
        sync_finished = time.perf_counter()
        if output_order == "mb420":
            if output is not None or not zero_copy:
                raise ValueError("MB420 requires a zero-copy output view")
            image = np.asarray(output_buffer).view(np.uint8).reshape(-1)
            validation_seconds = 0.0
            convert_seconds = 0.0
            validation_words = 0
            validation_full_frame = False
            selected_converter = "fpga-mb420-zero-copy"
        else:
            (
                image,
                validation_seconds,
                convert_seconds,
                validation_words,
                validation_full_frame,
                selected_converter,
            ) = self._unpack(
                output_buffer,
                output_order or order,
                output,
                zero_copy=zero_copy,
            )
        finished = time.perf_counter()
        self.frame_index += 1
        return ReusableFrameResult(
            image=image,
            pack_seconds=packed - started,
            dma_and_pl_seconds=dma_finished - packed,
            unpack_seconds=finished - dma_finished,
            total_seconds=finished - started,
            hardware_metrics=hardware_metrics,
            sync_from_device_seconds=sync_finished - sync_started,
            output_validation_seconds=validation_seconds,
            color_convert_seconds=convert_seconds,
            output_validation_words=validation_words,
            output_validation_full_frame=validation_full_frame,
            bgr_converter=selected_converter,
            hardware_status=status,
            hardware_error_code=error_code,
            input_word_count=actual_input,
            output_word_count=actual_output,
            hardware_crc32=hardware_crc32,
        )
