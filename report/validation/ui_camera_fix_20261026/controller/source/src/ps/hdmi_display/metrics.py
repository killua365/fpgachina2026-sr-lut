"""Owned still-image quality work and bounded display-acknowledgement rates."""
from collections import deque
import math
from pathlib import Path
import threading

import numpy as np
from PIL import Image
from roi_magnifier.quality import calculate_psnr_y, calculate_ssim_y, nearest_scaled


class ImagePairs:
    def __init__(self, root, records):
        self.root, self.records = Path(root).resolve(), list(records)
        if not self.records or len({r['name'] for r in self.records}) != len(self.records):
            raise ValueError('Image pairs require unique names')
        for record in self.records:
            for relative in (record['hr'], record['lr']['2'], record['lr']['4']):
                path = (self.root / relative).resolve(strict=True)
                path.relative_to(self.root)

    def load(self, index, scale):
        record = self.records[index % len(self.records)]
        source = self.root / record['lr'][str(scale)]
        with Image.open(source) as image:
            lr = np.asarray(image.convert('RGB')).copy()
        with Image.open(self.root / record['hr']) as image:
            hr = np.asarray(image.convert('RGB')).copy()
        if hr.shape != (lr.shape[0]*scale, lr.shape[1]*scale, 3):
            raise ValueError('LR/HR image pair geometry differs')
        hr.flags.writeable = False
        return lr, hr, source, record


def quality_scores(source, output, reference, scale):
    if reference.shape != output.shape:
        raise ValueError('ROI reference does not match output')
    def score(rgb):
        p, s = calculate_psnr_y(reference, rgb), calculate_ssim_y(reference, rgb)
        if p is None or s is None:
            raise ValueError('Region too small for quality measurement')
        return dict(psnr_y_db='infinity' if math.isinf(p) else float(p), ssim_y=float(s))
    return dict(sr=score(output), nearest=score(nearest_scaled(source, scale)),
                reference_type='hr_ground_truth', psnr_crop_border=4, ssim_window=11)


class QualityWorker:
    """One running CPU job and one replaceable pending job; never owns hardware."""
    def __init__(self, compute=quality_scores):
        self.compute = compute
        self.condition = threading.Condition()
        self.pending = self.current = None
        self.value = dict(state='idle')
        self.closed = False
        self.thread = threading.Thread(target=self._run, name='image-quality', daemon=True)
        self.thread.start()

    def invalidate(self):
        with self.condition:
            self.current = self.pending = None
            self.value = dict(state='idle')

    def submit(self, key, pair, reference):
        with self.condition:
            if key == self.current:
                return
            self.current = key
            self.value = dict(state='running', identity=key)
            self.pending = (key, pair, np.array(reference, copy=True))
            self.condition.notify()

    def status(self):
        with self.condition:
            return dict(self.value)

    def _run(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.closed or self.pending is not None)
                if self.closed:
                    return
                key, pair, reference = self.pending
                self.pending = None
            try:
                value = dict(state='complete', identity=key,
                    **self.compute(pair.source_rgb, pair.output_rgb, reference, pair.scale))
            except Exception as exc:
                value = dict(state='unavailable', identity=key, error=str(exc))
            with self.condition:
                if self.current == key and not self.closed:
                    self.value = value

    def close(self):
        with self.condition:
            self.closed = True
            self.pending = None
            self.condition.notify()
        self.thread.join()


class DisplayRate:
    """Two-second window, one-second publication, no repaint/refresh counting."""
    def __init__(self):
        self.key = None
        self.points = deque()
        self.last_publish = None
        self.value = dict(state='waiting', fps=None, window_seconds=2,
                          scope='PYNQ VDMA acknowledged new source pairs')

    def update(self, now, key, count, state='live'):
        if key != self.key or state != 'live' or count is None:
            self.key, self.last_publish = key, None
            self.points.clear()
            self.value = dict(self.value, state=state if state != 'live' else 'waiting', fps=None)
        if state != 'live' or count is None:
            return self.value
        if self.points and (now < self.points[-1][0] or count < self.points[-1][1]):
            self.points.clear()
            self.last_publish = None
            self.value = dict(self.value, state='waiting', fps=None)
        if not self.points or now-self.points[-1][0] >= .05:
            self.points.append((now, count))
        while len(self.points) > 1 and self.points[1][0] <= now-2:
            self.points.popleft()
        if self.points and now-self.points[0][0] >= 2 and (self.last_publish is None or now-self.last_publish >= 1):
            elapsed = now-self.points[0][0]
            self.value = dict(self.value, state='live', fps=(count-self.points[0][1])/elapsed)
            self.last_publish = now
        return self.value
