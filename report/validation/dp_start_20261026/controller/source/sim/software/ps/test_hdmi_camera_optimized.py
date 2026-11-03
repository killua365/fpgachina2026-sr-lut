"""Exact capture pixels, direct display ownership, and deferred validation."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import cv2
import numpy as np
from hdmi_display import pixels
from hdmi_display.camera import prepare_camera_frame
from hdmi_display.compositor import Composition
from hdmi_display.engine import Engine
from hdmi_display.frames import FramePair, BorrowedFramePair
from hdmi_display.workspace import Workspace
from hdmi_display.scanout import Scanout, FixedTiming, MODE_1080P30
from sr_lut_x4_driver import DriverError
from test_hdmi_engine import GPIO, Session
from test_hdmi_scanout import Buffer, Registers


class DirectSession(Session):
    def __init__(self, *args, buffer_count=1, external_output_buffers=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.buffer_count = buffer_count
        self.buffer_epochs = [0]*buffer_count
        self.buffers = external_output_buffers

    def run_camera_direct(self, source, index, overlap):
        self.buffer_epochs[index] += 1
        frame = self.buffers[index]
        frame[..., :3] = np.repeat(np.repeat(source, 4, 0), 4, 1)
        frame[..., 3] = 0
        if overlap:
            overlap()
        return SimpleNamespace(image=frame,pack_seconds=0,dma_and_pl_seconds=0,
            unpack_seconds=0,total_seconds=0),index,self.buffer_epochs[index]


class DirectCameraTests(unittest.TestCase):
    def setUp(self):
        self.dma = Registers()
        self.buffers = []
        def allocate(**kw):
            result = np.zeros(kw['shape'],kw['dtype']).view(Buffer)
            result.physical_address = 0x10000000 + len(self.buffers)*0x1000000
            result.flushes,result.freed = 0,False
            self.buffers.append(result)
            return result
        self.display = Scanout(self.dma,Registers(),FixedTiming(Registers(),MODE_1080P30),
            allocate,mode=MODE_1080P30,timeout=.1,sleep=lambda _: self.dma.frame_boundary())
        self.engine = Engine({2:'x2',4:'x4'},loader=lambda *a:SimpleNamespace(
            axi_dma_0=SimpleNamespace(mmio=None)),display_factory=lambda *a,**k:self.display,
            session_factory=DirectSession,display_mode=MODE_1080P30,
            mode_gpio_factory=lambda **k:GPIO(),scale_gpio_factory=lambda **k:GPIO())
        stop = patch('hdmi_display.engine.halt_frame_dma')
        stop.start()
        self.addCleanup(stop.stop)
        self.addCleanup(self.engine.close)
        self.engine.select('camera',1)

    def process(self, value, overlap=None):
        return self.engine.process(np.full((270,480,3),value,np.uint8),
            borrow_camera=True,camera_direct=True,camera_overlap=overlap)

    def test_checked_copy_matches_compositor_and_rejects_discarded_pixel_corruption(self):
        self.engine.camera_copy_enabled=True
        self.display.cacheable=False
        rng=np.random.default_rng(1812)
        raw=rng.integers(0,256,(1080,1920,4),np.uint8)
        raw[...,3]=0
        source=rng.integers(0,256,(270,480,3),np.uint8)
        session=SimpleNamespace(output_buffers=[raw],closed=False,buffer_epochs=[1])
        rt=SimpleNamespace(engine=self.engine,last_pair=object(),roi=None,playback=None,
            export_status={'state':'idle'},import_status={'state':'idle'}, camera_frozen=False,
            snapshot_status=lambda: {'state':'idle'}, composition=Composition())
        ui=Workspace(rt,[],'.',session_manual=True)
        slots=set()
        for index,layout in enumerate(['sr']*12+['split']*6+['sr']*6):
            rt.composition=Composition(layout=layout)
            raw[...,:3]=rng.integers(0,256,(1080,1920,3),np.uint8)
            if index==6: ui.pointer=(83,400)
            if index==9: ui.message='Toolbar changed'
            pair=BorrowedFramePair(self.engine.generations.issue(),source,raw[...,:3],4,0,session,0,1)
            rt.last_pair=pair
            expected=np.zeros_like(raw)
            self.engine.compositor.compose(pair,expected,rt.composition,compare_layout=ui.compare_layout())
            ui._draw_uncached(expected)
            self.assertTrue(self.engine.present(pair,Composition(layout=layout),ui.draw))
            slots.add(self.display.pending)
            np.testing.assert_array_equal(self.display.buffers[self.display.pending],expected)
            self.dma.frame_boundary()
        self.assertEqual(slots,{0,1,2})
        # Full SR must check even words hidden by retained opaque toolbars.
        for y,x in ((0,0),(1079,1919),(500,900)):
            raw[y,x,3]=128
            pair=BorrowedFramePair(self.engine.generations.issue(),source,raw[...,:3],4,0,session,0,1)
            before=self.display.submitted
            with self.assertRaises(DriverError):self.engine.present(pair,Composition(layout='sr'),ui.draw)
            self.assertEqual(self.display.submitted,before)
            self.assertIsNone(self.display.writing)
            raw[y,x,3]=0
            self.engine.faulted=False  # Independent injected failure on the fake hardware.
        raw[-1,-1,3]=128  # Omitted by half-size RGB sampling; must still fail.
        with self.assertRaises(DriverError):self.engine.present(pair,Composition(layout='split'),ui.draw)

    def test_rotation_overlap_and_repaint_regenerate_clean_sr(self):
        first = self.process(11)
        source = first.source_rgb.copy()
        second = self.process(29,lambda:self.engine.present(first,Composition(layout='sr'),
            lambda p:p.__setitem__((slice(0,10),slice(None)),0)))
        self.assertNotEqual(first.buffer_index,second.buffer_index)
        self.assertEqual(first.output_rgb[0,0,0],0)
        np.testing.assert_array_equal(first.source_rgb,source)
        self.engine.present(second,Composition(layout='sr'))
        self.dma.frame_boundary()
        # Repainting an already decorated direct frame must never reuse UI as
        # source pixels, nor claim another camera identity.
        self.engine.present(second,Composition(layout='split'))
        self.assertEqual(self.display.pending_identity,(second.token.generation,second.token.sequence))
        self.assertTrue(second.session.closed)
        self.assertFalse(any(b.freed for b in self.buffers))

    def test_corrupt_last_x_byte_never_reaches_vdma(self):
        pair = self.process(17)
        pair.display_lease.pixels[-1,-1,3]=1
        before = self.display.submitted
        with self.assertRaises(DriverError):
            self.engine.present(pair,Composition(layout='sr'))
        self.assertEqual(self.display.submitted,before)
        self.assertTrue(self.engine.faulted)

    def test_cancel_releases_ready_reservation_without_freeing_scanout(self):
        pair = self.process(5)
        self.engine.invalidate_source()
        self.assertFalse(self.display.reserved)
        self.assertFalse(self.engine.present(pair,Composition(layout='sr')))
        self.assertFalse(any(b.freed for b in self.buffers))

    def test_split_partial_repaint_matches_full_and_invalidates_for_pointer_change(self):
        rt=SimpleNamespace(engine=self.engine,last_pair=object(),roi=None,playback=None,
            export_status={'state':'idle'},import_status={'state':'idle'}, camera_frozen=False,
            snapshot_status=lambda: {'state':'idle'}, composition=Composition(layout='split'))
        ui=Workspace(rt,[],'.',session_manual=True)
        config=Composition(layout='split')
        with patch.object(self.display,'_flush',wraps=self.display._flush) as flush:
            for value in range(6):
                source=np.full((270,480,3),17+value,np.uint8)
                output=np.full((1080,1920,3),29+value,np.uint8)
                pair=FramePair(self.engine.generations.issue(),source,output,4,0)
                rt.last_pair=pair
                if value==4: ui.pointer=(33,44)
                expected=np.zeros((1080,1920,4),np.uint8)
                self.engine.compositor.compose(pair,expected,config,compare_layout=ui.compare_layout())
                ui._draw_uncached(expected)
                self.engine.present(pair,config,ui.draw)
                np.testing.assert_array_equal(self.display.buffers[self.display.pending],expected)
                dirty=flush.call_args.args[1]
                self.assertEqual(dirty,[(0,2),(270,810)] if value in (2,3) else None)
                self.dma.frame_boundary()


@unittest.skipUnless(pixels._native is not None and hasattr(pixels._native,'hdmi_camera_area'), 'ARM native library required')
class NativeAreaTests(unittest.TestCase):
    def test_area_pixels_match_opencv_with_padding_patterns_and_random_frames(self):
        rng=np.random.default_rng(1721)
        for i in range(12):
            backing=rng.integers(0,256,(480,648,3),np.uint8)
            source=backing[:,:640]
            if i<3: source[:]=i*127
            if i==3: source[:]=np.indices((480,640)).sum(axis=0)[...,None]%2*255
            expected=cv2.cvtColor(cv2.resize(source,(360,270),interpolation=cv2.INTER_AREA),cv2.COLOR_BGR2RGB)
            actual,region=prepare_camera_frame(source,480,270)
            self.assertEqual((region.x,region.width),(60,360))
            np.testing.assert_array_equal(actual[:,60:420],expected)
            np.testing.assert_array_equal(actual[:,:60],0)
            np.testing.assert_array_equal(actual[:,420:],0)


if __name__=='__main__': unittest.main()
