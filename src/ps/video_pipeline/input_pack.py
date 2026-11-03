"""BGR24 to FPGA RGBX32 input packing backends."""

from __future__ import annotations

import ctypes
from pathlib import Path
from typing import Any

import numpy as np


NATIVE_LIBRARY = Path(__file__).resolve().parent / "native" / "libsr_lut_yuv420.so"


class NativeBgrxPacker:
    def __init__(self, path: Path = NATIVE_LIBRARY) -> None:
        self.path = Path(path).resolve()
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        self.library = ctypes.CDLL(str(self.path))
        self.library.sr_lut_bgr_to_rgbx.argtypes = (
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_size_t,
        )
        self.library.sr_lut_bgr_to_rgbx.restype = ctypes.c_int
        self.library.sr_lut_input_pack_backend.argtypes = ()
        self.library.sr_lut_input_pack_backend.restype = ctypes.c_char_p
        encoded_name = self.library.sr_lut_input_pack_backend()
        if not encoded_name:
            raise RuntimeError("native input packer did not report its identity")
        self.name = encoded_name.decode("ascii")

    def pack(self, source: np.ndarray, destination: np.ndarray) -> None:
        result = int(
            self.library.sr_lut_bgr_to_rgbx(
                ctypes.c_void_p(source.ctypes.data),
                int(source.shape[1]),
                int(source.shape[0]),
                int(source.strides[0]),
                ctypes.c_void_p(destination.ctypes.data),
                int(destination.nbytes),
            )
        )
        if result:
            raise RuntimeError(f"native input packing failed with code {result}")


class BgrxInputPacker:
    def __init__(
        self,
        *,
        cv2_module: Any = None,
        native_packer: Any = None,
        native_path: Path = NATIVE_LIBRARY,
    ) -> None:
        self.cv2 = cv2_module
        self.native = native_packer
        if self.native is None:
            try:
                self.native = NativeBgrxPacker(native_path)
            except (FileNotFoundError, OSError, RuntimeError):
                self.native = None
        if self.native is not None:
            self.name = str(getattr(self.native, "name", "native-bgr-rgbx"))
        elif self.cv2 is not None:
            self.name = "opencv-mixchannels"
        else:
            self.name = "numpy-bgr-fallback"

    def pack(self, source: np.ndarray, destination: np.ndarray) -> None:
        bgr = np.asarray(source)
        rgbx = np.asarray(destination)
        expected = (*bgr.shape[:2], 4)
        if (
            bgr.ndim != 3
            or bgr.shape[2] != 3
            or bgr.dtype != np.uint8
            or not bgr.flags.c_contiguous
        ):
            raise ValueError("source must be contiguous uint8 HxWx3 BGR")
        if (
            rgbx.shape != expected
            or rgbx.dtype != np.uint8
            or not rgbx.flags.c_contiguous
        ):
            raise ValueError(f"destination must be contiguous uint8 {expected}")
        if self.native is not None:
            self.native.pack(bgr, rgbx)
            return
        if self.cv2 is not None:
            try:
                self.cv2.mixChannels([bgr], [rgbx], [2, 0, 1, 1, 0, 2])
                return
            except (AttributeError, self.cv2.error):
                self.cv2 = None
                self.name = "numpy-bgr-fallback"
        rgbx[..., 0] = bgr[..., 2]
        rgbx[..., 1] = bgr[..., 1]
        rgbx[..., 2] = bgr[..., 0]


__all__ = ["BgrxInputPacker", "NATIVE_LIBRARY", "NativeBgrxPacker"]
