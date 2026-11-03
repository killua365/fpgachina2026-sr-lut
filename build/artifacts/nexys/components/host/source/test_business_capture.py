import unittest
from analyze_business_capture import window
from test_business_diagnostic import sample, wire


class BusinessCaptureTests(unittest.TestCase):
    def setUp(self):
        self.raw = b'original-program' + b''.join(wire(row) for row in sample()['frames'])
        self.chunks = [dict(count=16, total_bytes=16, qpc_ticks=0)]
        for n in range(4):
            self.chunks.append(dict(count=381, total_bytes=16+381*(n+1), qpc_ticks=100_000_000*(n+1)))
        self.frames = [dict(generation=7, sequence=n) for n in range(1, 8)]

    def run_window(self, **extra):
        return window(self.raw, self.chunks, 1_000_000_000, 100_000_000, 400_000_000,
                      mode=2, scale=4, generation=7, minimum_seconds=.29, **extra)['summary']

    def test_complete_window_and_source_join(self):
        r = self.run_window(source_frames=self.frames)
        self.assertTrue(r['telemetry_passed'], r['reasons'])
        self.assertTrue(r['source_identities_bound'])
        self.assertEqual(r['host_new_display_fps'], 20)
        self.assertEqual(r['preserved_prefix_bytes'], 16)
        self.assertFalse(r['n6_passed'])

    def test_unknown_source_identity_fails(self):
        r = self.run_window(source_frames=self.frames[:-1])
        self.assertIn('displayed_identity_absent_from_source_trace', r['reasons'])
        self.assertFalse(r['source_identities_bound'])

    def test_duplicate_trace_and_missing_startup_fail(self):
        for duration in (float('nan'), float('inf'), -1):
            with self.assertRaises(ValueError):
                window(self.raw, self.chunks, 1_000_000_000, 100_000_000, 400_000_000,
                       mode=2, scale=4, generation=7, minimum_seconds=duration)
        with self.assertRaises(ValueError):
            self.run_window(source_frames=self.frames*2)
        self.raw = self.raw[:16] + self.raw[16+381:]
        self.chunks.pop()
        with self.assertRaises(ValueError):
            self.run_window()

    def test_chunk_corruption_and_host_clock_disagreement_fail(self):
        self.chunks[-1]['total_bytes'] -= 1
        with self.assertRaises(ValueError):
            self.run_window()
        self.chunks[-1]['total_bytes'] += 1
        self.chunks[-1]['qpc_ticks'] -= 70_000_000
        r = self.run_window()
        self.assertIn('host_hardware_timebase_disagreement', r['reasons'])


if __name__ == '__main__':
    unittest.main()
