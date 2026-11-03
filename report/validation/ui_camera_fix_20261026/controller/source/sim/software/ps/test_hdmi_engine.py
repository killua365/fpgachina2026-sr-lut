from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from hdmi_display.engine import Engine
from roi_magnifier.geometry import Rect


class GPIO:
    def write_leds(self, value):
        self.leds = value

    def read_buttons(self):
        return 2

    def read_switches(self):
        return 1


class Display:
    def __init__(self, overlay, **options):
        self.stopped = False
        self.cacheable = options.get('cacheable', True)

    def start(self):
        pass

    def stop(self):
        self.stopped = True

    def acquire(self):
        return SimpleNamespace(pixels=np.zeros((720, 1280, 4), dtype=np.uint8))

    def submit(self, lease, *, identity=None):
        pass

    def discard(self, lease):
        pass


class Session:
    instances = []

    def __init__(self, overlay, width, height, *, scale, **kwargs):
        self.width, self.height, self.scale = width, height, scale
        self.closed = False
        self.instances.append(self)

    def close(self):
        self.closed = True

    def run_rgb(self, source):
        output = np.repeat(np.repeat(source, self.scale, 0), self.scale, 1)
        return SimpleNamespace(image=output, pack_seconds=0.001, dma_and_pl_seconds=0.002,
                               unpack_seconds=0.003, total_seconds=0.006)


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.loads = []
        Session.instances = []

        def load(path, scale):
            self.loads.append(scale)
            return SimpleNamespace(axi_dma_0=SimpleNamespace(mmio=object()))

        self.engine = Engine({2: "x2", 4: "x4"}, loader=load, display_factory=Display,
                             session_factory=Session, mode_gpio_factory=lambda **k: GPIO(),
                             scale_gpio_factory=lambda **k: GPIO())
        self.halt = patch("hdmi_display.engine.halt_frame_dma")
        self.halt.start()
        self.addCleanup(self.halt.stop)
        self.addCleanup(self.engine.close)

    def test_modes_reuse_same_scale_and_camera_stays_x4(self):
        self.engine.select("image", 0)
        self.engine.select("video", 2)
        self.assertEqual(self.loads, [2])
        self.engine.select("camera", 0)
        self.engine.select("camera", 2)
        self.assertEqual(self.loads, [2, 4])
        self.engine.select("idle", 0)
        self.assertEqual(self.loads, [2, 4])
        self.assertEqual(self.engine.mode_gpio.leds, 0)
        self.engine.select("image", 0)
        self.assertEqual(self.loads, [2, 4, 2])

    def test_roi_uses_original_pixels_and_exact_halo_crop(self):
        self.engine.select("image", 1)
        image = np.arange(601 * 503 * 3, dtype=np.uint8).reshape(503, 601, 3)
        for roi in (Rect(0, 0, 1, 1), Rect(600, 502, 1, 1), Rect(200, 190, 47, 33)):
            pair, plan = self.engine.process_roi(image, roi)
            original = image[roi.y:roi.bottom, roi.x:roi.right]
            np.testing.assert_array_equal(pair.source_rgb, original)
            np.testing.assert_array_equal(pair.output_rgb, np.repeat(np.repeat(original, 4, 0), 4, 1))
            self.assertGreaterEqual(plan.process_window.width, 32)
            self.assertGreaterEqual(plan.process_window.height, 32)
        self.assertEqual(self.loads, [4])

    def test_mode_change_rejects_old_pair_and_geometry_change_closes_session(self):
        self.engine.select("image", 0)
        pair = self.engine.process(np.zeros((32, 32, 3), np.uint8))
        self.assertTrue(self.engine.present(pair))
        self.assertTrue(self.engine.present(pair))
        self.engine.process(np.zeros((33, 35, 3), np.uint8))
        self.assertTrue(Session.instances[0].closed)
        self.engine.select("video", 0)
        self.assertFalse(self.engine.present(pair))

    def test_halt_failure_prevents_overlay_replacement(self):
        self.engine.select("image", 0)
        old_display = self.engine.display
        with patch("hdmi_display.engine.halt_frame_dma", side_effect=TimeoutError("stuck")):
            with self.assertRaises(TimeoutError):
                self.engine.select("camera", 0)
        self.assertEqual(self.loads, [2])
        self.assertIs(self.engine.display, old_display)
        self.assertTrue(self.engine.faulted)

    def test_camera_buffer_policy_switch_preserves_same_scale_overlay(self):
        from hdmi_display.scanout import MODE_1080P30
        self.engine.display_mode = MODE_1080P30
        self.engine.camera_copy_enabled = True
        self.engine.select('image', 1)
        overlay = self.engine.overlay
        image_pair = self.engine.process(np.zeros((32, 32, 3), np.uint8))
        session, display = self.engine.session, self.engine.display
        self.engine.select('video', 1)
        self.assertIs(self.engine.display, display)
        self.engine.select('camera', 0)
        self.assertEqual(self.loads, [4])
        self.assertIs(self.engine.overlay, overlay)
        self.assertTrue(session.closed)
        self.assertTrue(display.stopped)
        self.assertIsNone(self.engine.session)
        self.assertFalse(self.engine.display.cacheable)
        self.assertFalse(self.engine.present(image_pair))
        camera_display = self.engine.display
        self.engine.select('camera', 2)
        self.engine.select('idle', 0)
        self.assertIs(self.engine.display, camera_display)
        self.engine.select('video', 1)
        self.assertTrue(camera_display.stopped)
        self.assertTrue(self.engine.display.cacheable)
        self.assertIs(self.engine.overlay, overlay)
        self.assertEqual(self.loads, [4])
        self.engine.select('image', 0)
        self.assertEqual(self.loads, [4, 2])

    def test_camera_cache_switch_retains_owner_if_dma_cannot_halt(self):
        from hdmi_display.scanout import MODE_1080P30
        self.engine.display_mode = MODE_1080P30
        self.engine.camera_copy_enabled = True
        self.engine.select('image', 1)
        old_overlay, old_display = self.engine.overlay, self.engine.display
        with patch('hdmi_display.engine.halt_frame_dma', side_effect=TimeoutError('stuck')):
            with self.assertRaises(TimeoutError):
                self.engine.select('camera', 1)
        self.assertEqual(self.loads, [4])
        self.assertIs(self.engine.overlay, old_overlay)
        self.assertIs(self.engine.display, old_display)
        self.assertFalse(old_display.stopped)
        self.assertTrue(self.engine.faulted)


if __name__ == "__main__":
    unittest.main()
