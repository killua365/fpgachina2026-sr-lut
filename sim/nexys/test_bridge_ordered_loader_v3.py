import sys,tempfile,tkinter,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
from test_sram_pattern import MOCK

AFTER=r'''
set ticks 0
proc command {name} {
    set f [open [file join $::control $name.request] w]
    puts $f "ordered_exit_v1 $::run_id $name"; close $f
}
proc after {ms args} {
    if {$ms!=250} {return}
    incr ::ticks
    if {$::scenario eq "loop_timeout" && $::ticks==1} {set ::tick 4078}
    if {$::scenario ne "lease_timeout"} {
        set f [open [file join $::control lease.txt] w]; puts $f $::ticks; close $f
    }
    if {$::ticks==1 && $::scenario ni {loop_timeout lease_timeout}} {command start}
    if {$::ticks==3 && $::scenario ni {loop_timeout lease_timeout}} {
        if {$::scenario eq "premature_restore"} {command restore} else {command close}
        if {$::scenario eq "foreign_close"} {
            set f [open [file join $::control close.request] w]; puts $f foreign; close $f
        }
    }
    if {$::ticks==4 && $::scenario eq "success"} {
        if {![file exists [file join $::out close.ack]] || $::state ne "loaded"} {error "Close unexpectedly restored hardware"}
        lappend ::events closed_still_loaded
    }
    if {$::ticks==6 && $::scenario ni {loop_timeout lease_timeout}} {command restore}
}
'''


class LoaderTests(unittest.TestCase):
    def run_case(self,scenario):
        with tempfile.TemporaryDirectory() as tmp:
            f=Path(tmp); candidate=f/'candidate'; candidate.mkdir(); (candidate/'candidate.json').write_text('{}')
            control=f/'control'; control.mkdir(); (control/'lease.txt').write_text('0')
            t=tkinter.Tcl(); t.eval(MOCK.replace('26092701','26100801').replace('return passed','return 1000000000'))
            t.setvar('scenario',scenario); t.setvar('control',control.as_posix()); t.setvar('run_id','a'*32); t.eval(AFTER)
            if scenario=='cancel_before_load': t.call('command','close')
            out=f/'output'; t.setvar('argc',6)
            t.setvar('argv',t.call('list','python',candidate.as_posix(),out.as_posix(),control.as_posix(),'package','a'*32))
            error=None
            try:t.call('source',(ROOT/'board/host/run_bridge_ordered_v3.tcl').as_posix())
            except tkinter.TclError as exc:error=str(exc)
            return error,tuple(map(str,t.splitlist(t.getvar('events')))),(out/'operation.txt').read_text() if (out/'operation.txt').exists() else ''

    def test_close_keeps_candidate_loaded_until_restore(self):
        err,events,record=self.run_case('success'); self.assertIsNone(err)
        self.assertEqual(events,('open','program_sram','closed_still_loaded','boot_factory','close'))
        self.assertIn('sample_closed=1',record); self.assertIn('restore_requested=1',record)

    def test_before_load_guards(self):
        for name in ('invalid_package','wrong_board','wrong_factory_id','wrong_mode','cancel_before_load'):
            with self.subTest(name=name):
                err,events,_=self.run_case(name); self.assertIsNotNone(err); self.assertNotIn('program_sram',events)

    def test_watchdogs_errors_and_out_of_order_restore(self):
        for name in ('load_error','wrong_candidate_id','crc_error','lease_timeout','loop_timeout','premature_restore','foreign_close'):
            with self.subTest(name=name):
                err,events,record=self.run_case(name); self.assertIsNotNone(err)
                self.assertIn('boot_factory',events); self.assertIn('recovery_exit=0',record)

    def test_recovery_failure_unknown(self):
        err,events,record=self.run_case('boot_error'); self.assertIsNotNone(err); self.assertIn('recovery_exit=1',record)


if __name__=='__main__': unittest.main()
