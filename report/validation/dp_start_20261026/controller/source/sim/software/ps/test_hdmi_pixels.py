import unittest
from unittest.mock import patch

import numpy as np

from hdmi_display.pixels import blit_rgb, unpack_rgb, fused_unpack_rgb, paint_nearest
from hdmi_display.compositor import Composition, Compositor
from hdmi_display.engine import HdmiFrameSession
from hdmi_display.frames import FramePair, FrameToken
from sr_lut_x4_driver import DriverError


class NativePixelTests(unittest.TestCase):
    def test_yuyv_matches_opencv_with_saturation_strides_and_vector_tails(self):
        import cv2
        from hdmi_display.pixels import yuyv_bgr
        rng = np.random.default_rng(1810)
        for height,width in ((1,2),(3,14),(7,16),(5,18),(480,640)):
            raw = rng.integers(0,256,(height+2,width+4,2),np.uint8)[1:-1,2:-2]
            guard = np.full((height+2,width+4,3),171,np.uint8)
            target = guard[1:-1,2:-2]
            saved=raw.copy()
            expected=guard.copy()
            expected[1:-1,2:-2] = cv2.cvtColor(raw,cv2.COLOR_YUV2BGR_YUY2)
            yuyv_bgr(raw,target)
            np.testing.assert_array_equal(guard,expected)
            np.testing.assert_array_equal(raw,saved)

    def test_input_pack_overwrites_all_x_without_changing_rgb(self):
        rng = np.random.default_rng(20261018)
        session = HdmiFrameSession.__new__(HdmiFrameSession)
        for height, width in ((1,1),(3,17),(270,480)):
            session.height, session.width = height, width
            source = rng.integers(0,256,(height,width,3),np.uint8)
            words = np.full(height*width,0xffffffff,np.uint32)
            session._pack(source,'rgb',words)
            result = words.view(np.uint8).reshape(height,width,4)
            np.testing.assert_array_equal(result[...,:3],source)
            self.assertFalse(np.any(result[...,3]))

    def test_borrowed_rgbx_view_matches_owned_rgb_for_all_compositions(self):
        rng = np.random.default_rng(20261017)
        source = rng.integers(0,256,(32,34,3),np.uint8)
        rgbx = rng.integers(0,256,(128,136,4),np.uint8)
        owned = FramePair(FrameToken(0,0), source, rgbx[...,:3].copy(), 4, 0)
        borrowed = FramePair(FrameToken(0,0), source, rgbx[...,:3], 4, 0)
        for layout in ('sr','split','wipe','baseline'):
            for pixel in (False, True):
                config = Composition(layout=layout, pixel_view=pixel, pan_x=3, pan_y=5)
                expected = np.zeros((128,136,4),np.uint8)
                actual = expected.copy()
                Compositor().compose(owned, expected, config)
                Compositor().compose(borrowed, actual, config)
                np.testing.assert_array_equal(actual, expected)

    def test_direct_rgbx_validation_includes_every_vector_lane_and_tail(self):
        from hdmi_display.pixels import validate_rgbx
        for width in (1,3,4,5,7,8,9,33):
            image = np.zeros((2,width,4),np.uint8)
            self.assertTrue(validate_rgbx(image))
            for i in range(2*width):
                image.reshape(-1,4)[i,3] = 128
                self.assertFalse(validate_rgbx(image))
                image.reshape(-1,4)[i,3] = 0

    def test_half_size_paint_actual_camera_geometry_and_vector_tails(self):
        from hdmi_display.pixels import _native
        if _native is None:
            self.skipTest('ARM native library exercised on the board')
        rng = np.random.default_rng(471018)
        for sh, sw in ((2, 14), (6, 16), (6, 18), (10, 30), (10, 32), (10, 34), (1080, 1920)):
            source = rng.integers(0, 256, (sh+2, sw+4, 3), np.uint8)[1:sh+1, 1:sw+1]
            saved = source.copy()
            dh, dw = sh//2, sw//2
            guard = np.full((dh+4, dw+6, 4), 171, np.uint8)
            expected = guard.copy()
            target = guard[1:dh+1, 3:dw+3]
            expected[1:dh+1, 3:dw+3, :3] = source[::2, ::2]
            expected[1:dh+1, 3:dw+3, 3] = 0
            self.assertTrue(paint_nearest(target, source))
            np.testing.assert_array_equal(guard, expected)
            np.testing.assert_array_equal(source, saved)

    def test_fused_paint_sampling_stride_and_guard_pixels(self):
        from hdmi_display.pixels import _native
        if _native is None or getattr(_native, 'hdmi_paint_nearest', None) is None:
            self.skipTest('New fused ARM painter exercised on the board')
        rng = np.random.default_rng(819)
        for sh, sw in ((1, 1), (2, 15), (17, 16), (13, 17), (31, 35), (33, 129)):
            source = rng.integers(0, 256, (sh+2, sw+4, 3), np.uint8)[1:sh+1, 2:sw+2]
            for dh, dw in ((sh, sw), (sh*2, sw*2), (19, 31), (1, 1)):
                guard = np.full((dh+4, dw+6, 4), 119, np.uint8)
                target = guard[2:dh+2, 3:dw+3]
                used = paint_nearest(target, source)
                supported = all(a == b or a in (2*b, 4*b) or b in (2*a, 4*a)
                                for a, b in ((dh, sh), (dw, sw)))
                self.assertEqual(used, supported)
                if not used:
                    self.assertTrue(np.all(guard == 119))
                    continue
                expected = np.full_like(guard, 119)
                ys, xs = np.arange(dh)*sh//dh, np.arange(dw)*sw//dw
                expected[2:dh+2, 3:dw+3, :3] = source[ys[:, None], xs]
                expected[2:dh+2, 3:dw+3, 3] = 0
                np.testing.assert_array_equal(guard, expected)

    def test_fused_validation_checks_vector_lanes_and_scalar_tail(self):
        for width in (1, 15, 16, 17, 31, 32, 35):
            source = np.zeros((2, width, 4), np.uint8)
            target = np.empty((2, width, 3), np.uint8)
            if fused_unpack_rgb(source, target) is None:
                self.skipTest('ARM native library exercised separately on the board')
            for index in (0, width - 1, 2 * width - 1):
                source.reshape(-1, 4)[index, 3] = 1
                self.assertFalse(fused_unpack_rgb(source, target))
                source.reshape(-1, 4)[index, 3] = 0
            self.assertTrue(fused_unpack_rgb(source, target))

    def test_panel_stride_singletons_and_x_byte_preserved(self):
        rng = np.random.default_rng(810)
        for h, w in ((32, 35), (1, 1), (1, 33), (37, 1)):
            canvas = np.full((h + 8, w + 14, 4), 79, np.uint8)
            source = rng.integers(0, 256, (h + 2, w + 4, 3), np.uint8)[1:1+h, 2:2+w]
            target = canvas[3:3+h, 5:5+w]
            blit_rgb(target, source)
            np.testing.assert_array_equal(target[..., :3], source)
            self.assertTrue(np.all(canvas[..., 3] == 79))
            self.assertTrue(np.all(canvas[:3] == 79))

    def test_unpack_owns_result_and_preserves_rgb_order(self):
        source = np.arange(32 * 35 * 4, dtype=np.uint8).reshape(32, 35, 4)
        expected = source[..., :3].copy()
        result = unpack_rgb(source)
        source.fill(0)
        np.testing.assert_array_equal(result, expected)
        self.assertTrue(result.flags.c_contiguous)
        with self.assertRaises(ValueError):
            unpack_rgb(source, source.reshape(-1)[:32*35*3].reshape(32, 35, 3))

    def test_session_retains_full_word_validation(self):
        session = HdmiFrameSession.__new__(HdmiFrameSession)
        session.output_height, session.output_width, session.output_pixels = 32, 35, 1120
        session.top_byte_validation = 'full'
        session.frame_index = 0
        words = np.zeros(1120, dtype=np.uint32)
        words[-1] = 0x01000000
        with self.assertRaises(DriverError):
            session._unpack(words, 'rgb')
        words[-1] = 0x00332211
        image, _, _, checked, full, _ = session._unpack(words, 'rgb')
        self.assertEqual(checked, 1120)
        self.assertTrue(full)
        self.assertEqual(image[-1, -1].tolist(), [17, 34, 51])

    def test_composition_matches_numpy_copies_for_all_layouts_and_pans(self):
        rng = np.random.default_rng(812)
        source = rng.integers(0, 256, (33, 35, 3), np.uint8)
        output = rng.integers(0, 256, (132, 140, 3), np.uint8)
        pair = FramePair(FrameToken(0, 0), source, output, 4, 0)
        compositor = Compositor()
        def copy(target, source):
            target[..., :3] = source
        for layout in ('sr', 'split', 'baseline', 'wipe'):
            for local in (False, True):
                for fraction in (0.0, 0.01, 0.5, 1.0):
                    config = Composition(layout=layout, pixel_view=local, pan_x=19,
                                         pan_y=23, wipe_fraction=fraction)
                    actual = np.empty((71, 129, 4), np.uint8)
                    expected = np.empty_like(actual)
                    compositor.compose(pair, actual, config)
                    with patch('hdmi_display.compositor.blit_rgb', side_effect=copy), \
                         patch('hdmi_display.compositor.paint_nearest', return_value=False):
                        compositor.compose(pair, expected, config)
                    np.testing.assert_array_equal(actual, expected)

    def test_fused_layout_clears_old_margins_and_matches_portable_fit(self):
        rng = np.random.default_rng(820)
        def simulated_paint(target, source):
            dh, dw = target.shape[:2]
            sh, sw = source.shape[:2]
            target[..., :3] = source[(np.arange(dh)*sh//dh)[:, None], np.arange(dw)*sw//dw]
            target[..., 3] = 0
            return True
        for sh, sw in ((33, 35), (31, 97), (180, 320), (32, 480)):
            source = rng.integers(0, 256, (sh, sw, 3), np.uint8)
            for scale in (2, 4):
                output = rng.integers(0, 256, (sh*scale, sw*scale, 3), np.uint8)
                pair = FramePair(FrameToken(0, 0), source, output, scale, 0)
                for layout in ('sr', 'baseline', 'split', 'wipe'):
                    config = Composition(layout=layout)
                    actual = np.full((71, 129, 4), 179, np.uint8)
                    expected = np.full_like(actual, 97)
                    with patch('hdmi_display.compositor.paint_nearest', side_effect=simulated_paint):
                        Compositor().compose(pair, actual, config)
                    with patch('hdmi_display.compositor.paint_nearest', return_value=False):
                        Compositor().compose(pair, expected, config)
                    np.testing.assert_array_equal(actual, expected)

    def test_fit_baseline_matches_two_stage_nearest_sampling(self):
        from hdmi_display.compositor import resize_nearest
        from hdmi_display.geometry import Viewport
        from roi_magnifier.geometry import Rect
        rng = np.random.default_rng(813)
        for h, w in ((33, 35), (31, 97), (180, 320), (32, 480)):
            source = rng.integers(0, 256, (h, w, 3), np.uint8)
            for scale in (2, 4):
                output = resize_nearest(source, (w*scale, h*scale))
                pair = FramePair(FrameToken(0, 0), source, output, scale, 0)
                for dh, dw in ((71, 129), (720, 1280), (511, 617)):
                    actual = np.empty((dh, dw, 4), np.uint8)
                    Compositor().compose(pair, actual, Composition(layout='baseline'))
                    rect = Viewport.fit(w*scale, h*scale, Rect(0, 0, dw, dh)).bounds
                    reference = resize_nearest(output, (rect.width, rect.height))
                    np.testing.assert_array_equal(actual[rect.y:rect.bottom, rect.x:rect.right, :3], reference)


if __name__ == '__main__':
    unittest.main()
