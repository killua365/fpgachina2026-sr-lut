"""Same-source frame pairs and generation changes for asynchronous operations."""

from __future__ import annotations

from dataclasses import dataclass
import math
import threading

import numpy as np


@dataclass(frozen=True)
class FrameToken:
    generation: int
    sequence: int


@dataclass(frozen=True)
class FramePair:
    token: FrameToken
    source_rgb: np.ndarray
    output_rgb: np.ndarray
    scale: int
    source_time: float

    def __post_init__(self) -> None:
        if self.scale not in (2, 4) or isinstance(self.scale, bool):
            raise ValueError("SR scale must be 2 or 4")
        if not math.isfinite(self.source_time) or self.source_time < 0:
            raise ValueError("Invalid source timestamp")
        for array in (self.source_rgb, self.output_rgb):
            if not isinstance(array, np.ndarray) or array.dtype != np.uint8 or array.ndim != 3 or array.shape[2] != 3:
                raise ValueError("Frame images must be HxWx3 uint8 RGB")
            if min(array.shape[:2]) < 1:
                raise ValueError("Empty frame")
        height, width = self.source_rgb.shape[:2]
        if self.output_rgb.shape[:2] != (height * self.scale, width * self.scale):
            raise ValueError("Source and SR result geometry disagree")


@dataclass(frozen=True)
class BorrowedFramePair(FramePair):
    """Camera-only result valid until its DMA slot is reused or closed.

    The owning runtime retains at most one ready result and one last display
    result. Export and ROI APIs continue to receive independent FramePair data.
    """
    session: object
    buffer_index: int
    buffer_epoch: int

    @property
    def valid(self):
        return (not self.session.closed and
                self.session.buffer_epochs[self.buffer_index] == self.buffer_epoch)


@dataclass(frozen=True)
class DirectFramePair(BorrowedFramePair):
    """Full SR result in a reserved, non-scanning display allocation.

    Once submitted it contains UI; a repaint must regenerate from source_rgb.
    """
    display_lease: object


class FrameGeneration:
    """Reject results from a cancelled source, scale, ROI or seek operation."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._generation = 0
        self._next_sequence = 0
        self._last_published = -1

    def invalidate(self) -> int:
        with self._lock:
            self._generation += 1
            self._next_sequence = 0
            self._last_published = -1
            return self._generation

    def issue(self) -> FrameToken:
        with self._lock:
            token = FrameToken(self._generation, self._next_sequence)
            self._next_sequence += 1
            return token

    def accept(self, token: FrameToken) -> bool:
        with self._lock:
            if token.generation != self._generation or not self._last_published < token.sequence < self._next_sequence:
                return False
            self._last_published = token.sequence
            return True
