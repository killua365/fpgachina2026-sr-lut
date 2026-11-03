from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from hdmi_display.engine import Engine
from hdmi_display.profiles import LEGACY, NEXYS, manifest_profile
from hdmi_display.runtime import Runtime
from hdmi_display.workspace import Workspace
from hdmi_display.compositor import Composition
from hdmi_display.controls import ControlEvent
from hdmi_display.profiling import evaluate
from hdmi_display.media import import_video
from roi_magnifier.geometry import Rect
from test_hdmi_engine import Display, GPIO, Session
from test_hdmi_runtime import Camera, Controls
import test_hdmi_profiling


class DualDisplay(Display):
    def __init__(self, overlay, *, mode):
        super().__init__(overlay)
        self.mode = mode
        self.last_pixels = None

    def acquire(self):
        return SimpleNamespace(pixels=np.zeros((self.mode.height, self.mode.width, 4), np.uint8))

    def submit(self, lease, *, identity=None):
        self.last_pixels = lease.pixels.copy()
        self.identity = identity


class ProfileIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.loads = []
        def load(path, scale):
            self.loads.append(scale)
            return SimpleNamespace(axi_dma_0=SimpleNamespace(mmio=object()))
        self.engine = Engine({2: 'x2', 4: 'x4'}, profile=NEXYS, loader=load,
            display_factory=DualDisplay, session_factory=Session,
            mode_gpio_factory=lambda **k: GPIO(), scale_gpio_factory=lambda **k: GPIO())
        self.halt = patch('hdmi_display.engine.halt_frame_dma')
        self.halt.start()
        self.addCleanup(self.halt.stop)
        self.runtime = Runtime(self.engine, camera=Camera(), controls=Controls())
        self.addCleanup(self.runtime.close)

    def test_manifest_needs_complete_matching_dual_profile(self):
        self.assertEqual(manifest_profile({'mode': LEGACY.hdmi.name}), LEGACY)
        manifest = {'mode': NEXYS.hdmi.name, 'business_profile': NEXYS.metadata()}
        self.assertEqual(manifest_profile(manifest), NEXYS)
        for key, value in [('input_size', [320, 180]), ('source_fps', 60), ('dp_mode', None)]:
            broken = deepcopy(manifest)
            broken['business_profile'][key] = value
            with self.assertRaises(ValueError):
                manifest_profile(broken)
        with self.assertRaises(ValueError):
            manifest_profile({'mode': NEXYS.hdmi.name})
        with self.assertRaises(ValueError):
            Engine({2: 'x2', 4: 'x4'}, profile=NEXYS, display_mode=LEGACY.hdmi)

    def test_camera_roi_and_import_share_profile_geometry(self):
        with patch('hdmi_display.runtime.CameraSource') as camera:
            Runtime(self.engine)
            camera.assert_called_once_with(input_size=(480, 270), fps=30)
        image = np.zeros((700, 900, 3), np.uint8)
        self.runtime.start(image)
        self.assertEqual(self.runtime.roi, Rect(211, 215, 478, 270))
        with self.assertRaisesRegex(ValueError, 'Import geometry'):
            import_video('unused.mp4', 'unused', profile=NEXYS, width=320, height=180)
        with self.assertRaisesRegex(ValueError, 'Clip geometry'):
            self.runtime.load_clip(SimpleNamespace(width=320, height=180))
        self.assertIsNone(self.runtime.playback)

    def test_real_output_sizes_repaint_and_same_scale_mode_reuse(self):
        source = np.arange(270 * 480 * 3, dtype=np.uint8).reshape(270, 480, 3)
        self.runtime.start(source)
        self.runtime.tick()
        self.assertEqual(self.runtime.last_pair.output_rgb.shape, (540, 960, 3))
        self.assertEqual(self.engine.display.last_pixels.shape, (1080, 1920, 4))
        self.runtime._control_event(ControlEvent('video', 0, True))
        self.assertEqual(self.loads, [2])
        self.runtime._control_event(ControlEvent('image', 1, True))
        self.runtime.tick()
        pair = self.runtime.last_pair
        self.assertEqual(pair.output_rgb.shape, (1080, 1920, 3))
        before = pair.output_rgb.copy()
        count = self.engine.processed_frames
        self.runtime.set_composition(Composition(layout='split'))
        self.runtime.tick()
        self.assertEqual(self.engine.processed_frames, count)
        np.testing.assert_array_equal(pair.output_rgb, before)
        self.assertEqual(self.engine.display.identity, (pair.token.generation, pair.token.sequence))

    def test_1080_ui_hit_boxes_wipe_and_draw_use_same_canvas(self):
        self.runtime.start(np.zeros((270, 480, 3), np.uint8))
        ui = Workspace(self.runtime, [], '.')
        ui._press((210, 970))
        self.assertEqual(self.runtime.composition.layout, 'split')
        self.runtime.set_composition(Composition(layout='wipe'))
        self.runtime.tick()
        box = ui.content_bounds()
        ui._press((box.x + box.width * .75, box.y + box.height * .5))
        self.assertEqual(self.runtime.composition.wipe_fraction, 0.75)
        ui.selecting = True
        self.assertLessEqual(ui.viewport().bounds.right, 1920)
        self.runtime.tick(ui.draw)
        canvas = self.engine.display.last_pixels
        np.testing.assert_array_equal(canvas[-1, -1], [17, 23, 32, 0])
        for _, _, rect in ui.buttons():
            self.assertLessEqual(rect.right, 1920)
            self.assertLessEqual(rect.bottom, 1080)

    def test_30hz_pynq_profile_cannot_claim_nexys_fps(self):
        samples = test_hdmi_profiling.ProfilingTests().samples()
        samples[-1]['display']['scan_frames'] = 1450
        result = evaluate(samples, 15, expected_refresh_hz=30)
        self.assertTrue(result['passed'])
        self.assertEqual(result['refresh_hz'], 30)
        self.assertIsNone(result['nexys_new_display_fps'])
        self.assertFalse(result['n6_passed'])
        self.assertFalse(evaluate(samples, 15)['passed'])


if __name__ == '__main__':
    unittest.main()
