import unittest

import numpy as np

from hdmi_display.compositor import Composition, Compositor
from hdmi_display.frames import FrameGeneration, FramePair, FrameToken
from hdmi_display.geometry import Viewport
from roi_magnifier.geometry import Rect


class GeometryTests(unittest.TestCase):
    def test_letterbox_maps_to_original_not_thumbnail(self):
        view = Viewport.fit(4096, 2048, Rect(10, 20, 800, 600))
        self.assertEqual(view.bounds, Rect(10, 120, 800, 400))
        self.assertEqual(view.source_point(410, 320), (2048, 1024))
        self.assertFalse(view.contains(10, 100))
        self.assertEqual(view.source_point(900, 900), (4095, 2047))

    def test_single_pixel_roi_and_reverse_drag(self):
        view = Viewport.fit(100, 100, Rect(0, 0, 200, 200))
        self.assertEqual(view.selection((0, 0), (0, 0), 4), Rect(0, 0, 1, 1))
        self.assertEqual(view.selection((20, 20), (0, 0), 2), Rect(0, 0, 11, 11))

    def test_padding_click_and_hardware_limit_rejected(self):
        view = Viewport.fit(1000, 500, Rect(0, 0, 1000, 1000))
        with self.assertRaises(ValueError):
            view.selection((0, 0), (10, 10), 4)
        with self.assertRaises(ValueError):
            view.selection((0, 250), (999, 749), 4)

    def test_full_image_display_rect_and_bad_dimension(self):
        view = Viewport.fit(99, 51, Rect(0, 0, 640, 360))
        self.assertEqual(view.display_rect(Rect(0, 0, 99, 51)), view.bounds)
        with self.assertRaises(ValueError):
            Viewport.fit(0, 20, Rect(0, 0, 10, 10))


class FrameTests(unittest.TestCase):
    def test_outdated_and_duplicate_frames_do_not_publish(self):
        epochs = FrameGeneration()
        first, second = epochs.issue(), epochs.issue()
        self.assertTrue(epochs.accept(second))
        self.assertFalse(epochs.accept(first))
        self.assertFalse(epochs.accept(second))
        outstanding = epochs.issue()
        epochs.invalidate()
        self.assertFalse(epochs.accept(outstanding))
        self.assertTrue(epochs.accept(epochs.issue()))

    def test_fabricated_sequence_and_wrong_geometry_rejected(self):
        self.assertFalse(FrameGeneration().accept(FrameToken(0, 99)))
        with self.assertRaises(ValueError):
            FramePair(FrameToken(0, 0), np.zeros((2, 2, 3), np.uint8),
                      np.zeros((7, 8, 3), np.uint8), 4, 0)


class CompositionTests(unittest.TestCase):
    def setUp(self):
        self.source = np.zeros((4, 8, 3), np.uint8)
        self.source[:, :, 0] = np.arange(8)
        self.sr = np.repeat(np.repeat(self.source, 4, axis=0), 4, axis=1)
        self.pair = FramePair(FrameToken(0, 0), self.source, self.sr, 4, 0)
        self.compositor = Compositor()

    def test_split_has_same_geometry_and_no_color_swap(self):
        dest = np.empty((20, 64, 4), np.uint8)
        self.compositor.compose(self.pair, dest, Composition())
        np.testing.assert_array_equal(dest[:, :32], dest[:, 32:])
        np.testing.assert_array_equal(dest[2:18, :32, :3], self.sr)
        self.assertTrue(np.all(dest[:, :, 3] == 0))

    def test_pixel_pan_keeps_views_aligned(self):
        dest = np.empty((8, 16, 4), np.uint8)
        self.compositor.compose(self.pair, dest, Composition(pixel_view=True, pan_x=9, pan_y=4))
        np.testing.assert_array_equal(dest[:, :8, :3], self.sr[4:12, 9:17])
        np.testing.assert_array_equal(dest[:, :8], dest[:, 8:])

    def test_virtual_baseline_crop_matches_expanded_pixels_at_all_scale_phases(self):
        source=np.arange(5*7*3,dtype=np.uint8).reshape(5,7,3)
        for scale in (2,4):
            expanded=np.repeat(np.repeat(source,scale,0),scale,1)
            pair=FramePair(self.pair.token,source,expanded,scale,0)
            for height,width in ((3,3),(7,11),(22,39)):
                for px in (-7,0,1,2,3,4,5,99):
                    for py in (-3,0,1,2,3,4,99):
                        config=Composition(layout='baseline',pixel_view=True,pan_x=px,pan_y=py)
                        actual=np.full((height,width,4),255,np.uint8)
                        self.compositor.compose(pair,actual,config)
                        x=max(0,min(px,max(0,expanded.shape[1]-width)))
                        y=max(0,min(py,max(0,expanded.shape[0]-height)))
                        view=expanded[y:y+height,x:x+width]
                        h,w=view.shape[:2]
                        expected=np.zeros_like(actual)
                        expected[(height-h)//2:(height-h)//2+h,(width-w)//2:(width-w)//2+w,:3]=view
                        np.testing.assert_array_equal(actual,expected)

    def test_wipe_preserves_corresponding_pixels(self):
        output = self.sr.copy()
        output[:, :, 1] = 99
        pair = FramePair(self.pair.token, self.source, output, 4, 0)
        dest = np.empty((16, 32, 4), np.uint8)
        self.compositor.compose(pair, dest, Composition(layout="wipe"))
        np.testing.assert_array_equal(dest[:, :16, :3], self.sr[:, :16])
        np.testing.assert_array_equal(dest[:, 16:, :3], output[:, 16:])

    def test_sources_are_not_modified(self):
        before = self.sr.copy()
        dest = np.empty((16, 32, 4), np.uint8)
        self.compositor.compose(self.pair, dest, Composition(layout="sr"))
        np.testing.assert_array_equal(self.sr, before)
        np.testing.assert_array_equal(dest[:, :, :3], before)

    def test_invalid_wipe_rejected(self):
        with self.assertRaises(ValueError):
            self.compositor.compose(self.pair, np.zeros((8, 8, 4), np.uint8),
                                    Composition(layout="wipe", wipe_fraction=float("nan")))


if __name__ == "__main__":
    unittest.main()
