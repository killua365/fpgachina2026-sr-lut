import unittest
from dataclasses import replace
import numpy as np

from hdmi_display.compare_layout import CompareLayout
from hdmi_display.compositor import Composition, Compositor
from hdmi_display.frames import FramePair,FrameToken
from hdmi_display.metric_overlay import CompareHeader,ACCENT,GREEN,AMBER
from hdmi_display.pixels import fill_rgbx


def pair(width=480,height=270,scale=4):
    source=np.arange(width*height*3,dtype=np.uint8).reshape(height,width,3)
    return FramePair(FrameToken(1,0),source,source.repeat(scale,0).repeat(scale,1),scale,0)


class CompareLayoutTests(unittest.TestCase):
    def test_word_fill_is_byte_identical_and_keeps_strided_neighbors(self):
        for color in (0,(17,23,32,0),(0,255,127,0)):
            actual=np.full((11,17,4),73,np.uint8);expected=actual.copy()
            expected[2:9,3:13]=color
            fill_rgbx(actual[2:9,3:13],color)
            fill_rgbx(actual[:0],color)
            np.testing.assert_array_equal(actual,expected)
        actual.flags.writeable=False
        with self.assertRaises(ValueError):fill_rgbx(actual)

    def test_all_shapes_keep_captions_and_fps_outside_pictures_and_toolbar(self):
        for w,h in ((128,128),(57,86),(1,32),(480,1),(480,270)):
            p=pair(w,h)
            for width,height in ((1920,1080),(1280,720)):
                for mode in ('image','video','camera'):
                    for pixel_view in (False,True):
                        for toolbar in (False,True):
                            with self.subTest(size=(w,h),mode=mode,width=width,pixel=pixel_view,toolbar=toolbar):
                                layout=CompareLayout.build(p,width,height,mode,pixel_view,toolbar)
                                bottom=round(height*625/720) if toolbar else height
                                for caption,view in zip(layout.captions,layout.views):
                                    self.assertLessEqual(caption.bottom,view.y)
                                    self.assertGreaterEqual(caption.x,0)
                                    self.assertLessEqual(caption.right,width)
                                    self.assertLessEqual(view.bottom,bottom)
                                if layout.fps:
                                    self.assertGreaterEqual(layout.fps.y,max(v.bottom for v in layout.views))
                                    self.assertLessEqual(layout.fps.bottom,bottom)

    def test_camera_fit_retains_exact_native_fast_path_band(self):
        layout=CompareLayout.build(pair(),1920,1080,'camera')
        self.assertTrue(layout.native_split)
        self.assertEqual([(v.x,v.y,v.width,v.height) for v in layout.views],
                         [(0,270,960,540),(960,270,960,540)])

    def test_pixel_letterbox_is_identical_in_both_panels(self):
        p=pair(8,12);layout=CompareLayout.build(p,1920,1080,'image',True,False)
        target=np.empty((1080,1920,4),np.uint8)
        Compositor().compose(p,target,Composition(pixel_view=True),compare_layout=layout)
        np.testing.assert_array_equal(target[:,:960],target[:,960:])

    def test_pixel_view_shared_pan_limits_and_matching_baseline_phase(self):
        p=pair();layout=CompareLayout.build(p,1280,720,'camera',True,True)
        config=Composition(pixel_view=True,pan_x=17,pan_y=19)
        normalized=Compositor.normalize(p,1280,720,replace(config,pan_x=9999,pan_y=9999),layout)
        self.assertEqual(normalized.pan_x,p.output_rgb.shape[1]-layout.views[0].width)
        self.assertEqual(normalized.pan_y,p.output_rgb.shape[0]-layout.views[0].height)
        target=np.zeros((720,1280,4),np.uint8)
        Compositor().compose(p,target,config,compare_layout=layout)
        left,right=layout.views
        np.testing.assert_array_equal(target[left.y:left.bottom,left.x:left.right,:3],
                                      target[right.y:right.bottom,right.x:right.right,:3])
        np.testing.assert_array_equal(target[right.y:right.bottom,right.x:right.right,:3],
                                      p.output_rgb[19:19+right.height,17:17+right.width])

    def test_fps_is_single_color_at_all_rates_and_reuses_static_patches(self):
        p=pair();overlay=CompareHeader();target=np.zeros((1080,1920,4),np.uint8)
        layout=CompareLayout.build(p,1920,1080,'camera')
        patches=None
        for fps in (0.,10.7,20.,24.,36.):
            overlay.draw(target,'camera',p,{},dict(state='live',fps=fps),layout)
            if patches is not None:self.assertIs(overlay.patches,patches)
            patches=overlay.patches
            canvas=overlay._rate_patch
            self.assertTrue(np.any(np.all(canvas==ACCENT,axis=-1)))
            self.assertFalse(np.any(np.all(canvas==GREEN,axis=-1)))
            self.assertFalse(np.any(np.all(canvas==AMBER,axis=-1)))
        self.assertLess(sum(c.nbytes for _,c in patches)+overlay._rate_patch.nbytes,1920*200*4)
        # A previously displayed Fit FPS must not leak into pixel view.
        pixel_layout=CompareLayout.build(p,1920,1080,'camera',True)
        self.assertIsNone(pixel_layout.fps)
        target.fill(0)
        overlay.draw(target,'camera',p,{},dict(state='live',fps=10.7),pixel_layout)
        self.assertFalse(np.any(np.all(target==ACCENT,axis=-1)))
        self.assertIn('SR-LUT',overlay.labels)
        overlay.draw(target,'camera',p,{},dict(state='live',fps=22.6),layout)
        self.assertTrue(np.any(np.all(target==ACCENT,axis=-1)))

    def test_actual_frame_edges_anchor_normal_and_narrow_image_captions(self):
        normal=CompareLayout.build(pair(128,128),1920,1080,'image')
        self.assertEqual(normal.captions[0].x,normal.views[0].x)
        narrow=CompareLayout.build(pair(1,32),1920,1080,'image')
        for c,v in zip(narrow.captions,narrow.views):
            self.assertAlmostEqual(c.x+c.width/2,v.x+v.width/2,delta=1)

    def test_scale_badge_preserves_complete_captions_with_toolbar_shown_or_hidden(self):
        quality=dict(state='complete',nearest=dict(psnr_y_db=27.50,ssim_y=.7815),
                     sr=dict(psnr_y_db=31.32,ssim_y=.8878))
        for w,h in ((128,128),(480,270),(1,32)):
            p=pair(w,h)
            for width,height in ((1920,1080),(1280,720)):
                for pixel_view in (False,True):
                    overlay=CompareHeader()
                    for toolbar in (True,False,True):
                        with self.subTest(source=(w,h),width=width,pixel=pixel_view,toolbar=toolbar):
                            layout=CompareLayout.build(p,width,height,'image',pixel_view,toolbar)
                            canvas=np.zeros((height,width,4),np.uint8)
                            overlay.draw(canvas,'image',p,quality,{},layout)
                            badge=overlay.patches[-1][0]
                            self.assertGreaterEqual(badge.x,layout.captions[0].right)
                            self.assertLessEqual(badge.right,layout.captions[1].x)
                            for box,caption in overlay.patches[:2]:
                                np.testing.assert_array_equal(canvas[box.y:box.bottom,box.x:box.right],caption)


if __name__=='__main__':unittest.main()
