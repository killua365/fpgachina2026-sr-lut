"""Frozen-frame ownership, export integrity, continuous wipe and view state."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from hdmi_display.compositor import Composition, Compositor, resize_nearest
from hdmi_display.controls import ControlEvent
from hdmi_display.frames import FramePair, FrameToken
from hdmi_display.image_export import export_image
from hdmi_display.pixels import camera_wipe_checked
import test_hdmi_camera_pipeline as cp
import test_hdmi_workspace as ws


class CampaignRequestTests(unittest.TestCase):
    def test_request_is_explicit_run_bound_and_one_shot(self):
        from hdmi_display.presentation_performance import consume_request, CASES
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            run_id = 'a' * 32
            path = home / ('.sr_lut_presentation_' + run_id)
            self.assertFalse(consume_request(run_id, home))
            path.write_text('b' * 32)
            self.assertFalse(consume_request(run_id, home))
            self.assertFalse(consume_request('../unsafe', home))
            path.write_text(run_id)
            self.assertTrue(consume_request(run_id, home))
            self.assertFalse(consume_request(run_id, home))
        self.assertLess(sum(c[2] + 30 for c in CASES), 1200)


class FreezeTests(unittest.TestCase):
    def setUp(self):
        self.f = cp.CameraPipelineTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.rt = self.f.runtime
        self.f.frame(1); self.rt.tick()
        # The fake DMA session exposes the production allocation interface.
        self.rt.engine.session.output_buffers = self.rt.engine.session.buffers
        self.f.frame(2); self.rt.tick()

    def test_snapshot_owns_last_displayed_pair_and_does_not_count_repaints(self):
        old = self.rt.last_pair
        token = old.token
        processed, submitted = self.rt.processed_source_frames, self.rt.submitted_new_frames
        self.rt.freeze_camera()
        frozen = self.rt.last_pair
        self.assertEqual(frozen.token, token)
        self.assertEqual(self.rt.last_source_index, 1)
        self.assertIsNone(self.rt._camera_pending)
        self.assertFalse(np.shares_memory(frozen.output_rgb, old.output_rgb))
        self.assertFalse(frozen.output_rgb.flags.writeable)
        for buffer in self.rt.engine.session.buffers:
            buffer[...] = 211
        for n in range(3,13):
            self.f.frame(n)
            self.rt.request_redraw()
            self.assertFalse(self.rt.tick())
        np.testing.assert_array_equal(frozen.source_rgb, 1)
        np.testing.assert_array_equal(frozen.output_rgb, 1)
        self.assertEqual((processed,submitted), (self.rt.processed_source_frames,self.rt.submitted_new_frames))

    def test_resume_discards_old_capture_and_restarts_identity(self):
        self.rt.clock = lambda: 50
        self.rt.freeze_camera()
        old = self.rt.last_pair.token
        self.rt.resume_camera()
        processed = self.rt.processed_source_frames
        self.f.frame(49); self.rt.tick()
        self.assertEqual(self.rt.processed_source_frames, processed)
        self.f.frame(51); self.rt.tick(); self.rt.tick()
        self.assertEqual(self.rt.last_source_index, 51)
        self.assertGreater(self.rt.last_pair.token.generation, old.generation)
        self.assertFalse(self.rt.camera_frozen)

    def test_mode_change_releases_freeze_and_resets_view(self):
        self.rt.freeze_camera()
        self.rt.set_composition(Composition(layout='wipe',pixel_view=True,wipe_fraction=.2))
        self.f.controls.events.append(ControlEvent('image',0,True))
        self.rt.tick()
        self.assertFalse(self.rt.camera_frozen)
        self.assertIsNone(self.rt.frozen_metadata)
        self.assertFalse(self.rt.composition.pixel_view)
        self.assertEqual(self.rt.composition.wipe_fraction,.5)

    def test_invalid_borrowed_frame_or_hidden_corruption_cannot_freeze(self):
        pair = self.rt.last_pair
        self.rt.engine.session.buffers[pair.buffer_index][0,0,3] = 1
        with self.assertRaisesRegex(RuntimeError, 'non-zero X'):
            self.rt.freeze_camera()
        self.assertFalse(self.rt.camera_frozen)

    def test_no_frame_does_not_freeze(self):
        self.rt.last_pair = None
        with self.assertRaisesRegex(RuntimeError,'Wait for'):
            self.rt.freeze_camera()

    def test_camera_restart_while_frozen_keeps_owned_snapshot(self):
        self.rt.freeze_camera()
        pair = self.rt.last_pair
        self.f.frame(3,generation=2); self.rt.tick()
        self.assertIs(self.rt.last_pair,pair)
        self.assertTrue(self.rt.status()['camera_frozen'])

    def test_snapshot_exports_exact_same_frame_with_comparison(self):
        self.rt.freeze_camera()
        pair = self.rt.last_pair
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'saved'
            self.rt.save_snapshot(target,{'manifest_sha256':'test'})
            self.rt.snapshot_export.thread.join(10)
            self.assertEqual(self.rt.snapshot_status()['state'],'complete')
            report=json.loads((target/'export.json').read_text())
            self.assertEqual(report['frame_token'],vars(pair.token))
            self.assertEqual({x['path'] for x in report['files']},
                             {'source.png','sr.png','nearest.png','comparison.png'})
            for name,array in (('source.png',pair.source_rgb),('sr.png',pair.output_rgb)):
                with Image.open(target/name) as image:
                    np.testing.assert_array_equal(np.asarray(image),array)
            self.assertTrue(report['png_readback_exact'])

    def test_saving_blocks_resume_and_duplicate_but_can_cancel(self):
        self.rt.freeze_camera()
        entered, release=threading.Event(), threading.Event()
        def saving(*args,**kwargs):
            entered.set(); release.wait(5)
            if kwargs['cancelled']():raise InterruptedError()
            return Path('saved/export.json')
        with tempfile.TemporaryDirectory() as directory, patch('hdmi_display.snapshot.export_image',saving):
            self.rt.save_snapshot(Path(directory)/'saved')
            try:
                self.assertTrue(entered.wait(5))
                with self.assertRaises(RuntimeError):self.rt.resume_camera()
                with self.assertRaises(RuntimeError):self.rt.save_snapshot(Path(directory)/'other')
                self.rt.snapshot_export.cancelled.set()
            finally:
                release.set(); self.rt.snapshot_export.thread.join(5)
            self.assertEqual(self.rt.snapshot_status()['state'],'cancelled')


class WipeTests(unittest.TestCase):
    def test_fused_wipe_cuts_padding_and_hidden_x_validation(self):
        rng=np.random.default_rng(8)
        source=rng.integers(0,256,(3,7,3),dtype=np.uint8)
        raw=rng.integers(0,256,(12,28,4),dtype=np.uint8);raw[...,3]=0
        expected_base=np.repeat(np.repeat(source,4,axis=0),4,axis=1)
        for cut in (0,1,3,4,7,13,27,28):
            padded=np.full((12,32,4),93,np.uint8); target=padded[:,2:30]
            self.assertTrue(camera_wipe_checked(raw,source,target,cut))
            np.testing.assert_array_equal(target[:,:cut,:3],expected_base[:,:cut])
            np.testing.assert_array_equal(target[:,cut:,:3],raw[:,cut:,:3])
            np.testing.assert_array_equal(target[...,3],0)
            np.testing.assert_array_equal(padded[:,:2],93)
            np.testing.assert_array_equal(padded[:,30:],93)
        raw[0,0,3]=1
        self.assertFalse(camera_wipe_checked(raw,source,target,28))
        raw[0,0,3]=0;raw[-1,-1,3]=128
        self.assertFalse(camera_wipe_checked(raw,source,target,0))

    def test_wipe_input_validation(self):
        source=np.zeros((2,3,3),np.uint8);raw=np.zeros((8,12,4),np.uint8)
        with self.assertRaises(ValueError):camera_wipe_checked(raw,source,raw,6)
        for cut in (-1,13,1.5,True):
            with self.assertRaises(ValueError):camera_wipe_checked(raw,source,raw.copy(),cut)

    def test_cached_wipe_matches_independent_reference_all_pan_phases(self):
        rng=np.random.default_rng(19)
        source=rng.integers(0,256,(5,9,3),dtype=np.uint8)
        output=rng.integers(0,256,(20,36,3),dtype=np.uint8)
        pair=FramePair(FrameToken(0,0),source,output,4,0)
        c=Compositor();baseline=np.repeat(np.repeat(source,4,0),4,1)
        for pixel_view in (False,True):
            for pan in range(8):
                cfg=Composition(layout='wipe',pixel_view=pixel_view,pan_x=pan,pan_y=pan)
                for fraction in (0,.03,.25,.5,.97,1):
                    cfg=replace(cfg,wipe_fraction=fraction)
                    actual=np.empty((13,25,4),np.uint8); expected=np.zeros_like(actual)
                    before=c._view(baseline if pixel_view else source,25,13,cfg)
                    after=c._view(output,25,13,cfg)
                    h,w=before.shape[:2];x,y=(25-w)//2,(13-h)//2;cut=round(w*fraction)
                    expected[y:y+h,x:x+cut,:3]=before[:,:cut]
                    expected[y:y+h,x+cut:x+w,:3]=after[:,cut:]
                    c.compose(pair,actual,cfg)
                    np.testing.assert_array_equal(actual,expected)
                    planes=c._wipe_planes
                    c.compose(pair,actual,replace(cfg,wipe_fraction=.4))
                    self.assertIs(c._wipe_planes,planes)
        c.compose(pair,actual,Composition(layout='sr'))
        self.assertIsNone(c._wipe_planes)

    def test_cancelled_export_leaves_no_completed_or_partial_directory(self):
        source=np.zeros((2,3,3),np.uint8)
        pair=FramePair(FrameToken(0,0),source,np.zeros((8,12,3),np.uint8),4,0)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(InterruptedError):
                export_image(pair,Path(directory)/'saved',cancelled=lambda:True)
            self.assertEqual(list(Path(directory).iterdir()),[])


class ViewInteractionTests(unittest.TestCase):
    def setUp(self):
        self.f=ws.WorkspaceTests(); self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.rt,self.ui=self.f.rt,self.f.ui

    def test_view_label_highlight_and_reset_do_not_change_roi(self):
        roi=self.rt.roi
        self.ui.action('wipe');self.ui.action('local')
        self.assertTrue(self.ui.active_button('wipe'))
        self.assertIn('View: 1:1',[label for _,label,_ in self.ui.buttons()])
        self.rt.set_composition(replace(self.rt.composition,wipe_fraction=.1))
        self.ui.action('view_reset')
        self.assertFalse(self.rt.composition.pixel_view)
        self.assertEqual(self.rt.composition.wipe_fraction,.5)
        self.assertEqual(self.rt.roi,roi)

    def test_wipe_drag_uses_content_bounds_and_release_coordinate(self):
        self.ui.action('wipe');box=self.ui.content_bounds();y=300
        self.f.mouse(box.x-1,y,[dict(button=272,pressed=True,x=box.x-1,y=y)])
        self.assertIsNone(self.ui.drag)
        start=box.x+box.width//4
        self.f.mouse(start,y,[dict(button=272,pressed=True,x=start,y=y)],buttons=[272])
        self.assertAlmostEqual(self.rt.composition.wipe_fraction,(start-box.x)/box.width)
        self.f.mouse(box.right+50,y,buttons=[272]);self.assertEqual(self.rt.composition.wipe_fraction,1)
        self.f.mouse(box.x,y,[dict(button=272,pressed=False,x=box.x,y=y)])
        self.assertEqual(self.rt.composition.wipe_fraction,0);self.assertIsNone(self.ui.drag)

    def test_wipe_line_reaches_visible_picture_bottom_in_all_modes(self):
        from hdmi_display.scanout import MODE_1080P30,MODE_720P60
        accent=np.array((30,230,190,0),np.uint8)
        for w,h in ((480,270),(128,128)):
            source=np.zeros((h,w,3),np.uint8)
            self.rt.last_pair=FramePair(FrameToken(0,0),source,np.zeros((h*4,w*4,3),np.uint8),4,0)
            for display in (MODE_1080P30,MODE_720P60):
                self.rt.engine.display_mode=display
                self.ui.width,self.ui.height=display.width,display.height
                for mode in ('image','video','camera'):
                    self.rt.engine.mode=mode
                    for pixel in (False,True):
                        self.rt.composition=Composition(layout='wipe',pixel_view=pixel)
                        for visible in (True,False):
                            with self.subTest(size=(w,h),display=display.name,mode=mode,pixel=pixel,toolbar=visible):
                                self.ui.visible=visible
                                canvas=np.zeros((display.height,display.width,4),np.uint8)
                                self.ui._draw_view_details(canvas)
                                box=self.ui.content_bounds()
                                bottom=min(box.bottom,self.ui.point(0,625)[1]) if visible else box.bottom
                                x=min(box.right-1,box.x+round(box.width*.5))
                                np.testing.assert_array_equal(canvas[bottom-1,x],accent)
                                self.assertFalse(np.any(np.all(canvas[bottom:]==accent,axis=-1)))

    def test_wipe_caps_and_handle_stay_inside_picture_and_above_toolbar(self):
        source=np.zeros((60,80,3),np.uint8)
        self.rt.last_pair=FramePair(FrameToken(0,0),source,np.zeros((240,320,3),np.uint8),4,0)
        accent=np.array((30,230,190,0),np.uint8)
        for visible in (True,False):
            self.ui.visible=visible
            for fraction in (0.,1.):
                self.rt.composition=Composition(layout='wipe',wipe_fraction=fraction)
                canvas=np.zeros((720,1280,4),np.uint8)
                self.ui._draw_view_details(canvas)
                box=self.ui.content_bounds()
                top=max(box.y,self.ui.point(0,73)[1]) if visible else box.y
                bottom=min(box.bottom,self.ui.point(0,625)[1]) if visible else box.bottom
                colored=np.all(canvas==accent,axis=-1)
                self.assertFalse(np.any(colored[:top]))
                self.assertFalse(np.any(colored[bottom:]))
                self.assertFalse(np.any(colored[:,:box.x]))
                self.assertFalse(np.any(colored[:,box.right:]))

    def test_dropped_event_cancels_drag_without_a_stale_release_commit(self):
        self.ui.action('wipe');box=self.ui.content_bounds();x=box.x+box.width//2
        self.f.mouse(x,300,[dict(button=272,pressed=True,x=x,y=300)],buttons=[272])
        fraction=self.rt.composition.wipe_fraction
        self.f.mouse(box.right,300,[dict(button=272,pressed=False,x=box.right,y=300)],dropped=1)
        self.assertIsNone(self.ui.drag)
        self.assertEqual(self.rt.composition.wipe_fraction,fraction)

    def test_dynamic_pan_and_wipe_do_not_rebuild_static_camera_overlay(self):
        self.rt.engine.mode='camera';self.ui.clock=lambda:1
        self.rt.composition=Composition(layout='wipe',pixel_view=True)
        canvas=np.zeros((720,1280,4),np.uint8);self.ui.draw(canvas)
        cached=self.ui._camera_ui_cache
        self.rt.composition=replace(self.rt.composition,pan_x=7,wipe_fraction=.3)
        actual,expected=np.zeros_like(canvas),np.zeros_like(canvas)
        self.ui.draw(actual);self.ui._draw_uncached(expected)
        self.assertIs(self.ui._camera_ui_cache,cached)
        np.testing.assert_array_equal(actual,expected)


if __name__=='__main__':
    unittest.main()
