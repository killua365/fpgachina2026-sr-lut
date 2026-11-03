import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import numpy as np

from hdmi_display.frame_trace import FrameTrace
from hdmi_display.frames import FrameToken
from test_hdmi_engine import EngineTests

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'board/pynq'))
import hdmi_business_app as app


class TraceTests(unittest.TestCase):
    def test_repaint_mode_generation_and_regression(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'build') as name:
            path = Path(name)/'frames.jsonl'
            trace = FrameTrace(path)
            try:
                trace(FrameToken(2, 0), 'image', 2)
                trace(FrameToken(2, 0), 'image', 2)
                trace(FrameToken(2, 1), 'image', 2)
                trace(FrameToken(3, 0), 'camera', 4)
                with self.assertRaises(ValueError):
                    trace(FrameToken(2, 2), 'video', 4)
                with self.assertRaises(ValueError):
                    trace(FrameToken(4, 0), 'idle', 0)
            finally:
                trace.close()
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual([(r['generation'], r['sequence']) for r in rows], [(2, 0), (2, 1), (3, 0)])
            self.assertEqual(trace.count, 3)
            self.assertEqual(rows[-1]['mode'], 3)
            with self.assertRaises(FileExistsError):
                FrameTrace(path)

    def test_launch_rejects_foreign_path_and_unbounded_profiles_before_hardware(self):
        good = ['app', 'run', '--root', str(ROOT), '--seconds', '180', '--run-id', 'a'*32,
                '--destination', '/home/xilinx/n5_business_results_'+'a'*32]
        variants = [good+['--profile-seconds', '10'], good+['--seconds', '301'],
                    good+['--destination', '/home/xilinx/other'], good+['--run-id', 'not-unique'],
                    good+['--camera-device', '/dev/video1']]
        with patch.object(app, 'supervise') as supervise, patch.object(app.faulthandler, 'enable'), contextlib.redirect_stderr(io.StringIO()):
            for args in variants:
                with self.subTest(args=args), patch.object(sys, 'argv', args), self.assertRaises(SystemExit):
                    app.main()
            supervise.assert_not_called()
            with patch.object(sys, 'argv', good):
                app.main()
            self.assertEqual(supervise.call_args.args[-1], 'a'*32)


class ObserverTests(unittest.TestCase):
    def setUp(self):
        EngineTests.setUp(self)

    def test_observer_follows_submission_and_rejects_stale_pair(self):
        seen = []
        self.engine.frame_observer = lambda token, mode, scale: seen.append((token, mode, scale))
        self.engine.select('image', 0)
        pair = self.engine.process(np.zeros((32, 32, 3), np.uint8))
        self.engine.present(pair)
        self.assertEqual(seen, [(pair.token, 'image', 2)])
        self.engine.select('video', 0)
        self.assertFalse(self.engine.present(pair))
        self.assertEqual(len(seen), 1)

    def test_failed_submit_and_failed_trace_cannot_fake_success(self):
        seen = []
        self.engine.frame_observer = lambda *args: seen.append(args)
        self.engine.select('image', 0)
        pair = self.engine.process(np.zeros((32, 32, 3), np.uint8))
        with patch.object(self.engine.display, 'submit', side_effect=RuntimeError('DMA')):
            with self.assertRaises(RuntimeError):
                self.engine.present(pair)
        self.assertFalse(seen)
        self.engine.select('image', 0)
        pair = self.engine.process(np.zeros((32, 32, 3), np.uint8))
        with patch.object(self.engine, 'frame_observer', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.engine.present(pair)


if __name__ == '__main__':
    unittest.main()
