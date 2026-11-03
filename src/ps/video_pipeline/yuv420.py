"""Video-only RGBX to YUV420P conversion backends."""

from __future__ import annotations

import ctypes
from pathlib import Path
from typing import Any

import numpy as np


BACKENDS = ("auto", "native", "opencv")
NATIVE_LIBRARY = Path(__file__).resolve().parent / "native" / "libsr_lut_yuv420.so"


class NativeYuv420Library:
    """Small ctypes wrapper around the deployment-built ARM conversion library."""

    def __init__(self, path: Path = NATIVE_LIBRARY) -> None:
        self.path = Path(path).resolve()
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        self.library = ctypes.CDLL(str(self.path))
        self.library.sr_lut_rgba_to_i420.argtypes = (
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_size_t,
        )
        self.library.sr_lut_rgba_to_i420.restype = ctypes.c_int
        self.library.sr_lut_yuv420_backend.argtypes = ()
        self.library.sr_lut_yuv420_backend.restype = ctypes.c_char_p
        encoded_name = self.library.sr_lut_yuv420_backend()
        if not encoded_name:
            raise RuntimeError("native YUV420 backend did not report its identity")
        self.name = encoded_name.decode("ascii")

    def convert(self, source: np.ndarray, destination: np.ndarray) -> np.ndarray:
        result = int(
            self.library.sr_lut_rgba_to_i420(
                ctypes.c_void_p(source.ctypes.data),
                int(source.shape[1]),
                int(source.shape[0]),
                int(source.strides[0]),
                ctypes.c_void_p(destination.ctypes.data),
                int(destination.nbytes),
            )
        )
        if result:
            raise RuntimeError(f"native YUV420 conversion failed with code {result}")
        return destination


class Yuv420Converter:
    """Resolve one conversion backend once and reuse it for every video frame."""

    def __init__(
        self,
        *,
        backend: str = "auto",
        cv2_module: Any = None,
        native_library: Any = None,
        native_path: Path = NATIVE_LIBRARY,
    ) -> None:
        if backend not in BACKENDS:
            raise ValueError(f"backend must be one of {BACKENDS}, got {backend}")
        self.cv2 = cv2_module
        self.native = native_library if backend != "opencv" else None
        native_error: BaseException | None = None
        if backend in ("auto", "native") and self.native is None:
            try:
                self.native = NativeYuv420Library(native_path)
            except (FileNotFoundError, OSError, RuntimeError) as error:
                native_error = error
        if self.native is not None:
            self.name = str(getattr(self.native, "name", "native-yuv420"))
        elif backend == "native":
            raise RuntimeError(f"native YUV420 backend is unavailable: {native_error}")
        else:
            if self.cv2 is None:
                import cv2 as cv2_module

                self.cv2 = cv2_module
            self.name = "opencv-rgba-i420"

    def convert(
        self,
        rgbx: np.ndarray,
        destination: np.ndarray | None = None,
    ) -> np.ndarray:
        source, target = _validated_buffers(rgbx, destination)
        if self.native is not None:
            return self.native.convert(source, target)
        assert self.cv2 is not None
        self.cv2.cvtColor(
            source,
            self.cv2.COLOR_RGBA2YUV_I420,
            dst=target.reshape(source.shape[0] * 3 // 2, source.shape[1]),
        )
        return target


def _validated_buffers(
    rgbx: np.ndarray,
    destination: np.ndarray | None,
) -> tuple[np.ndarray, np.ndarray]:
    source = np.asarray(rgbx)
    if (
        source.ndim != 3
        or source.shape[2] != 4
        or source.dtype != np.uint8
        or not source.flags.c_contiguous
    ):
        raise ValueError(
            f"RGBX input must be contiguous uint8 HxWx4, got {source.shape} "
            f"{source.dtype} contiguous={source.flags.c_contiguous}"
        )
    height, width = source.shape[:2]
    if width % 2 or height % 2:
        raise ValueError("YUV420P requires even output dimensions")
    expected = width * height * 3 // 2
    if destination is None:
        target = np.empty((expected,), dtype=np.uint8)
    else:
        target = np.asarray(destination)
        if (
            target.dtype != np.uint8
            or target.ndim != 1
            or target.size != expected
            or not target.flags.c_contiguous
        ):
            raise ValueError(f"destination must be contiguous uint8[{expected}]")
    return source, target


def rgbx_to_yuv420p(
    rgbx: np.ndarray,
    *,
    cv2_module: Any = None,
    destination: np.ndarray | None = None,
    backend: str = "auto",
    native_library: Any = None,
) -> np.ndarray:
    """Convert RGBX bytes to packed planar YUV420P without touching FPGA input."""
    return Yuv420Converter(
        backend=backend,
        cv2_module=cv2_module,
        native_library=native_library,
    ).convert(rgbx, destination)


__all__ = [
    "BACKENDS",
    "NATIVE_LIBRARY",
    "NativeYuv420Library",
    "Yuv420Converter",
    "rgbx_to_yuv420p",
]
