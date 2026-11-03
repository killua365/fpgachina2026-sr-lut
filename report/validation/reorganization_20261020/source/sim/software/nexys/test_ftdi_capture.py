"""Capture lifecycle tests with fragmented binary data and injected driver errors."""
import ctypes
import csv
import json
from pathlib import Path
import tempfile
import unittest
from capture_ftdi_uart import capture


class Device:
    def __init__(self, destination, rows, close_error=False):
        self.destination, self.rows = destination, iter(rows)
        self.closed = False
        self.close_error = close_error

    def open(self):
        return dict(backend='test fixture')

    def read(self, buffer):
        try:
            payload, error = next(self.rows)
        except StopIteration:
            (self.destination/'stop.request').write_text('stop')
            return 0, 0, 0
        ctypes.memmove(buffer, payload, len(payload))
        return len(payload), error, len(payload)

    def close(self):
        self.closed = True
        if self.close_error:
            raise OSError('injected restore failure')


class CaptureTests(unittest.TestCase):
    def test_fragmented_binary_and_chunk_boundaries_are_exact(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)/'capture'
            payload = bytes(range(256))*9
            fragments = [payload[:1], payload[1:62], payload[62:381], payload[381:]]
            device = Device(out, [(b, 0) for b in fragments])
            stamps = iter(range(1000, 10000))
            result = capture(out, {}, device, lambda: next(stamps), 1000, sleep=lambda _: None)
            self.assertTrue(device.closed and result['success'])
            self.assertEqual((out/'capture.bin').read_bytes(), payload)
            with (out/'chunks.csv').open() as stream:
                chunks = list(csv.DictReader(stream))
            self.assertEqual([int(c['count']) for c in chunks], list(map(len, fragments)))
            self.assertEqual(int(chunks[-1]['total_bytes']), len(payload))
            self.assertEqual([int(c['qpc_ticks']) for c in chunks], [1000, 1001, 1002, 1003])
            self.assertFalse(json.loads((out/'identity.json').read_text())['writes_performed'])

    def test_line_error_and_clear_times_preserved_with_all_received_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)/'capture'
            device = Device(out, [(b'\x00\xff\x7e', 2), (b'tail', 0)])
            ticks = iter((99, 100, 101))
            capture(out, {}, device, lambda: next(ticks), 1000, sleep=lambda _: None)
            result = json.loads((out/'exit.json').read_text())
            self.assertTrue(device.closed)
            self.assertTrue(result['success'])
            self.assertEqual(result['line_status_changes'], [dict(qpc_ticks=99, flags=2), dict(qpc_ticks=100, flags=0)])
            self.assertEqual(result['last_status_qpc_ticks'], 101)
            self.assertEqual((out/'capture.bin').read_bytes(), b'\x00\xff\x7etail')

    def test_restoration_failure_and_independent_deadline_fail(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)/'restore'
            device = Device(out, [], close_error=True)
            with self.assertRaisesRegex(OSError, 'restore failure'):
                capture(out, {}, device, lambda: 1, 1000, sleep=lambda _: None)
            self.assertFalse(json.loads((out/'exit.json').read_text())['success'])
            out = Path(folder)/'deadline'
            device = Device(out, [])
            ticks = iter((0, 2))
            with self.assertRaises(TimeoutError):
                capture(out, {}, device, lambda: 1, 1000, seconds=1, monotonic=lambda: next(ticks))
            self.assertTrue(device.closed)
            self.assertFalse(json.loads((out/'exit.json').read_text())['success'])


if __name__ == '__main__':
    unittest.main()
