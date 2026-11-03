"""Convert the Stage 34-V macroblock stream into standard planar I420."""

from __future__ import annotations

from typing import Optional

import numpy as np


def mb420_to_i420(
    source: bytes | bytearray | memoryview | np.ndarray,
    width: int,
    height: int,
    destination: Optional[np.ndarray | bytearray] = None,
) -> np.ndarray:
    """Reorder one MB420 frame without changing any Y, U, or V samples."""

    width = int(width)
    height = int(height)
    if width <= 0 or height <= 0 or width % 4 or height % 2:
        raise ValueError("MB420 width must be divisible by four and height by two")
    expected = width * height * 3 // 2
    source_view = np.frombuffer(source, dtype=np.uint8)
    if source_view.size != expected:
        raise ValueError(
            f"MB420 source length mismatch: {source_view.size}/{expected}"
        )
    if destination is None:
        target = np.empty(expected, dtype=np.uint8)
    else:
        target = np.asarray(destination).view(np.uint8).reshape(-1)
        if target.size != expected or not target.flags.c_contiguous:
            raise ValueError(
                f"I420 destination must be {expected} contiguous uint8 bytes"
            )

    records = source_view.reshape(height // 2, width // 4, 12)
    y_size = width * height
    chroma_size = y_size // 4
    y_plane = target[:y_size].reshape(height, width)
    u_plane = target[y_size : y_size + chroma_size].reshape(
        height // 2, width // 2
    )
    v_plane = target[y_size + chroma_size :].reshape(
        height // 2, width // 2
    )

    y_plane[0::2, 0::4] = records[:, :, 0]
    y_plane[0::2, 1::4] = records[:, :, 1]
    y_plane[1::2, 0::4] = records[:, :, 2]
    y_plane[1::2, 1::4] = records[:, :, 3]
    u_plane[:, 0::2] = records[:, :, 4]
    v_plane[:, 0::2] = records[:, :, 5]
    y_plane[0::2, 2::4] = records[:, :, 6]
    y_plane[0::2, 3::4] = records[:, :, 7]
    y_plane[1::2, 2::4] = records[:, :, 8]
    y_plane[1::2, 3::4] = records[:, :, 9]
    u_plane[:, 1::2] = records[:, :, 10]
    v_plane[:, 1::2] = records[:, :, 11]
    return target


__all__ = ["mb420_to_i420"]
