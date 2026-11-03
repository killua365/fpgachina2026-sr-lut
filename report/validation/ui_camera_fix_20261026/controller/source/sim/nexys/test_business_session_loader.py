"""Exercise the real new loader through mocked Vivado hardware primitives."""
from pathlib import Path
import tempfile
import tkinter
import unittest
from test_sram_pattern import MOCK
from test_bridge_ordered_loader import AFTER

ROOT = Path(__file__).resolve().parents[2]


class LoaderTests(unittest.TestCase):
    def run_case(self, scenario, end_mode='timed'):
        with tempfile.TemporaryDirectory(dir=ROOT/'build') as name:
            root = Path(name)
            candidate = root/'candidate'
            candidate.mkdir()
            (candidate/'result.json').write_text('{}')
            control = root/'control'
            control.mkdir()
            (control/'lease.txt').write_text('0')
            t = tkinter.Tcl()
            t.eval(MOCK.replace('26092701', '26101201').replace('return passed', 'return 1000000000'))
            t.setvar('scenario', scenario)
            t.setvar('control', control.as_posix())
            t.setvar('run_id', 'a'*32)
            t.eval(AFTER.replace('4078', '11758' if end_mode == 'operator' else '5038'))
            if scenario == 'cancel_before_load':
                t.call('command', 'close')
            out = root/'out'
            t.setvar('argc', 7)
            t.setvar('argv', t.call('list', 'python', candidate.as_posix(), out.as_posix(), control.as_posix(), 'package', 'a'*32, end_mode))
            error = None
            try:
                t.call('source', (ROOT/'board/host/run_business_session.tcl').as_posix())
            except tkinter.TclError as exc:
                error = str(exc)
            return error, tuple(map(str, t.splitlist(t.getvar('events')))), (out/'operation.txt').read_text() if (out/'operation.txt').exists() else ''

    def test_close_keeps_candidate_until_ordered_restore(self):
        error, events, record = self.run_case('success')
        self.assertIsNone(error)
        self.assertEqual(events, ('open', 'program_sram', 'closed_still_loaded', 'boot_factory', 'close'))
        self.assertIn('sample_closed=1', record)
        self.assertIn('flash_write_called=false', record)

    def test_preload_failures_never_program(self):
        for case in ('invalid_package', 'wrong_board', 'wrong_factory_id', 'wrong_mode', 'cancel_before_load'):
            with self.subTest(case=case):
                error, events, _ = self.run_case(case)
                self.assertIsNotNone(error)
                self.assertNotIn('program_sram', events)

    def test_watchdogs_and_bad_commands_restore_but_fail(self):
        for case in ('load_error', 'wrong_candidate_id', 'crc_error', 'lease_timeout', 'loop_timeout', 'premature_restore', 'foreign_close'):
            with self.subTest(case=case):
                error, events, record = self.run_case(case)
                self.assertIsNotNone(error)
                self.assertIn('boot_factory', events)
                self.assertIn('recovery_exit=0', record)

    def test_failed_recovery_stays_unknown(self):
        error, _, record = self.run_case('boot_error')
        self.assertIsNotNone(error)
        self.assertIn('recovery_exit=1', record)

    def test_operator_loader_keeps_ordering_lease_and_independent_cap(self):
        error, events, _ = self.run_case('success', 'operator')
        self.assertIsNone(error)
        self.assertIn('closed_still_loaded', events)
        for case in ('loop_timeout', 'lease_timeout', 'premature_restore'):
            with self.subTest(case=case):
                error, events, record = self.run_case(case, 'operator')
                self.assertIsNotNone(error)
                self.assertIn('boot_factory', events)
                self.assertIn('recovery_exit=0', record)
                if case == 'loop_timeout':
                    self.assertIn('2940-second hold limit', error)
