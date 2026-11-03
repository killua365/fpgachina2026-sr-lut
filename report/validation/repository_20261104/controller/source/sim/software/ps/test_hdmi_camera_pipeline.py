"""Ownership, source identity and control-transition checks for camera overlap."""
from types import SimpleNamespace
import unittest
import numpy as np
from hdmi_display.camera import CameraFrame
from hdmi_display.controls import ControlEvent
from hdmi_display.runtime import Runtime
from roi_magnifier.geometry import Rect
from sr_lut_x4_driver import DriverError
import test_hdmi_engine
from test_hdmi_runtime import Camera, Controls


class CameraSession(test_hdmi_engine.Session):
    def __init__(self, overlay, width, height, *, scale, buffer_count, **kwargs):
        super().__init__(overlay, width, height, scale=scale, **kwargs)
        self.buffer_count = buffer_count
        self.buffer_epochs = [0]*buffer_count
        self.buffers = [np.zeros((height*scale, width*scale, 4), np.uint8) for _ in range(buffer_count)]
        self.index = 0
        self.inflight = False

    def run_camera(self, source, overlap):
        index = self.index
        self.buffer_epochs[index] += 1
        self.inflight = True
        self.buffers[index][..., :3] = np.repeat(np.repeat(source, 4, 0), 4, 1)
        if overlap:
            overlap()
        self.inflight = False
        self.index = 1-index
        result = SimpleNamespace(image=self.buffers[index], pack_seconds=.001,
            dma_and_pl_seconds=.002, unpack_seconds=.003, total_seconds=.006)
        return result, index, self.buffer_epochs[index]


class CameraPipelineTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_hdmi_engine.EngineTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.engine = self.fixture.engine
        self.engine.session_factory = CameraSession
        self.engine.camera_pipeline_enabled = True
        self.controls, self.camera = Controls(), Camera()
        self.runtime = Runtime(self.engine, camera=self.camera, controls=self.controls, clock=lambda: 0)
        self.addCleanup(self.runtime.close)
        self.runtime.start(np.zeros((32,32,3), np.uint8))
        self.controls.events.append(ControlEvent('camera', 0, True))
        self.scans = []
        def observe(token, mode, scale):
            self.scans.append((token, mode, scale, self.engine.session.inflight))
        self.engine.frame_observer = observe

    def frame(self, sequence, generation=1):
        self.camera.frames.append(CameraFrame(generation, sequence,
            np.full((32,32,3), sequence, np.uint8), float(sequence), .01, 640,480,Rect(0,0,32,32)))

    def test_overlap_preserves_matching_source_and_output_and_expires_old_slot(self):
        self.frame(1)
        self.assertFalse(self.runtime.tick())
        first = self.runtime._camera_pending[0]
        self.assertFalse(first.output_rgb.flags.writeable)
        self.frame(2)
        self.assertTrue(self.runtime.tick())
        np.testing.assert_array_equal(first.output_rgb, 1)
        np.testing.assert_array_equal(self.runtime.last_pair.source_rgb, 1)
        self.assertEqual(self.runtime.last_source_index, 1)
        self.assertTrue(self.scans[-1][3])
        self.frame(3)
        self.assertTrue(self.runtime.tick())
        np.testing.assert_array_equal(self.runtime.last_pair.output_rgb, 2)
        self.assertFalse(first.valid)
        with self.assertRaises(DriverError):
            self.engine.present(first)
        self.assertEqual(self.runtime.processed_source_frames, 3)
        self.assertEqual(self.runtime.submitted_new_frames, 2)
        self.assertTrue(self.runtime.tick())  # Drain ready result if capture pauses.
        self.assertEqual(self.runtime.last_source_index, 3)
        self.assertFalse(self.runtime.tick())
        self.assertEqual(self.runtime.submitted_new_frames, 3)

    def test_camera_restart_drops_pending_old_generation(self):
        self.frame(1)
        self.runtime.tick()
        prior = self.runtime._camera_pending[0]
        self.frame(2, generation=2)
        self.assertFalse(self.runtime.tick())
        self.assertEqual(self.scans, [])
        self.assertNotEqual(prior.token.generation, self.runtime._camera_pending[0].token.generation)
        self.assertTrue(self.runtime.tick())
        self.assertEqual(self.runtime.last_source_index, 2)

    def test_idle_and_scale_transition_discard_pending_and_close_borrowed_storage(self):
        self.frame(1)
        self.runtime.tick()
        pair = self.runtime._camera_pending[0]
        self.controls.events.append(ControlEvent('idle', 0, True))
        self.assertFalse(self.runtime.tick())
        self.assertIsNone(self.runtime._camera_pending)
        self.assertIsNone(self.runtime.last_pair)
        self.controls.events.append(ControlEvent('image', 0, True))
        self.runtime.tick()
        self.assertFalse(pair.valid)
        self.assertTrue(self.runtime.last_pair.output_rgb.flags.c_contiguous)


if __name__ == '__main__':
    unittest.main()
