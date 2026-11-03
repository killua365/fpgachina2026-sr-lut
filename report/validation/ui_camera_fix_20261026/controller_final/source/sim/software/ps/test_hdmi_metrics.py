import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import numpy as np
from hdmi_display.metrics import ImagePairs, QualityWorker, DisplayRate, quality_scores
from hdmi_display.metric_overlay import CompareHeader
from hdmi_display.frames import FramePair, FrameToken
from hdmi_display.controls import ControlEvent
from roi_magnifier.geometry import Rect
import test_hdmi_runtime

ROOT=Path(__file__).resolve().parents[3]


def records():
    return [dict(name=n,hr=f'data/golden/source_images/hr/{n}_gt.png',
        lr={'2':f'data/golden/source_images/lr_x2/{n}_gt.png',
            '4':f'data/golden/source_images/lr_x4/{n}.png'})
        for n in ('baby','bird','butterfly','head','woman')]


class MetricTests(unittest.TestCase):
    def test_all_real_pairs_and_roi_match_scale_specific_hr(self):
        pairs=ImagePairs(ROOT,records())
        for index in range(5):
            for scale in (2,4):
                lr,hr,_,_=pairs.load(index,scale)
                self.assertEqual(hr.shape,(lr.shape[0]*scale,lr.shape[1]*scale,3))
                source=lr[3:19,5:24]
                ref=hr[3*scale:19*scale,5*scale:24*scale]
                values=quality_scores(source,ref,ref,scale)
                self.assertEqual(values['sr']['psnr_y_db'],'infinity')
                self.assertAlmostEqual(values['sr']['ssim_y'],1)
                self.assertIsInstance(values['nearest']['psnr_y_db'],float)

    def test_atomic_switch_loads_new_lr_before_processing(self):
        f=test_hdmi_runtime.RuntimeTests();f.setUp()
        try:
            rt=f.runtime;rt.image_pairs=ImagePairs(ROOT,records());rt.load_paired_image(0)
            self.assertEqual(rt.image.shape,(256,256,3))
            rt.set_roi(Rect(5,7,25,25))
            np.testing.assert_array_equal(rt.quality_reference(),rt.image_reference[14:64,10:60])
            rt._control_event(ControlEvent(None,1,True))
            self.assertEqual((rt.engine.scale,rt.image.shape),(4,(128,128,3)))
            self.assertEqual(rt.roi,Rect(0,0,128,128))
            self.assertIsNone(rt.last_pair)
            self.assertFalse(rt.composition.pixel_view)
        finally: f.doCleanups()

    def test_quality_worker_drops_old_result(self):
        entered,release=threading.Event(),threading.Event()
        def compute(*_): entered.set();release.wait(3);return {'marker':1}
        worker=QualityWorker(compute)
        rgb=np.zeros((16,16,3),np.uint8)
        pair=FramePair(FrameToken(1,0),rgb,rgb.repeat(2,0).repeat(2,1),2,0)
        try:
            worker.submit(('old',),pair,pair.output_rgb)
            self.assertTrue(entered.wait(2));worker.invalidate();release.set()
            worker.submit(('new',),pair,pair.output_rgb)
            deadline=time.monotonic()+3
            while worker.status()['state']=='running' and time.monotonic()<deadline:time.sleep(.01)
            self.assertEqual(worker.status()['identity'],('new',))
            self.assertEqual(worker.status()['state'],'complete')
        finally:release.set();worker.close()

    def test_fps_window_repaints_freeze_disconnect_and_generation(self):
        rate=DisplayRate()
        for i in range(61): rate.update(i*.05,('camera',1),int(i*.05*22))
        self.assertAlmostEqual(rate.value['fps'],22,delta=.5)
        for i in range(61,121): rate.update(i*.05,('camera',1),66)
        self.assertAlmostEqual(rate.value['fps'],0,delta=1)
        self.assertIsNone(rate.update(7,('camera',1),66,'frozen')['fps'])
        self.assertIsNone(rate.update(8,('camera',2),0)['fps'])
        self.assertEqual(rate.update(9,('camera',2),0,'disconnected')['state'],'disconnected')
        self.assertLess(len(rate.points),45)

    def test_small_cached_header_changes_with_dimensions_and_signed_delta(self):
        rgb=np.zeros((16,16,3),np.uint8)
        pair=FramePair(FrameToken(1,0),rgb,rgb.repeat(4,0).repeat(4,1),4,0)
        header=CompareHeader();pixels=np.zeros((1080,1920,4),np.uint8)
        quality=dict(state='complete',sr=dict(psnr_y_db=30.,ssim_y=.9),nearest=dict(psnr_y_db=31.,ssim_y=.8))
        header.draw(pixels,'image',pair,quality,{})
        cached=header.canvas
        self.assertTrue(any(np.any(np.all(p==[255,185,75,0],axis=-1)) for _,p in header.patches))
        header.draw(pixels,'image',pair,quality,{})
        self.assertIs(header.canvas,cached)
        self.assertLess(sum(p.nbytes for _,p in header.patches),1920*200*4)
        header.draw(pixels,'camera',pair,{},dict(state='live',fps=22.))
        self.assertTrue(np.all(header.canvas[...,3]==0))


if __name__=='__main__':unittest.main()
