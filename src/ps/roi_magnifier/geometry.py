"""Geometry planning for pixel-exact ROI processing with the x4 core."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


MIN_PROCESS_WIDTH = 32
MIN_PROCESS_HEIGHT = 32
MAX_PROCESS_WIDTH = 480
MAX_PROCESS_HEIGHT = 320
SCALE = 4


@dataclass(frozen=True)
class Rect:
    """An integer rectangle using an inclusive origin and exclusive extent."""

    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    def as_dict(self) -> Mapping[str, int]:
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }


@dataclass(frozen=True)
class RoiPlan:
    """The visible ROI, FPGA input window, and x4 output crop."""

    source_width: int
    source_height: int
    roi: Rect
    process_window: Rect
    output_crop: Rect

    @property
    def output_width(self) -> int:
        return self.roi.width * self.scale

    @property
    def output_height(self) -> int:
        return self.roi.height * self.scale

    @property
    def scale(self) -> int:
        return int(self.output_crop.width // self.roi.width)

    def as_dict(self) -> Mapping[str, object]:
        return {
            "source": {
                "width": self.source_width,
                "height": self.source_height,
            },
            "roi": dict(self.roi.as_dict()),
            "process_window": dict(self.process_window.as_dict()),
            "output_crop": dict(self.output_crop.as_dict()),
            "output": {
                "width": self.output_width,
                "height": self.output_height,
                "scale": self.scale,
            },
        }


def _require_integer(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value


def _expand_interval(start: int, end: int, limit: int, minimum: int) -> tuple[int, int]:
    """Expand an interval deterministically while remaining inside the source."""

    missing = max(0, minimum - (end - start))
    take_left = min((missing + 1) // 2, start)
    start -= take_left
    missing -= take_left

    take_right = min(missing, limit - end)
    end += take_right
    missing -= take_right

    take_left = min(missing, start)
    start -= take_left
    missing -= take_left
    if missing:
        raise ValueError(
            f"source dimension {limit} is smaller than the required minimum {minimum}"
        )
    return start, end


def plan_roi(
    source_width: int,
    source_height: int,
    x: int,
    y: int,
    width: int,
    height: int,
    scale: int = SCALE,
) -> RoiPlan:
    """Plan a hardware window that preserves the ROI's one-pixel neighborhood.

    Internal ROI edges receive one real source pixel of context. Edges that
    coincide with the source image intentionally remain processing-window edges
    so the existing RTL reflect behavior is also the true image-boundary rule.
    """

    if scale not in (2, 4):
        raise ValueError("scale must be 2 or 4")
    values = {
        "source_width": source_width,
        "source_height": source_height,
        "x": x,
        "y": y,
        "width": width,
        "height": height,
    }
    values = {name: _require_integer(name, value) for name, value in values.items()}
    source_width = values["source_width"]
    source_height = values["source_height"]
    x = values["x"]
    y = values["y"]
    width = values["width"]
    height = values["height"]

    if source_width < MIN_PROCESS_WIDTH or source_height < MIN_PROCESS_HEIGHT:
        raise ValueError(
            f"source image must be at least {MIN_PROCESS_WIDTH}x{MIN_PROCESS_HEIGHT}"
        )
    if width < 1 or height < 1:
        raise ValueError("ROI width and height must be positive")
    if x < 0 or y < 0 or x + width > source_width or y + height > source_height:
        raise ValueError("ROI must stay inside the source image")

    process_x0 = max(0, x - 1)
    process_y0 = max(0, y - 1)
    process_x1 = min(source_width, x + width + 1)
    process_y1 = min(source_height, y + height + 1)

    if process_x1 - process_x0 > MAX_PROCESS_WIDTH:
        raise ValueError(
            f"ROI plus horizontal neighborhood exceeds the {MAX_PROCESS_WIDTH}-pixel FPGA limit"
        )
    if process_y1 - process_y0 > MAX_PROCESS_HEIGHT:
        raise ValueError(
            f"ROI plus vertical neighborhood exceeds the {MAX_PROCESS_HEIGHT}-pixel FPGA limit"
        )

    process_x0, process_x1 = _expand_interval(
        process_x0, process_x1, source_width, MIN_PROCESS_WIDTH
    )
    process_y0, process_y1 = _expand_interval(
        process_y0, process_y1, source_height, MIN_PROCESS_HEIGHT
    )
    process_window = Rect(
        process_x0,
        process_y0,
        process_x1 - process_x0,
        process_y1 - process_y0,
    )
    output_crop = Rect(
        (x - process_x0) * scale,
        (y - process_y0) * scale,
        width * scale,
        height * scale,
    )
    return RoiPlan(
        source_width=source_width,
        source_height=source_height,
        roi=Rect(x, y, width, height),
        process_window=process_window,
        output_crop=output_crop,
    )


__all__ = [
    "MAX_PROCESS_HEIGHT",
    "MAX_PROCESS_WIDTH",
    "MIN_PROCESS_HEIGHT",
    "MIN_PROCESS_WIDTH",
    "Rect",
    "RoiPlan",
    "SCALE",
    "plan_roi",
]
