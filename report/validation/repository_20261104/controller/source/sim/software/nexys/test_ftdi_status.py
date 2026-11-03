import copy
import unittest
from ftdi_status import check_status


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.identity = dict(backend='FTDI D2XX', qpc_frequency=1000)
        self.closed = dict(device_closed=True, latency_restored=True,
            line_status_changes=[dict(qpc_ticks=1000, flags=2), dict(qpc_ticks=8000, flags=0)],
            last_status_qpc_ticks=25000)

    def test_preexisting_error_must_clear_before_candidate(self):
        check_status(self.identity, self.closed, 10_000_000_000, 20_000_000_000)
        for clearing in (10001, 15000, 21000):
            value = copy.deepcopy(self.closed)
            value['line_status_changes'][1]['qpc_ticks'] = clearing
            with self.assertRaisesRegex(ValueError, 'line error'):
                check_status(self.identity, value, 10_000_000_000, 20_000_000_000)

    def test_new_errors_missing_tail_and_regression_are_rejected(self):
        for changes in ([dict(qpc_ticks=12000, flags=8), dict(qpc_ticks=12001, flags=0)],
                        [dict(qpc_ticks=12000, flags=2)], [dict(qpc_ticks=7999, flags=0)]):
            value = copy.deepcopy(self.closed)
            value['line_status_changes'] += changes
            with self.assertRaises(ValueError):
                check_status(self.identity, value, 10_000_000_000, 20_000_000_000)
        for field, replacement in [('last_status_qpc_ticks', 19000), ('latency_restored', False)]:
            value = dict(self.closed, **{field: replacement})
            with self.assertRaises(ValueError):
                check_status(self.identity, value, 10_000_000_000, 20_000_000_000)
