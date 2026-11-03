"""Fault-inject the real temporary programming Tcl without accessing hardware."""
from pathlib import Path
import tempfile
import tkinter
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / 'board/host/run_sram_pattern.tcl'
MOCK = r'''
set events {}
set state factory
proc version {args} {return 2026.1}
rename exec original_exec
proc exec {args} {
    if {$::scenario eq "invalid_package"} {error "injected hash mismatch"}
    return passed
}
proc open_hw_manager {} {lappend ::events open}
proc connect_hw_server {args} {}
proc get_hw_targets {args} {
    if {$::scenario eq "wrong_board"} {return {}}
    return serial210276B3E2ADB
}
proc current_hw_target {args} {}
proc set_property {args} {}
proc open_hw_target {args} {}
proc get_hw_devices {args} {return xc7a200t_0}
proc current_hw_device {args} {}
proc refresh_hw_device {args} {}
proc report_property {args} {}
proc get_property {key object} {
    if {$key eq "IDCODE_HEX"} {return 13636093}
    if {$key eq "REGISTER.USR_ACCESS"} {
        if {$::state eq "loaded"} {
            if {$::scenario eq "wrong_candidate_id"} {return 11223344}
            return 26092701
        }
        if {$::scenario eq "wrong_factory_id"} {return 87654321}
        return 00000000
    }
    if {[string match *BIT08_MODE* $key]} {
        if {$::scenario eq "wrong_mode"} {return 0}
        return 1
    }
    if {[string match *BIT12_INIT* $key] || [string match *BIT14_DONE* $key]} {return 1}
    if {$::state eq "loaded" && $::scenario eq "crc_error" && [string match *BIT00_CRC* $key]} {return 1}
    return 0
}
proc program_hw_devices {args} {
    lappend ::events program_sram
    if {$::scenario eq "load_error"} {error "injected programming failure"}
    set ::state loaded
}
proc create_hw_cfgmem {args} {error "FORBIDDEN cfgmem access"}
proc program_hw_cfgmem {args} {error "FORBIDDEN Flash programming"}
proc boot_hw_device {args} {
    lappend ::events boot_factory
    if {$::scenario eq "boot_error"} {error "injected recovery failure"}
    set ::state factory
}
rename after original_after
proc after {args} {}
proc close_hw_target {args} {}
proc disconnect_hw_server {} {}
proc close_hw_manager {} {lappend ::events close}
'''


class SramTests(unittest.TestCase):
    def run_case(self, scenario):
        with tempfile.TemporaryDirectory(prefix='nexys_sram_mock_') as tmp:
            root = Path(tmp)
            candidate = root / 'candidate'
            candidate.mkdir()
            (candidate / 'candidate.json').write_text('{}')
            output = root / 'results'
            if scenario == 'existing_output':
                output.mkdir()
            tcl = tkinter.Tcl()
            tcl.eval(MOCK)
            tcl.setvar('scenario', scenario)
            tcl.setvar('argc', 4)
            tcl.setvar('argv', tcl.call('list', 'python', candidate.as_posix(), output.as_posix(), '10'))
            error = None
            try:
                tcl.call('source', SCRIPT.as_posix())
            except tkinter.TclError as exc:
                error = str(exc)
            record = output / 'operation.txt'
            return error, tuple(tcl.splitlist(tcl.getvar('events'))), record.read_text() if record.exists() else ''

    def test_success_restores_factory_and_keeps_visual_unknown(self):
        error, events, record = self.run_case('success')
        self.assertIsNone(error)
        self.assertEqual(events, ('open', 'program_sram', 'boot_factory', 'close'))
        self.assertIn('physical_display_verified=false', record)
        self.assertIn('visual_recovery_confirmed=false', record)
        self.assertIn('recovery_exit=0', record)

    def test_guards_prevent_programming(self):
        for case in ('invalid_package', 'wrong_board', 'wrong_mode', 'wrong_factory_id', 'existing_output'):
            with self.subTest(case=case):
                error, events, _ = self.run_case(case)
                self.assertIsNotNone(error)
                self.assertNotIn('program_sram', events)

    def test_failures_still_restore_factory(self):
        for case in ('load_error', 'wrong_candidate_id', 'crc_error'):
            with self.subTest(case=case):
                error, events, record = self.run_case(case)
                self.assertIsNotNone(error)
                self.assertEqual(events.count('boot_factory'), 1)
                self.assertIn('test_exit=1', record)
                self.assertIn('recovery_exit=0', record)

    def test_failed_recovery_is_not_reported_as_success(self):
        error, events, record = self.run_case('boot_error')
        self.assertIsNotNone(error)
        self.assertIn('recovery_exit=1', record)
        self.assertIn('close', events)


if __name__ == '__main__':
    unittest.main(verbosity=2)
