from fractions import Fraction
from types import SimpleNamespace
import unittest

import numpy as np

from hdmi_display.camera import CameraFrame
from hdmi_display.compositor import Composition
from hdmi_display.controls import ControlEvent
from hdmi_display.runtime import Runtime
from roi_magnifier.geometry import Rect
import test_hdmi_engine


class Controls:
    def __init__(self):
        self.events = []

    def poll_engine(self, engine):
        return self.events.pop(0) if self.events else None

    def status(self):
        return {}


class Camera:
    def __init__(self):
        self.started = self.stopped = 0
        self.frames = []

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1

    def poll(self):
        return self.frames.pop(0) if self.frames else None

    def status(self):
        return {}


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_hdmi_engine.EngineTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.now = 0.0
        self.controls, self.camera = Controls(), Camera()
        self.runtime = Runtime(self.fixture.engine, camera=self.camera,
                               controls=self.controls, clock=lambda: self.now)
        self.addCleanup(self.runtime.close)
        self.runtime.start(np.arange(32 * 35 * 3, dtype=np.uint8).reshape(32, 35, 3))

    def event(self, mode, switches=0):
        self.controls.events.append(ControlEvent(mode, switches, True))

    def test_image_repaint_does_not_reprocess_or_count_a_new_source_frame(self):
        self.assertTrue(self.runtime.tick())
        initial = self.fixture.engine.processed_frames
        self.runtime.set_composition(Composition(layout="wipe", wipe_fraction=0.25))
        self.assertFalse(self.runtime.tick())
        self.assertEqual(self.fixture.engine.processed_frames, initial)
        self.assertEqual(self.runtime.submitted_new_frames, 1)

    def test_roi_change_rejects_previous_generation_and_uses_original_pixels(self):
        self.runtime.tick()
        previous = self.runtime.last_pair
        self.runtime.set_roi(Rect(34, 31, 1, 1))
        self.assertFalse(self.fixture.engine.present(previous))
        self.assertTrue(self.runtime.tick())
        np.testing.assert_array_equal(self.runtime.last_pair.source_rgb, self.runtime.image[31:32, 34:35])
        self.assertEqual(self.runtime.last_pair.output_rgb.shape, (2, 2, 3))

    def test_camera_fixed_scale_and_idle_stop_preserve_display(self):
        self.event("camera")
        self.runtime.tick()
        self.assertEqual(self.camera.started, 1)
        self.assertEqual(self.fixture.loads, [2, 4])
        self.event(None, 3)
        self.runtime.tick()
        self.assertEqual(self.camera.started, 1)
        self.assertEqual(self.fixture.loads, [2, 4])
        self.event("idle", 0)
        self.runtime.tick()
        self.assertEqual(self.camera.stopped, 1)
        self.assertEqual(self.fixture.loads, [2, 4])
        self.assertIsNotNone(self.fixture.engine.display)
        self.assertIsNone(self.runtime.last_pair)

    def test_camera_recovery_invalidates_old_frame_generation(self):
        self.event("camera")
        self.camera.frames.append(CameraFrame(1, 1, np.zeros((32, 32, 3), np.uint8),
            1.0, 0.01, 640, 480, Rect(0, 0, 32, 32)))
        self.assertTrue(self.runtime.tick())
        previous = self.runtime.last_pair
        self.camera.frames.append(CameraFrame(2, 2, np.ones((32, 32, 3), np.uint8),
            2.0, 0.01, 640, 480, Rect(0, 0, 32, 32)))
        self.assertTrue(self.runtime.tick())
        self.assertFalse(self.fixture.engine.present(previous))
        self.assertEqual(self.runtime.last_source_index, 2)

    def test_video_seek_and_exact_step_bind_matching_frame_pairs(self):
        clip = SimpleNamespace(fps=Fraction(30), frame_count=30,
            frame=lambda i: np.full((32, 32, 3), i, np.uint8), close=lambda: None)
        self.runtime.load_clip(clip)
        self.event("video")
        self.assertTrue(self.runtime.tick())
        self.runtime.playback.pause()
        old_pair = self.runtime.last_pair
        self.runtime.seek(23)
        self.assertFalse(self.fixture.engine.present(old_pair))
        self.assertTrue(self.runtime.tick())
        self.assertEqual(self.runtime.last_source_index, 23)
        np.testing.assert_array_equal(self.runtime.last_pair.output_rgb, 23)
        self.runtime.step_video(-1)
        self.assertTrue(self.runtime.tick())
        self.assertEqual(self.runtime.last_source_index, 22)

        self.event(None, 1)
        self.assertTrue(self.runtime.tick())
        self.assertEqual(self.runtime.last_pair.scale, 4)
        self.assertEqual(self.runtime.last_source_index, 22)

    def test_status_separates_processing_and_new_frame_rates(self):
        self.assertTrue(self.runtime.tick())
        self.now = 2.0
        status = self.runtime.status()
        self.assertEqual(status["processed_source_frames"], 1)
        self.assertEqual(status["submitted_new_frames"], 1)
        self.assertEqual(status["processing_fps"], 0.5)
        self.assertEqual(status["submitted_pair_fps"], 0.5)

    def test_idle_repaints_surface_and_cannot_count_as_processing(self):
        self.runtime.tick()
        self.event("idle")
        draws = []
        self.runtime.tick(decorate=lambda pixels: draws.append(pixels.shape))
        self.assertEqual(draws, [(720, 1280, 4)])
        self.assertEqual(self.runtime.status()["window_submitted_pairs"], 0)


if __name__ == "__main__":
    unittest.main()
