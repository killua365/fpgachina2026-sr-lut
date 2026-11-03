"""Local cooperative-export integration, using a simulated Engine and real codecs."""
from fractions import Fraction
import json
from pathlib import Path
import shutil
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from hdmi_display.controls import ControlEvent
from hdmi_display.workspace import Workspace
from hdmi_display.export import export_video_steps
from hdmi_display.frames import FramePair, FrameToken
from hdmi_display.media import ImportCancelled
import test_hdmi_runtime


class InteractiveExportTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_hdmi_runtime.RuntimeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.rt = self.fixture.runtime
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.clip = SimpleNamespace(width=32, height=32, frame_count=6, fps=Fraction(30),
            manifest={'fixture':'synthetic host only'}, frame=lambda i: np.full((32,32,3), i*20, np.uint8), close=lambda:None)
        self.rt.load_clip(self.clip)
        self.rt._control_event(ControlEvent('video', 0, True))
        self.rt.tick()
        image = self.root/'image.png'
        Image.fromarray(self.rt.image).save(image)
        self.ui = Workspace(self.rt, [image], self.root, identity={'manifest_sha256':'a'*64})

    def start(self, name='video', **kwargs):
        self.rt.start_video_export(self.root/name, processing_identity={'backend':'host_test'}, **kwargs)

    def test_one_frame_per_tick_same_thread_and_cancel_keeps_display(self):
        pair = self.rt.last_pair
        before = self.rt.engine.processed_frames
        self.start()
        self.assertFalse(self.rt.playback.playing)
        self.rt.tick(self.ui.draw)  # Initial progress, no DMA call yet.
        self.assertEqual(self.rt.engine.processed_frames, before)
        seen = []
        original = self.rt.engine.process
        with patch.object(self.rt.engine, 'process', side_effect=lambda *a: (seen.append(threading.get_ident()), original(*a))[1]):
            self.rt.tick(self.ui.draw)
        self.assertEqual(seen, [threading.get_ident()])
        self.assertEqual(self.rt.engine.processed_frames, before+1)
        self.assertIs(self.rt.last_pair, pair)
        self.ui.action('cancel_export')
        self.assertFalse(self.rt.export_active)
        self.assertEqual(self.rt.export_status['state'], 'cancelled')
        self.assertFalse((self.root/'video').exists())
        self.assertFalse(list(self.root.glob('.sr_export_*')))
        self.assertFalse(self.rt.engine.display.stopped)

    def test_physical_scale_change_cancels_before_reload(self):
        self.start()
        self.rt.tick()
        self.rt.tick()
        calls = []
        original = self.rt.engine.select
        def select(*args):
            calls.append(self.rt.export_active)
            return original(*args)
        with patch.object(self.rt.engine, 'select', side_effect=select):
            self.fixture.controls.events.append(ControlEvent(None, 1, True))
            self.rt.tick()
        self.assertEqual(calls, [False])
        self.assertEqual(self.rt.engine.scale, 4)
        self.assertEqual(self.rt.export_status['reason'], 'physical_control')
        self.assertFalse(list(self.root.glob('.sr_export_*')))

    def test_playback_actions_blocked_and_button_fits_display(self):
        for width, height in ((1280,720), (1920,1080)):
            self.ui.width, self.ui.height = width, height
            buttons = self.ui.buttons()
            self.assertIn('export', [b[0] for b in buttons])
            self.assertTrue(all(0 <= r.x < r.right <= width for _,_,r in buttons))
        self.start()
        self.assertIn('cancel_export', [b[0] for b in self.ui.buttons()])
        for action in ('play', 'forward', 'speed', 'loop'):
            with self.subTest(action=action), self.assertRaises(RuntimeError):
                self.ui.action(action)
        with self.assertRaises(RuntimeError):
            self.rt.seek(1)
        self.ui.action('split')
        self.assertEqual(self.rt.composition.layout, 'split')

    def test_storage_failure_does_not_fault_hardware_or_leave_partial_output(self):
        self.start()
        with patch('hdmi_display.export.shutil.disk_usage', return_value=SimpleNamespace(free=0)):
            self.rt.tick(self.ui.draw)
        self.assertEqual(self.rt.export_status['state'], 'failed')
        self.assertIn('Insufficient space', self.rt.export_status['error'])
        self.assertFalse(self.rt.engine.faulted)
        self.assertFalse(list(self.root.glob('.sr_export_*')))

    def test_mouse_export_action_binds_overlay_and_package(self):
        with patch('hdmi_display.workspace.sha256_file', return_value='b'*64) as hashes:
            self.ui.action('export')
        self.assertEqual(hashes.call_count, 2)
        self.assertTrue(self.rt.export_active)
        self.rt.tick()  # Validates all FPGA/package identities before processing.
        self.assertEqual(self.rt.export_status['state'], 'running')
        self.ui.action('cancel_export')

    def test_write_failure_removes_partial_export_and_can_retry(self):
        original = Path.open
        class FullDisk:
            def __init__(self, stream): self.stream = stream
            def __enter__(self): return self
            def __exit__(self, *args): self.stream.close()
            def write(self, value): raise OSError('simulated disk full')
        def open_file(path, *args, **kwargs):
            stream = original(path, *args, **kwargs)
            return FullDisk(stream) if path.name == 'output.rgb' else stream
        self.start()
        self.rt.tick()
        with patch.object(Path, 'open', open_file):
            self.rt.tick()
        self.assertEqual(self.rt.export_status['state'], 'failed')
        self.assertIn('disk full', self.rt.export_status['error'])
        self.assertFalse(list(self.root.glob('.sr_export_*')))
        self.start('retry')
        self.rt.tick()
        self.assertEqual(self.rt.export_status['state'], 'running')
        self.rt.cancel_video_export()

    def test_processing_dma_fault_is_propagated_for_ordered_shutdown(self):
        self.start()
        self.rt.tick()
        def fail(*args):
            self.rt.engine.faulted = True
            raise TimeoutError('simulated DMA timeout')
        with patch.object(self.rt.engine, 'process', side_effect=fail):
            with self.assertRaises(TimeoutError):
                self.rt.tick()
        self.assertEqual(self.rt.export_status['state'], 'failed')
        self.assertFalse(list(self.root.glob('.sr_export_*')))

    def test_window_exit_closes_export_before_clip(self):
        self.start()
        self.rt.tick()
        states = []
        self.clip.close = lambda: states.append(self.rt.export_active)
        self.rt.close()
        self.assertEqual(states, [False])
        self.assertEqual(self.rt.export_status['reason'], 'runtime_close')

    def test_cancel_while_encoder_running_reaps_only_owned_process(self):
        encoder = SimpleNamespace(returncode=None)
        encoder.poll = lambda: encoder.returncode
        def terminate():
            encoder.returncode = -15
        encoder.terminate = terminate
        encoder.wait = lambda **kwargs: encoder.returncode
        encoder.kill = lambda: self.fail('Unneeded kill')
        with patch('hdmi_display.export.subprocess.Popen', return_value=encoder):
            self.start()
            for _ in range(self.clip.frame_count+2):
                self.rt.tick()
            self.assertEqual(self.rt.export_status['phase'], 'encoding')
            self.rt.cancel_video_export()
        self.assertEqual(encoder.returncode, -15)
        self.assertFalse(list(self.root.glob('.sr_export_*')))

    def test_cancel_while_probe_running_remains_responsive(self):
        done = SimpleNamespace(returncode=0, poll=lambda:0)
        probe = SimpleNamespace(returncode=None)
        probe.poll = lambda: probe.returncode
        probe.terminate = lambda: setattr(probe, 'returncode', -15)
        probe.wait = lambda **kwargs: probe.returncode
        with patch('hdmi_display.export.subprocess.Popen', side_effect=[done, probe]):
            self.start()
            for _ in range(self.clip.frame_count+2):
                self.rt.tick()
            self.assertEqual(self.rt.export_status['phase'], 'verifying')
            self.ui.action('cancel_export')
        self.assertEqual(probe.returncode, -15)
        self.assertFalse(list(self.root.glob('.sr_export_*')))

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'Real FFmpeg tools required')
    def test_real_encode_both_scales_includes_every_frame_and_keeps_playback_paused(self):
        for scale in (2,4):
            self.rt._control_event(ControlEvent('video', int(scale==4), True))
            self.rt.tick()
            live_count = self.rt.submitted_new_frames
            self.start('video'+str(scale))
            deadline = time.monotonic()+30
            while self.rt.export_active and time.monotonic() < deadline:
                self.rt.tick(self.ui.draw)
                time.sleep(.005)
            self.assertFalse(self.rt.export_active)
            self.assertEqual(self.rt.export_status['state'], 'complete', self.rt.export_status)
            report = Path(self.rt.export_status['report'])
            metadata = json.loads(report.read_text())
            frames = [json.loads(s) for s in (report.parent/'frames.jsonl').read_text().splitlines()]
            self.assertEqual([f['index'] for f in frames], list(range(6)))
            self.assertEqual(metadata['encoded_stream']['width'], 32*scale)
            self.assertEqual(int(metadata['encoded_stream']['nb_read_frames']), 6)
            self.assertEqual(self.rt.submitted_new_frames, live_count)
            self.assertFalse(self.rt.playback.playing)
            self.assertFalse(metadata['golden_reference_verified'])


if __name__ == '__main__':
    unittest.main()
