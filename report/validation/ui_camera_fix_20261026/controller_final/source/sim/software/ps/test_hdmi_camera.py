import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import numpy as np

from hdmi_display.camera import CameraSource, prepare_camera_frame


class SyntheticCapture:
    def __init__(self, stall=False):
        self.stall = stall
        self.sequence = 0

    def read(self):
        if self.stall:
            time.sleep(60)
        time.sleep(0.01)
        self.sequence += 1
        return True, np.full((48, 64, 3), (0, 0, self.sequence % 256), np.uint8)

    def release(self):
        pass


def synthetic_factory(device, width, height, fps):
    if device == "stall":
        return SyntheticCapture(stall=True)
    if device != "normal":
        marker = Path(device)
        if not marker.exists():
            marker.touch()
            os._exit(7)
    return SyntheticCapture()


class CameraTests(unittest.TestCase):
    def test_native_capture_checks_geometry_and_converts_before_area_filter(self):
        import cv2
        from hdmi_display.camera import _RawYuyvCapture
        raw=np.random.default_rng(1801).integers(0,256,(48,64,2),np.uint8)
        capture=Mock()
        capture.read.return_value=(True,raw)
        adapter=_RawYuyvCapture(capture,64,48)
        ok,bgr=adapter.read()
        self.assertTrue(ok)
        np.testing.assert_array_equal(bgr,cv2.cvtColor(raw,cv2.COLOR_YUV2BGR_YUY2))
        capture.read.return_value=(True,raw[:1])
        with self.assertRaises(RuntimeError):adapter.read()
        adapter.release()
        capture.release.assert_called_once()

    def test_native_capture_negotiation_retains_resolution_and_falls_back_if_rejected(self):
        import cv2
        from hdmi_display.camera import _open_camera_native as _open_camera, _RawYuyvCapture
        def new_capture():
            capture=Mock()
            values={}
            capture.isOpened.return_value=True
            capture.set.side_effect=lambda k,v:values.setdefault(k,v)==v
            capture.get.side_effect=lambda k:values[k]
            return capture
        good=new_capture()
        with patch('hdmi_display.pixels._native',Mock()),patch('hdmi_display.camera.cv2.VideoCapture',return_value=good):
            result=_open_camera('/dev/video0',640,480,30)
        self.assertIsInstance(result,_RawYuyvCapture)
        self.assertEqual(result.bgr.shape,(480,640,3))
        rejected,fallback=new_capture(),new_capture()
        rejected.set.return_value=False
        rejected.set.side_effect=None
        with patch('hdmi_display.pixels._native',Mock()),patch('hdmi_display.camera.cv2.VideoCapture',side_effect=[rejected,fallback]):
            self.assertIs(_open_camera('/dev/video0',640,480,30),fallback)
        rejected.release.assert_called_once()
        fallback.set.assert_any_call(cv2.CAP_PROP_FOURCC,cv2.VideoWriter_fourcc(*'MJPG'))

    def test_default_format_preserves_negotiated_full_resolution_and_rate(self):
        import cv2
        from hdmi_display.camera import _open_camera
        capture=Mock()
        capture.isOpened.return_value=True
        values={}
        capture.set.side_effect=lambda k,v: values.setdefault(k,v)==v
        capture.get.side_effect=lambda k:values[k]
        with patch('hdmi_display.camera.cv2.VideoCapture',return_value=capture):
            self.assertIs(_open_camera('/dev/video0',640,480,30),capture)
        self.assertEqual(values[cv2.CAP_PROP_FOURCC],cv2.VideoWriter_fourcc(*'MJPG'))
        capture.get.side_effect=lambda k:320 if k==cv2.CAP_PROP_FRAME_WIDTH else values[k]
        with patch('hdmi_display.camera.cv2.VideoCapture',return_value=capture):
            with self.assertRaises(RuntimeError):_open_camera('/dev/video0',640,480,30)
        capture.release.assert_called_once()

    def test_cold_spawn_uses_open_timeout_then_stream_uses_read_timeout(self):
        source = CameraSource(clock=lambda: 4.0, open_timeout=20, read_timeout=3)
        source.enabled, source.state = True, 'opening'
        source.process = Mock()
        source.process.is_alive.return_value = True
        source.lock = Mock()
        source.lock.acquire.return_value = False
        self.assertIsNone(source.poll())
        self.assertEqual(source.timeouts, 0)
        source.state = 'streaming'
        source.poll()
        self.assertEqual(source.timeouts, 1)
        source.process.terminate.assert_called_once()

    def wait_frame(self, source, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            frame = source.poll()
            if frame is not None:
                return frame
            time.sleep(0.01)
        self.fail(f"No synthetic camera frame: {source.status()}")

    def test_letterbox_preserves_four_by_three_geometry_and_rgb_order(self):
        source = np.full((480, 640, 3), (3, 5, 17), np.uint8)
        prepared, active = prepare_camera_frame(source)
        self.assertEqual((active.x, active.y, active.width, active.height), (40, 0, 240, 180))
        self.assertEqual(prepared.shape, (180, 320, 3))
        np.testing.assert_array_equal(prepared[:, :40], 0)
        np.testing.assert_array_equal(prepared[0, 40], [17, 5, 3])

    def test_mailbox_is_bounded_and_consumer_owns_copied_frame(self):
        source = CameraSource("normal", capture_factory=synthetic_factory, read_timeout=5)
        source.start()
        try:
            first = self.wait_frame(source)
            saved = first.rgb.copy()
            time.sleep(0.15)
            second = self.wait_frame(source)
            self.assertGreater(second.sequence, first.sequence)
            self.assertGreater(source.frames_overwritten, 0)
            self.assertEqual(source.status()["mailbox_bytes"], 172800)
            np.testing.assert_array_equal(first.rgb, saved)
            self.assertFalse(np.shares_memory(first.rgb, second.rgb))
            self.assertEqual((first.source_width, first.source_height), (64, 48))
        finally:
            source.stop()
        self.assertIsNone(source.process)
        self.assertIsNone(source.poll())

    def test_failed_open_retries_automatically_without_a_button(self):
        with tempfile.TemporaryDirectory() as temp:
            source = CameraSource(str(Path(temp) / "first_attempt"),
                capture_factory=synthetic_factory, read_timeout=5, retry_seconds=0.05)
            source.start()
            try:
                frame = self.wait_frame(source)
                self.assertEqual(source.attempts, 2)
                self.assertEqual(frame.generation, 2)
                self.assertEqual(source.state, "streaming")
            finally:
                source.stop()

    def test_streaming_worker_failure_recovers_with_a_new_generation(self):
        source = CameraSource("normal", capture_factory=synthetic_factory,
                              read_timeout=5, retry_seconds=0.05)
        source.start()
        try:
            first = self.wait_frame(source)
            previous_pid = source.process.pid
            source.process.terminate()
            source.process.join(timeout=1)
            recovered = self.wait_frame(source)
            self.assertGreater(recovered.generation, first.generation)
            self.assertGreater(recovered.sequence, first.sequence)
            self.assertNotEqual(source.process.pid, previous_pid)
            self.assertEqual(source.recoveries, 1)
            self.assertEqual(source.state, "streaming")
        finally:
            source.stop()

    def test_stuck_read_is_retired_and_switching_mode_cancels_retry(self):
        source = CameraSource("stall", capture_factory=synthetic_factory,
                              read_timeout=1.5, open_timeout=1.5, retry_seconds=0.1)
        source.start()
        deadline = time.monotonic() + 6
        try:
            while source.timeouts == 0 and time.monotonic() < deadline:
                source.poll()
                time.sleep(0.01)
            self.assertEqual(source.timeouts, 1)
            started = time.monotonic()
            source.stop()
            self.assertLess(time.monotonic() - started, 1.5)
            attempts = source.attempts
            time.sleep(0.15)
            self.assertIsNone(source.poll())
            self.assertEqual(source.attempts, attempts)
            self.assertEqual(source.state, "stopped")
        finally:
            source.stop()

    def test_camera_geometry_must_respect_the_sr_contract(self):
        for kwargs in ({"input_size": (481, 180)}, {"fps": 0}, {"read_timeout": float("inf")},
                       {"open_timeout": float('nan')}):
            with self.assertRaises(ValueError):
                CameraSource(**kwargs)


if __name__ == "__main__":
    unittest.main()
