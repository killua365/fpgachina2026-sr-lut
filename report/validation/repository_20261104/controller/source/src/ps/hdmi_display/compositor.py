"""Compose paired RGB frames into a caller-owned RGBX scanout buffer."""

from __future__ import annotations

from dataclasses import dataclass, replace

import cv2
import numpy as np

from roi_magnifier.geometry import Rect
from .frames import FramePair, BorrowedFramePair
from .geometry import Viewport
from .pixels import blit_rgb, paint_nearest, fill_rgbx


def resize_nearest(image, size):
    """Nearest resize, including singleton axes on PYNQ's OpenCV 4.5.4.

    That ARM build rejects valid 1x1x3 NumPy matrices. Integer index grids
    implement identical nearest-neighbor sampling without a Python pixel loop.
    """
    width, height = size
    if image.shape[:2] == (height, width):
        return image
    if min(image.shape[:2]) == 1:
        xs = np.arange(width, dtype=np.int64) * image.shape[1] // width
        ys = np.arange(height, dtype=np.int64) * image.shape[0] // height
        return image[ys[:, None], xs]
    return cv2.resize(image, size, interpolation=cv2.INTER_NEAREST)


@dataclass(frozen=True)
class Composition:
    layout: str = "split"
    pixel_view: bool = False
    pan_x: int = 0
    pan_y: int = 0
    wipe_fraction: float = 0.5


class Compositor:
    """The caller retains both source buffers until compose() returns.

    The destination must not be owned by a running display DMA. No source or
    processed buffer is modified, and no frame state is retained by this class.
    """

    def __init__(self):
        self.clear_cache()

    def clear_cache(self):
        self._wipe_pair = self._wipe_key = self._wipe_planes = None

    @staticmethod
    def view_bounds(pair, width, height, config, compare_layout=None):
        """Shared content bounds for drawing, pointer mapping and pan limits."""
        if config.layout == 'split' and compare_layout is not None:
            return compare_layout.views[0]
        h, w = pair.output_rgb.shape[:2]
        bounds = Rect(0, 0, width // 2 if config.layout == 'split' else width, height)
        if config.pixel_view:
            vw, vh = min(w, bounds.width), min(h, bounds.height)
            return Rect((bounds.width-vw)//2, (height-vh)//2, vw, vh)
        return Viewport.fit(w, h, bounds).bounds

    @staticmethod
    def normalize(pair, width, height, config, compare_layout=None):
        if not config.pixel_view:
            return replace(config, pan_x=0, pan_y=0)
        bounds = Compositor.view_bounds(pair, width, height, config, compare_layout)
        h, w = pair.output_rgb.shape[:2]
        return replace(config, pan_x=max(0, min(int(config.pan_x), w-bounds.width)),
                       pan_y=max(0, min(int(config.pan_y), h-bounds.height)))

    def _wipe(self, pair, destination, config):
        # Owned still frames reuse both views. Never cache a recyclable DMA view.
        key = (destination.shape, config.pixel_view, config.pan_x, config.pan_y)
        reusable = not isinstance(pair, BorrowedFramePair)
        if not reusable or self._wipe_pair is not pair or self._wipe_key != key:
            planes = [np.zeros_like(destination), np.zeros_like(destination)]
            bounds = Rect(0, 0, destination.shape[1], destination.shape[0])
            if config.pixel_view:
                self._blit_expanded(planes[0], pair.source_rgb, pair.scale, bounds, config)
            else:
                self._blit(planes[0], pair.source_rgb, bounds, config)
            self._blit(planes[1], pair.output_rgb, bounds, config)
            if reusable:
                self._wipe_pair, self._wipe_key, self._wipe_planes = pair, key, planes
        else:
            planes = self._wipe_planes
        view = self.view_bounds(pair, destination.shape[1], destination.shape[0], config)
        cut = view.x + round(view.width * config.wipe_fraction)
        destination[:, :cut] = planes[0][:, :cut]
        destination[:, cut:] = planes[1][:, cut:]

    @staticmethod
    def _blit_expanded(target, image, scale, bounds, config, preserve_letterbox=False):
        """Crop the virtual nearest baseline without expanding the full frame.

        Aligned interior tiles use the existing native integer painter. At
        most three edge pixels per axis retain the exact sampling phase.
        """
        source_height, source_width = image.shape[:2]
        width, height = min(bounds.width, source_width*scale), min(bounds.height, source_height*scale)
        pan_x = max(0,min(config.pan_x,source_width*scale-width))
        pan_y = max(0,min(config.pan_y,source_height*scale-height))
        x, y = bounds.x+(bounds.width-width)//2, bounds.y+(bounds.height-height)//2
        if not preserve_letterbox:
            target[bounds.y:y,bounds.x:bounds.right] = 0
            target[y+height:bounds.bottom,bounds.x:bounds.right] = 0
            target[y:y+height,bounds.x:x] = 0
            target[y:y+height,x+width:bounds.right] = 0
        def intervals(pan,length):
            cuts=sorted({0,length,min(length,(-pan)%scale),max(0,length-(pan+length)%scale)})
            return list(zip(cuts,cuts[1:]))
        for top,bottom in intervals(pan_y,height):
            for left,right in intervals(pan_x,width):
                src=image[(pan_y+top)//scale:(pan_y+bottom+scale-1)//scale,
                          (pan_x+left)//scale:(pan_x+right+scale-1)//scale]
                dst=target[y+top:y+bottom,x+left:x+right]
                if not paint_nearest(dst,src):
                    dst[...,3]=0
                    blit_rgb(dst,resize_nearest(src,(right-left,bottom-top)))

    @staticmethod
    def _view(image: np.ndarray, width: int, height: int, config: Composition) -> np.ndarray:
        if config.pixel_view:
            source_height, source_width = image.shape[:2]
            x = max(0, min(config.pan_x, max(0, source_width - width)))
            y = max(0, min(config.pan_y, max(0, source_height - height)))
            return image[y:y + height, x:x + width]
        rect = Viewport.fit(image.shape[1], image.shape[0], Rect(0, 0, width, height)).bounds
        return resize_nearest(image, (rect.width, rect.height))

    @staticmethod
    def _blit(target: np.ndarray, image: np.ndarray, bounds: Rect, config: Composition, preserve_letterbox=False,
              background=0) -> None:
        if config.pixel_view:
            view = Compositor._view(image, bounds.width, bounds.height, config)
            height, width = view.shape[:2]
            rect = Rect(bounds.x + (bounds.width - width) // 2,
                        bounds.y + (bounds.height - height) // 2, width, height)
            source = view
        else:
            rect = Viewport.fit(image.shape[1], image.shape[0], bounds).bounds
            source = image
        if paint_nearest(target[rect.y:rect.bottom, rect.x:rect.right], source):
            # Exact cropped 1:1 copies use the same stride-aware native path.
            # Clear only letterbox pixels; the painter writes every RGBX byte.
            if not preserve_letterbox:
                fill_rgbx(target[bounds.y:rect.y, bounds.x:bounds.right],background)
                fill_rgbx(target[rect.bottom:bounds.bottom, bounds.x:bounds.right],background)
                fill_rgbx(target[rect.y:rect.bottom, bounds.x:rect.x],background)
                fill_rgbx(target[rect.y:rect.bottom, rect.right:bounds.right],background)
            return
        if not preserve_letterbox:
            fill_rgbx(target[bounds.y:bounds.bottom, bounds.x:bounds.right],background)
        view = Compositor._view(image, bounds.width, bounds.height, config)
        height, width = view.shape[:2]
        x = bounds.x + (bounds.width - width) // 2
        y = bounds.y + (bounds.height - height) // 2
        blit_rgb(target[y:y + height, x:x + width], view)

    def compose(self, pair: FramePair, destination: np.ndarray, config: Composition, *, preserve_letterbox=False,
                compare_layout=None) -> None:
        if destination.dtype != np.uint8 or destination.ndim != 3 or destination.shape[2] != 4:
            raise ValueError("Destination must be HxWx4 uint8 RGBX")
        if not destination.flags.c_contiguous or min(destination.shape[:2]) < 2:
            raise ValueError("Destination must be contiguous and at least 2x2")
        if np.shares_memory(destination, pair.source_rgb) or np.shares_memory(destination, pair.output_rgb):
            raise ValueError("Scanout must not alias source or SR result")
        if config.layout not in ("sr", "baseline", "split", "wipe"):
            raise ValueError("Unknown comparison layout")
        if not 0 <= config.wipe_fraction <= 1:
            raise ValueError("Wipe fraction must be between 0 and 1")
        if config.layout == 'wipe':
            self._wipe(pair, destination, config)
            return
        self.clear_cache()
        height, width = destination.shape[:2]
        if config.layout == 'split' and compare_layout is not None and not compare_layout.native_split:
            fill_rgbx(destination,(17,23,32,0))
            left, right = compare_layout.panels
            if config.pixel_view:
                self._blit_expanded(destination,pair.source_rgb,pair.scale,left,config,preserve_letterbox=True)
            else:
                self._blit(destination,pair.source_rgb,left,config,preserve_letterbox=True)
            self._blit(destination,pair.output_rgb,right,config,preserve_letterbox=True)
            return
        if preserve_letterbox and (config.layout != 'split' or config.pixel_view
                or (height, width) != (1080, 1920) or pair.source_rgb.shape != (270, 480, 3)):
            raise ValueError('Partial compose requires the exact full-resolution camera split geometry')
        whole = Rect(0, 0, width, height)
        if config.layout == "sr":
            self._blit(destination, pair.output_rgb, whole, config)
            return
        if config.pixel_view and config.layout in ('baseline','split'):
            if config.layout == 'baseline':
                self._blit_expanded(destination,pair.source_rgb,pair.scale,whole,config)
            else:
                panel_width=width//2
                destination[:,panel_width:width-panel_width]=0
                self._blit_expanded(destination,pair.source_rgb,pair.scale,Rect(0,0,panel_width,height),config)
                self._blit(destination,pair.output_rgb,Rect(width-panel_width,0,panel_width,height),config)
            return
        # Integer SR scales commute with nearest fit sampling: expanding by s
        # then fitting selects floor(floor(x*s*W/T)/s) == floor(x*W/T).
        # Pixel inspection still uses the expanded grid so pan stays in SR pixels.
        baseline = (resize_nearest(pair.source_rgb, (pair.output_rgb.shape[1], pair.output_rgb.shape[0]))
                    if config.pixel_view else pair.source_rgb)
        if config.layout == "baseline":
            self._blit(destination, baseline, whole, config)
        elif config.layout == "split":
            # Equal panel extents keep source/SR scaling and pixel pan identical.
            panel_width = width // 2
            destination[:, panel_width:width-panel_width] = 0
            background = (17,23,32,0) if compare_layout is not None else 0
            self._blit(destination, baseline, Rect(0, 0, panel_width, height), config, preserve_letterbox,background)
            self._blit(destination, pair.output_rgb, Rect(width - panel_width, 0, panel_width, height), config, preserve_letterbox,background)
        else:
            destination.fill(0)
            before = self._view(baseline, width, height, config)
            after = self._view(pair.output_rgb, width, height, config)
            h, w = before.shape[:2]
            x, y = (width - w) // 2, (height - h) // 2
            split = round(w * config.wipe_fraction)
            blit_rgb(destination[y:y + h, x:x + split], before[:, :split])
            blit_rgb(destination[y:y + h, x + split:x + w], after[:, split:])
