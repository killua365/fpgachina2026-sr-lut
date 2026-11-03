"""Letterbox geometry with source-space ROI coordinates."""

from __future__ import annotations

from dataclasses import dataclass
import math

from roi_magnifier.geometry import Rect, plan_roi


@dataclass(frozen=True)
class Viewport:
    source_width: int
    source_height: int
    bounds: Rect

    @classmethod
    def fit(cls, source_width: int, source_height: int, bounds: Rect) -> "Viewport":
        values = (source_width, source_height, bounds.width, bounds.height)
        if any(isinstance(v, bool) or not isinstance(v, int) or v < 1 for v in values):
            raise ValueError("Positive integer dimensions are required")
        if bounds.width * source_height <= bounds.height * source_width:
            width = bounds.width
            height = max(1, source_height * width // source_width)
        else:
            height = bounds.height
            width = max(1, source_width * height // source_height)
        return cls(source_width, source_height, Rect(
            bounds.x + (bounds.width - width) // 2,
            bounds.y + (bounds.height - height) // 2, width, height,
        ))

    def contains(self, x: float, y: float) -> bool:
        return self.bounds.x <= x < self.bounds.right and self.bounds.y <= y < self.bounds.bottom

    def source_point(self, x: float, y: float) -> tuple[int, int]:
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError("Finite display coordinates are required")
        rect = self.bounds
        return (
            max(0, min(self.source_width - 1, math.floor((x - rect.x) * self.source_width / rect.width))),
            max(0, min(self.source_height - 1, math.floor((y - rect.y) * self.source_height / rect.height))),
        )

    def selection(self, start: tuple[float, float], end: tuple[float, float], scale: int) -> Rect:
        if not self.contains(*start):
            raise ValueError("Selection must start inside the displayed image")
        first = self.source_point(*start)
        last = self.source_point(*end)
        x, y = min(first[0], last[0]), min(first[1], last[1])
        width, height = abs(last[0] - first[0]) + 1, abs(last[1] - first[1]) + 1
        # The existing planner remains authoritative about halo and FPGA limits.
        return plan_roi(self.source_width, self.source_height, x, y, width, height, scale).roi

    def display_rect(self, source: Rect) -> Rect:
        if source.width < 1 or source.height < 1 or source.x < 0 or source.y < 0:
            raise ValueError("Invalid source rectangle")
        if source.right > self.source_width or source.bottom > self.source_height:
            raise ValueError("Source rectangle is outside the image")
        target = self.bounds
        x0 = target.x + source.x * target.width // self.source_width
        y0 = target.y + source.y * target.height // self.source_height
        x1 = target.x + math.ceil(source.right * target.width / self.source_width)
        y1 = target.y + math.ceil(source.bottom * target.height / self.source_height)
        return Rect(x0, y0, max(1, x1 - x0), max(1, y1 - y0))
