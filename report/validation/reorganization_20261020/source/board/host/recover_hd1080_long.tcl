# Bounded externally; reconnect only to recorded Nexys and boot original QSPI.
if {$argc!=2} {error "Usage: recover_hd1080_long.tcl ATTEMPT_MARKER NEW_OUT"}
if {![string match "2026.1*" [version -short]]} {error "Requires 2026.1"}
lassign $argv marker out
if {![file exists $marker] || [file exists $out]} {error "Missing programming attempt or reused recovery directory"}
source [file join [file dirname [file normalize [info script]]] longrun_hw.tcl]
file mkdir $out
set code [catch {
    lassign [long_connect] target device
    report_property -all $device -file [file join $out device_before_recovery.rpt]
    long_recover $device $out
} message]
long_cleanup
long_write [file join $out operation.txt] "recovery_exit=$code\nrecovery_message=$message\nflash_write_called=false\nvisual_recovery_confirmed=false"
if {$code} {error "Recovery remains UNKNOWN: $message"}
