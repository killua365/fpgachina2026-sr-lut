# Independent VGA candidate: same SRAM lease and restore protocol.
# N5 business session. Independent lease/hold enforcement; never writes Flash.
if {$argc ni {6 7}} {error "Usage: PYTHON CANDIDATE NEW_OUT CONTROL PACKAGE RUN_ID ?END_MODE?"}
if {![string match "2026.1*" [version -short]]} {error "Requires Vivado 2026.1"}
set scripts [file normalize [file join [file dirname [info script]] ../../../board/host]]
lassign $argv python candidate out control package run_id end_mode
if {$argc==6} {set end_mode timed}
if {$end_mode ni {timed operator demo}} {error "Unknown session end mode"}
set hold_seconds [expr {$end_mode eq "demo" ? -1 : ($end_mode eq "operator" ? 2940 : 1260)}]
if {![regexp {^[0-9a-f]{32}$} $run_id]} {error "Bad run ID"}
if {[file exists $out]} {error "Refuse existing output"}
puts [exec $python -X utf8 -E -B [file join $candidate qualify_candidate.py] check]
source [file join $scripts longrun_hw.tcl]
proc request {name} {
    set path [file join $::control $name.request]
    if {![file exists $path]} {return 0}
    set f [open $path r]; try {set content [read $f]} finally {close $f}
    if {$content ne "ordered_exit_v1 $::run_id $name\n"} {error "Invalid $name request"}
    return 1
}
proc ack {name} {long_write [file join $::out $name.ack] "ordered_exit_v1 $::run_id $name"}
file mkdir $out
set attempted 0; set loaded 0; set sample_started 0; set sample_closed 0
set restore_requested 0; set recovery_code -1; set recovery_message "load not attempted"
set test_code [catch {
    lassign [long_connect] target device
    report_property -all $device -file [file join $out device_before.rpt]
    long_assert $device 00000000
    if {[request close] || [request restore]} {error "Cancelled before load"}
    if {![file exists [file join $control lease.txt]]} {error "No lease"}
    file copy [file join $candidate result.json] [file join $out candidate.json]
    set_property PROGRAM.FILE [file join $candidate artifacts pattern.bit] $device
    long_write [file join $out programming_attempted.txt] "utc=[clock seconds]"
    set attempted 1
    program_hw_devices $device
    refresh_hw_device -update_hw_probes false $device
    report_property -all $device -file [file join $out device_loaded.rpt]
    long_assert $device 26102101
    set loaded 1
    long_write [file join $out active.txt] "candidate=26102101\nrun_id=$run_id"
    set previous_lease ""; set stale 0
    for {set tick 0} {$hold_seconds<0 || $tick<$hold_seconds*4} {incr tick} {
        set lease ""
        catch {
            set f [open [file join $control lease.txt] r]
            try {set lease [string trim [read $f]]} finally {close $f}
        }
        if {$lease ne "" && [string is wideinteger -strict $lease] && $lease ne $previous_lease} {
            set previous_lease $lease; set stale 0
        } else {incr stale}
        if {$stale>=40} {error "Lease expired: source ordering unknown"}
        # Close takes precedence over a concurrent start on cancellation.
        if {!$sample_closed && [request close]} {
            set sample_closed 1; ack close
        }
        if {!$sample_started && !$sample_closed && [request start]} {
            set sample_started 1; ack start
        }
        if {[request restore]} {
            if {!$sample_closed} {error "Restore before close acknowledgement"}
            set restore_requested 1; break
        }
        after 250
        if {$tick%120==119} {
            refresh_hw_device -update_hw_probes false $device
            long_assert $device 26102101
        }
    }
    if {!$restore_requested} {error "Independent $hold_seconds-second hold limit"}
    refresh_hw_device -update_hw_probes false $device
    report_property -all $device -file [file join $out device_end.rpt]
    long_assert $device 26102101
} test_message test_options]
if {$attempted} {
    # Preserve the precise transition boundary independently of supervisor polling.
    set stamp_code [catch {
        set stamp [string trim [exec $python -X utf8 -E -B [file join $scripts bridge_ordered_clock.py]]]
        if {![string is wideinteger -strict $stamp] || $stamp<=0} {error "Invalid recovery QPC"}
        long_write [file join $out recovery_started_qpc.txt] $stamp
    } stamp_message]
    if {$stamp_code} {set test_code 1; set test_message $stamp_message; set test_options [dict create -code 1 -level 0]}
    set recovery_code [catch {long_recover $device $out} recovery_message]
}
long_cleanup
long_write [file join $out operation.txt] "run_id=$run_id\nflash_write_called=false\nload_attempted=$attempted\nloaded=$loaded\nsample_started=$sample_started\nsample_closed=$sample_closed\nrestore_requested=$restore_requested\ntest_exit=$test_code\ntest_message=$test_message\nrecovery_exit=$recovery_code\nrecovery_message=$recovery_message\nphysical_display_verified=false\nvisual_recovery_confirmed=false"
if {$recovery_code!=0 && $attempted} {error "Recovery UNKNOWN: $recovery_message"}
if {$test_code} {return -options $test_options $test_message}
puts "FACTORY_CONFIG_RESTORED: same-run visual confirmation still required"
