from copy import deepcopy
import unittest

from hdmi_display.profiling import evaluate


class ProfilingTests(unittest.TestCase):
    def samples(self):
        start = {'monotonic': 100, 'runtime': {'mode': 'video', 'scale': 4,
                  'layout': 'split', 'generation': 8, 'last_error': None},
                 'display': {'acknowledged_source_pairs': 7, 'scan_frames': 1000,
                    'running': True, 'locked': True, 'counters_saturated': False,
                    'dma_status': 0x11000, 'underflow_events': 1, 'overflow_events': 0}}
        end = deepcopy(start)
        end['monotonic'] = 115
        end['display'].update(acknowledged_source_pairs=382, scan_frames=1900)
        return [start, end]

    def test_counts_only_acknowledged_pairs_and_retains_startup_counters(self):
        result = evaluate(self.samples(), 15)
        self.assertTrue(result['passed'])
        self.assertEqual(result['new_frame_fps'], 25)
        self.assertEqual(result['refresh_hz'], 60)
        self.assertFalse(result['formal_release'])

    def test_counter_reset_underflow_and_source_change_fail(self):
        for area, key, value in (('display', 'acknowledged_source_pairs', 0),
                                  ('display', 'underflow_events', 2),
                                  ('display', 'locked', False),
                                  ('runtime', 'generation', 9)):
            samples = self.samples()
            samples[-1][area][key] = value
            self.assertFalse(evaluate(samples, 15)['passed'])

    def test_repeated_scans_and_ui_repaints_cannot_pass_fps(self):
        samples = self.samples()
        samples[-1]['display']['acknowledged_source_pairs'] = 8
        samples[-1]['display']['dma_frame_switches'] = 900
        result = evaluate(samples, 15)
        self.assertFalse(result['passed'])
        self.assertIn('below_20_fps', result['failure_reasons'])


if __name__ == '__main__':
    unittest.main()
