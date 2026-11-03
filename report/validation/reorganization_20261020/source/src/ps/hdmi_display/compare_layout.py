"""One geometry for Compare pictures, captions and pixel-view pan limits."""
from dataclasses import dataclass

from roi_magnifier.geometry import Rect
from .geometry import Viewport


@dataclass(frozen=True)
class CompareLayout:
    panels: tuple
    views: tuple
    captions: tuple
    fps: object = None
    native_split: bool = False

    @classmethod
    def build(cls, pair, width, height, mode, pixel_view=False, toolbar=True):
        factor = width / 1920
        px = lambda n: max(1, round(n * factor))
        gap, margin = px(12), px(24)
        half = width // 2
        caption_height = px(128 if mode == 'image' else 42)
        footer_height = px(68) if mode == 'camera' else 0
        bottom = round(height * 625 / 720) if toolbar else height - margin
        # Exact 1080p Fit geometry is shared with the existing native camera path.
        native = (mode in ('camera', 'video') and not pixel_view
                  and (width, height) == (1920, 1080)
                  and pair.source_rgb.shape[:2] == (270, 480))
        if native:
            panels = (Rect(0, 0, half, height), Rect(width-half, 0, half, height))
        else:
            top = margin + caption_height + gap
            body_bottom = bottom - (footer_height + gap if footer_height else 0)
            panels = (Rect(0, top, half, max(2, body_bottom-top)),
                      Rect(width-half, top, half, max(2, body_bottom-top)))
        h, w = pair.output_rgb.shape[:2]
        views = []
        for panel in panels:
            if pixel_view:
                vw, vh = min(w, panel.width), min(h, panel.height)
                view = Rect(panel.x+(panel.width-vw)//2, panel.y+(panel.height-vh)//2, vw, vh)
            else:
                view = Viewport.fit(w, h, panel).bounds
            views.append(view)
        captions = []
        for panel, view in zip(panels, views):
            cw = min(panel.width-2*margin, max(view.width, px(600)))
            x = view.x if view.width >= cw else view.x+(view.width-cw)//2
            x = max(panel.x+margin, min(x, panel.right-margin-cw))
            captions.append(Rect(x, max(margin, view.y-gap-caption_height), cw, caption_height))
        fps = None
        if mode == 'camera' and not pixel_view:
            y = max(v.bottom for v in views)+gap
            fps = Rect((width-px(680))//2, min(y, bottom-footer_height), px(680), footer_height)
        return cls(tuple(panels), tuple(views), tuple(captions), fps, native)
