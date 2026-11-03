"""Single-owner playback timeline; processing results carry generation tokens."""

from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import Callable

import numpy as np

from .frames import FrameGeneration, FramePair, FrameToken
from .media import RawClip


@dataclass(frozen=True)
class PlaybackRequest:
    token: FrameToken
    source_index: int
    source_time: float
    source_rgb: np.ndarray
    scale: int

    def pair(self, output_rgb: np.ndarray) -> FramePair:
        return FramePair(self.token, self.source_rgb, output_rgb, self.scale, self.source_time)


class Playback:
    """UI thread owns this object and dispatches at most one request per tick.

    Real-time playback samples the current timeline and may skip expired frames.
    Paused stepping changes by exactly one source frame. Export uses RawClip
    directly and never samples this timeline. The caller owns clip lifetime.
    """

    def __init__(self, clip: RawClip, scale: int, *, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.generations = FrameGeneration()
        self.clip = clip
        self.scale = self._scale(scale)
        self.playing = False
        self.loop = False
        self.speed = 1.0
        self._position = 0.0
        self._anchor = clock()
        self._last_requested = None

    @staticmethod
    def _scale(scale: int) -> int:
        if isinstance(scale, bool) or scale not in (2, 4):
            raise ValueError("Scale must be 2 or 4")
        return scale

    def _position_now(self) -> float:
        position = self._position
        if self.playing:
            position += max(0, self.clock() - self._anchor) * float(self.clip.fps) * self.speed
        if self.loop:
            return position % self.clip.frame_count
        return min(position, self.clip.frame_count - 1)

    @property
    def index(self) -> int:
        return math.floor(self._position_now())

    def _anchor_now(self) -> None:
        self._position = self._position_now()
        self._anchor = self.clock()

    def _invalidate(self) -> None:
        self.generations.invalidate()
        self._last_requested = None

    def play(self) -> None:
        if not self.playing:
            if self.index == self.clip.frame_count - 1:
                self.seek(0)
            self._anchor = self.clock()
            self.playing = True

    def pause(self) -> None:
        self._anchor_now()
        self.playing = False

    def seek(self, index: int) -> None:
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < self.clip.frame_count:
            raise IndexError(index)
        self._position = float(index)
        self._anchor = self.clock()
        self._invalidate()

    def step(self, direction: int) -> None:
        if isinstance(direction, bool) or direction not in (-1, 1):
            raise ValueError("Step direction must be -1 or 1")
        self.pause()
        self.seek(max(0, min(self.clip.frame_count - 1, self.index + direction)))

    def set_speed(self, speed: float) -> None:
        if isinstance(speed, bool) or speed not in (0.5, 1.0, 2.0):
            raise ValueError("Playback speed must be 0.5, 1 or 2")
        self._anchor_now()
        self.speed = speed

    def set_loop(self, enabled: bool) -> None:
        self._anchor_now()
        self.loop = bool(enabled)

    def set_scale(self, scale: int) -> None:
        scale = self._scale(scale)
        if self.scale != scale:
            self.scale = scale
            self._invalidate()

    def load(self, clip: RawClip) -> None:
        self.clip = clip
        self.playing = False
        self.seek(0)

    def request(self) -> PlaybackRequest | None:
        index = self.index
        if not self.loop and index == self.clip.frame_count - 1:
            self.pause()
        if self._last_requested == index:
            return None
        # Read first: a failed read must not consume this source index.
        source = self.clip.frame(index)
        self._last_requested = index
        return PlaybackRequest(self.generations.issue(), index,
                               index / float(self.clip.fps), source, self.scale)

    def accept(self, pair: FramePair) -> bool:
        return pair.scale == self.scale and self.generations.accept(pair.token)
