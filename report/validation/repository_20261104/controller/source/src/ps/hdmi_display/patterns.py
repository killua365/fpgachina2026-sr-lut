"""Native diagnostic RGBX patterns for the verified display profiles."""

import cv2
import numpy as np
from .scanout import MODE_720P60, DisplayMode


def draw_pattern(destination, name, sequence=0, mode: DisplayMode = MODE_720P60):
    if destination.shape != (mode.height, mode.width, 4) or destination.dtype != np.uint8:
        raise ValueError(f"Patterns require a native {mode.name} RGBX destination")
    if name not in ("bars", "rgb", "checker", "motion"):
        raise ValueError("Unknown HDMI diagnostic pattern")
    destination.fill(0)
    if name == "bars":
        colors = [(255, 255, 255), (255, 255, 0), (0, 255, 255), (0, 255, 0),
                  (255, 0, 255), (255, 0, 0), (0, 0, 255), (0, 0, 0)]
        bar_width = mode.width // len(colors)
        for index, color in enumerate(colors):
            destination[:, index * bar_width:(index + 1) * bar_width, :3] = color
    elif name == "rgb":
        for index, color in enumerate(((255, 0, 0), (0, 255, 0), (0, 0, 255))):
            left, right = index * mode.width // 3, (index + 1) * mode.width // 3
            destination[:, left:right, :3] = color
    elif name == "checker":
        rows, columns = np.ogrid[:mode.height, :mode.width]
        levels = (((rows // 16 + columns // 16) % 2) * 255).astype(np.uint8)
        destination[:, :, :3] = levels[:, :, None]
        # Native one-pixel lines expose cropping and unwanted monitor scaling.
        y0, y1 = mode.height * 7 // 20, mode.height * 13 // 20
        x0, x1 = mode.width * 5 // 16, mode.width * 11 // 16
        destination[y0:y1, x0:x1, :3] = ((np.arange(x1 - x0) % 2) * 255)[None, :, None]
    else:
        destination[:, :, :3] = (24, 28, 32)
        box_w, box_h = max(16, mode.width // 16), max(16, mode.height // 4)
        left = (int(sequence) * 17) % max(1, mode.width - box_w)
        destination[mode.height // 3:mode.height // 3 + box_h, left:left + box_w, :3] = (45, 215, 130)
        top = (int(sequence) * 11) % max(1, mode.height - 60)
        destination[top:top + max(16, mode.height // 18), mode.width // 2:mode.width // 2 + max(8, mode.width // 32), :3] = (245, 190, 45)
    destination[0, :, :3] = destination[-1, :, :3] = (255, 255, 255)
    destination[:, 0, :3] = destination[:, -1, :3] = (255, 255, 255)
    cv2.rectangle(destination, (24, 24), (min(mode.width - 1, 900), 106), (0, 0, 0, 0), -1)
    cv2.putText(destination, f"SR-LUT  {mode.name}  {name}", (40, 57),
                cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255, 0), 1, cv2.LINE_AA)
    cv2.putText(destination, f"FRAME {int(sequence):010d}", (40, 91),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255, 0), 1, cv2.LINE_AA)


PATTERNS = ("bars", "rgb", "checker", "motion")
