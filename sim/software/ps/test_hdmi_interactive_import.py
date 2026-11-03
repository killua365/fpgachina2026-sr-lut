"""Offline local picker/import integration with real codecs and a simulated Engine."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hdmi_display.file_picker import VideoPicker
from hdmi_display.media import validate_probe, sha256_file
from hdmi_display.controls import ControlEvent
from hdmi_display.profiles import NEXYS
import test_hdmi_interactive_export


class PickerTests(unittest.TestCase):
    def test_navigation_paging_filtering_and_missing_file(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name)
            (root/'child').mkdir()
            for i in range(12): (root/f'video_{i:02}.mp4').touch()
            (root/'unrelated.txt').touch()
            picker=VideoPicker([root])
            self.assertIsNone(picker.choose(0))
            self.assertEqual(len(picker.entries),13)
            self.assertEqual(picker.visible[0].name,'child')
            picker.choose(0)
            self.assertEqual(picker.visible,[])
            picker.up()
            picker.turn(1)
            selected=picker.choose(0)
            self.assertEqual(selected.suffix,'.mp4')
            selected.unlink()
            with self.assertRaises(OSError): picker.choose(0)
            picker.up()
            self.assertIsNone(picker.directory)

    def test_large_directory_is_bounded_and_path_cannot_escape_root(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name)
            media=root/'media'
            media.mkdir()
            for i in range(205): (media/f'{i}.avi').touch()
            picker=VideoPicker([media])
            picker.choose(0)
            self.assertEqual(len(picker.entries),200)
            self.assertIn('large',picker.message)
            with self.assertRaises(ValueError): picker.browse(root)


class InteractiveImportTests(unittest.TestCase):
    def setUp(self):
        self.fixture=test_hdmi_interactive_export.InteractiveExportTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.rt,self.ui,self.root=self.fixture.rt,self.fixture.ui,self.fixture.root
        self.ui.media_roots=[self.root]
        self.source=self.root/'selected.mp4'
        self.source.write_bytes(b'synthetic placeholder')
        self.old=self.rt.playback.clip

    def start(self): self.rt.start_video_import(self.source,self.root/'imported')

    def drain(self):
        deadline=time.monotonic()+30
        while self.rt.import_active and time.monotonic()<deadline:
            self.rt.tick(self.ui.draw)
            time.sleep(.005)
        self.assertFalse(self.rt.import_active)

    def test_mouse_picker_selects_file_and_mutual_exclusion(self):
        self.ui.action('open_video')
        self.ui.action('pick_0')
        # Picker entries are resolved; Windows TEMP may use an 8.3 alias.
        index=self.ui.picker.visible.index(self.source.resolve())
        self.ui.action('pick_'+str(index))
        self.assertIsNone(self.ui.picker)
        self.assertTrue(self.rt.import_active)
        self.assertIn('cancel_import',[x[0] for x in self.ui.buttons()])
        with self.assertRaises(RuntimeError): self.fixture.start()
        with self.assertRaises(RuntimeError): self.ui.action('play')
        self.ui.action('cancel_import')
        self.assertIs(self.rt.playback.clip,self.old)
        self.assertFalse(self.rt.playback.playing)
        self.fixture.start()
        with self.assertRaises(RuntimeError): self.start()
        self.rt.cancel_video_export()

    def test_physical_control_cancels_probe_before_mode_switch(self):
        proc=SimpleNamespace(returncode=None)
        proc.poll=lambda:proc.returncode
        proc.terminate=lambda:setattr(proc,'returncode',-15)
        proc.wait=lambda **kw:proc.returncode
        self.start()
        with patch('hdmi_display.process.subprocess.Popen',return_value=proc):
            self.rt.tick(self.ui.draw)
            self.assertEqual(self.rt.import_status['phase'],'probing')
            self.fixture.fixture.controls.events.append(ControlEvent('image',0,True))
            self.rt.tick(self.ui.draw)
        self.assertEqual(proc.returncode,-15)
        self.assertEqual(self.rt.engine.mode,'image')
        self.assertEqual(self.rt.import_status['reason'],'physical_control')
        self.assertIs(self.rt.playback.clip,self.old)

    def test_unsupported_codec_or_bit_depth_never_replaces_clip(self):
        for codec,pixels in (('hevc','yuv420p'),('h264','yuv420p10le')):
            with self.subTest(codec=codec,pixels=pixels), self.assertRaises(ValueError):
                validate_probe({'streams':[dict(codec_name=codec,pix_fmt=pixels)]})
        self.start()
        with patch('hdmi_display.media.run_cancellable_steps',side_effect=ValueError('Unsupported codec')):
            self.rt.tick(self.ui.draw)
        self.assertEqual(self.rt.import_status['state'],'failed')
        self.assertIs(self.rt.playback.clip,self.old)
        self.assertFalse((self.root/'imported').exists())

    def test_cancel_decoding_removes_partial_file_and_keeps_old_video(self):
        def probe(*a,**kw):
            if False: yield
            return SimpleNamespace(stdout=json.dumps({'streams':[dict(codec_name='h264',pix_fmt='yuv420p',
                width=64,height=64,avg_frame_rate='30/1',duration='1')]}))
        proc=SimpleNamespace(returncode=None)
        proc.poll=lambda:proc.returncode
        proc.terminate=lambda:setattr(proc,'returncode',-15)
        proc.wait=lambda **kw:proc.returncode
        def decoder(args,**kw):
            Path(args[-1]).write_bytes(b'partial RGB')
            return proc
        self.start()
        with patch('hdmi_display.media.run_cancellable_steps',probe), patch('hdmi_display.media.subprocess.Popen',side_effect=decoder):
            self.rt.tick(self.ui.draw)
            self.assertEqual(self.rt.import_status['phase'],'decoding')
            self.ui.action('cancel_import')
        self.assertEqual(proc.returncode,-15)
        self.assertFalse(list(self.root.glob('.sr_import_*')))
        self.assertIs(self.rt.playback.clip,self.old)

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'Real codecs required')
    def test_real_h264_and_mjpeg_normalize_to_dual_board_geometry(self):
        self.rt.profile=NEXYS
        for codec,extension in (('libx264','mp4'),('mjpeg','avi')):
            source=self.root/('real.'+extension)
            command=['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=80x64:rate=12',
                     '-frames:v','6','-c:v',codec]
            if codec=='libx264': command+=['-pix_fmt','yuv420p']
            subprocess.run([*command,str(source)],check=True,timeout=15)
            before=sha256_file(source)
            previous=self.rt.playback.clip
            self.rt.start_video_import(source,self.root/('clip_'+extension))
            self.drain()
            self.assertEqual(self.rt.import_status['state'],'complete',self.rt.import_status)
            self.assertIsNot(self.rt.playback.clip,previous)
            clip=self.rt.playback.clip
            self.assertEqual((clip.width,clip.height,clip.frame_count),(480,270,6))
            self.assertEqual(sha256_file(source),before)
            self.assertFalse(self.rt.playback.playing)
            self.assertEqual(clip.frame(5).shape,(270,480,3))
            self.assertFalse(list(self.root.glob('.sr_import_*')))
            self.rt.tick(self.ui.draw)

    def test_missing_media_path_is_visible_and_import_keeps_old_video(self):
        self.source.unlink()
        self.start()
        self.rt.tick(self.ui.draw)
        self.assertEqual(self.rt.import_status['state'],'failed')
        self.assertIs(self.rt.playback.clip,self.old)
        self.assertFalse(self.rt.engine.faulted)

    def fake_probe(self, *args, **kwargs):
        if False: yield
        return SimpleNamespace(stdout=json.dumps({'streams':[dict(codec_name='h264',pix_fmt='yuv420p',
            width=64,height=64,avg_frame_rate='30/1',duration='1')]}))

    def test_insufficient_space_never_starts_decoder(self):
        self.start()
        with patch('hdmi_display.media.run_cancellable_steps',self.fake_probe), \
             patch('hdmi_display.media.shutil.disk_usage',return_value=SimpleNamespace(free=0)), \
             patch('hdmi_display.media.subprocess.Popen') as process:
            self.rt.tick(self.ui.draw)
            process.assert_not_called()
        self.assertEqual(self.rt.import_status['state'],'failed')
        self.assertIn('Insufficient space',self.rt.import_status['error'])
        self.assertIs(self.rt.playback.clip,self.old)

    def test_decoder_failure_cleans_partial_output(self):
        def failed(args,**kwargs):
            Path(args[-1]).write_bytes(b'partial')
            return SimpleNamespace(returncode=1,poll=lambda:1)
        self.start()
        with patch('hdmi_display.media.run_cancellable_steps',self.fake_probe), \
             patch('hdmi_display.media.subprocess.Popen',side_effect=failed):
            self.rt.tick(self.ui.draw)
        self.assertEqual(self.rt.import_status['state'],'failed')
        self.assertIn('decoder exited',self.rt.import_status['error'])
        self.assertFalse(list(self.root.glob('.sr_import_*')))
        self.assertIs(self.rt.playback.clip,self.old)

    def test_cancel_verification_and_changed_source_are_not_published(self):
        def decoded(args,**kwargs):
            width,height=self.rt.profile.input_size
            Path(args[-1]).write_bytes(bytes(width*height*3))
            return SimpleNamespace(returncode=0,poll=lambda:0)
        with patch('hdmi_display.media.run_cancellable_steps',self.fake_probe), \
             patch('hdmi_display.media.subprocess.Popen',side_effect=decoded):
            self.start()
            self.rt.tick()
            self.assertEqual(self.rt.import_status['phase'],'verifying')
            self.rt.cancel_video_import()
            self.assertFalse(list(self.root.glob('.sr_import_*')))
            self.start()
            self.rt.tick()
            self.source.write_bytes(b'changed during verification')
            self.drain()
        self.assertEqual(self.rt.import_status['state'],'failed')
        self.assertFalse((self.root/'imported').exists())
        self.assertFalse(list(self.root.glob('.sr_import_*')))
        self.assertIs(self.rt.playback.clip,self.old)


if __name__=='__main__': unittest.main()
