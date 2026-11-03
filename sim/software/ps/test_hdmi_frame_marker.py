import unittest
from unittest.mock import patch
import numpy as np

from hdmi_display import frame_marker as marker
from hdmi_display.frames import FrameToken
from hdmi_display.compositor import Composition
import test_hdmi_profiles


class MarkerTests(unittest.TestCase):
    def test_known_wire_bytes_and_semantics(self):
        payload = marker.encode(FrameToken(0x12345678, 0x9abcdef0), 'video', 4)
        self.assertEqual(payload[:16].hex(), '53524c5401020401123456789abcdef0')
        canvas = np.zeros((2, 20, 4), np.uint8)
        marker.draw(canvas, FrameToken(0x12345678, 0x9abcdef0), 'video', 4)
        self.assertEqual(marker.decode(canvas), marker.FrameIdentity('video', 4, 0x12345678, 0x9abcdef0, True))
        marker.draw(canvas, FrameToken(4, 0), 'idle', 0, has_result=False)
        self.assertFalse(marker.decode(canvas).has_result)

    def test_bad_crc_bad_complement_and_incomplete_marker_rejected(self):
        pixels = np.zeros((2, 20, 4), np.uint8)
        marker.draw(pixels, FrameToken(3, 12), 'camera', 4)
        for x, y, channel in ((18, 0, 0), (5, 1, 2), (0, 0, 1)):
            broken = pixels.copy()
            broken[y, x, channel] ^= 1
            with self.assertRaises(ValueError):
                marker.decode(broken)
        broken = pixels.copy()
        broken[:, 15, :3] ^= 1  # preserve grayscale and complement, violate CRC
        with self.assertRaisesRegex(ValueError, 'CRC'):
            marker.decode(broken)
        with self.assertRaises(ValueError):
            marker.decode(pixels[:1])
        with self.assertRaises(ValueError):
            marker.encode(FrameToken(1, 2**32), 'video', 4)

    def test_composition_only_stable_on_repaint_and_no_result_on_idle(self):
        fixture = test_hdmi_profiles.ProfileIntegrationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        engine, runtime = fixture.engine, fixture.runtime
        self.assertFalse(engine.identity_marker)
        engine.identity_marker = True
        runtime.start(np.arange(480 * 270 * 3, dtype=np.uint8).reshape(270, 480, 3))
        runtime.tick()
        pair = runtime.last_pair
        source, result = pair.source_rgb.copy(), pair.output_rgb.copy()
        first = marker.decode(engine.display.last_pixels)
        runtime.set_composition(Composition(layout='sr'))
        runtime.tick(decorate=lambda pixels: pixels.fill(99))
        self.assertEqual(marker.decode(engine.display.last_pixels), first)
        np.testing.assert_array_equal(pair.source_rgb, source)
        np.testing.assert_array_equal(pair.output_rgb, result)
        engine.present_surface(lambda pixels: pixels.fill(50))
        self.assertFalse(marker.decode(engine.display.last_pixels).has_result)


if __name__ == '__main__':
    unittest.main()
