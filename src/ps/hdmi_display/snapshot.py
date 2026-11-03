"""One bounded CPU-only export of an owned, immutable camera snapshot."""
from dataclasses import asdict, replace
import threading

import numpy as np

from .compositor import Compositor
from .image_export import export_image


class SnapshotExport:
    def __init__(self, pair, destination, composition, size, identity):
        self.cancelled = threading.Event()
        self.lock = threading.Lock()
        self.state = dict(state='running', destination=str(destination))
        self.thread = threading.Thread(target=self._run,
            args=(pair, destination, composition, size, dict(identity)), name='snapshot-export')
        self.thread.start()

    def status(self):
        with self.lock:
            return dict(self.state)

    def _run(self, pair, destination, composition, size, identity):
        try:
            width, height = size
            view = Compositor.normalize(pair, width, height, replace(composition, layout='split'))
            canvas = np.empty((height, width, 4), np.uint8)
            Compositor().compose(pair, canvas, view)
            identity.update(kind='frozen_camera', requested_view=asdict(composition),
                            comparison_view=asdict(view))
            path = export_image(pair, destination, source_identity=identity,
                                comparison_rgb=canvas[..., :3], cancelled=self.cancelled.is_set)
            result = dict(state='complete', report=str(path))
        except InterruptedError:
            result = dict(state='cancelled')
        except Exception as exc:
            result = dict(state='failed', error=f'{type(exc).__name__}: {exc}')
        with self.lock:
            self.state.update(result)

    def close(self):
        self.cancelled.set()
        self.thread.join(timeout=30)
        if self.thread.is_alive():
            raise RuntimeError('Snapshot writer has not stopped')
