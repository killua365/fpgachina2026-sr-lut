# One bounded JTAG SRAM experiment. Always attempt factory boot after a load attempt.
# Does not create or program any Flash/configuration-memory object.
if {$argc != 4} {error "Usage: run_sram_pattern.tcl PYTHON CANDIDATE NEW_RESULTS HOLD_SECONDS"}
if {![string match "2026.1*" [version -short]]} {error "Requires installed Vivado 2026.1"}
set scriptdir [file dirname [file normalize [info script]]]
set python [file normalize [lindex $argv 0]]
set candidate [file normalize [lindex $argv 1]]
set out [file normalize [lindex $argv 2]]
set seconds [lindex $argv 3]
if {![string is integer -strict $seconds] || $seconds < 10 || $seconds > 600} {error "Hold must be 10..600 seconds"}
if {[file exists $out]} {error "Refusing to reuse results directory"}
puts [exec $python -E -B [file join $scriptdir sram_candidate.py] check --candidate $candidate --backup]
file mkdir $out
proc assert_device {device} {
    foreach {property expected} {
        IDCODE_HEX 13636093
        REGISTER.CONFIG_STATUS.BIT00_CRC_ERROR 0
        REGISTER.CONFIG_STATUS.BIT12_INIT_B_PIN 1
        REGISTER.CONFIG_STATUS.BIT14_DONE_PIN 1
        REGISTER.CONFIG_STATUS.BIT15_IDCODE_ERROR 0
        {REGISTER.CONFIG_STATUS.BIT08_MODE_PIN_M[0]} 1
        {REGISTER.CONFIG_STATUS.BIT09_MODE_PIN_M[1]} 0
        {REGISTER.CONFIG_STATUS.BIT10_MODE_PIN_M[2]} 0
    } {
        if {[get_property $property $device] ne $expected} {error "FPGA status or QSPI mode mismatch: $property"}
    }
}
open_hw_manager
connect_hw_server -url localhost:3121
set targets [get_hw_targets -quiet *210276B3E2AD*]
if {[llength $targets] != 1} {error "Expected the recorded Nexys USB-JTAG serial only"}
set target [lindex $targets 0]
current_hw_target $target
set_property PARAM.FREQUENCY 15000000 $target
open_hw_target $target
set devices [get_hw_devices -quiet -filter {PART == xc7a200t}]
if {[llength $devices] != 1 || [llength [get_hw_devices]] != 1} {error "Unexpected device chain"}
set device [lindex $devices 0]
current_hw_device $device
refresh_hw_device -update_hw_probes false $device
report_property -all $device -file [file join $out device_before.rpt]
assert_device $device
if {[get_property REGISTER.USR_ACCESS $device] ne "00000000"} {error "Expected preserved factory image before this experiment"}
file copy [file join $candidate candidate.json] [file join $out candidate.json]
set attempted 0
set loaded 0
set test_code [catch {
    set_property PROGRAM.FILE [file join $candidate pattern.bit] $device
    set attempted 1
    program_hw_devices $device
    set loaded 1
    refresh_hw_device -update_hw_probes false $device
    report_property -all $device -file [file join $out device_loaded.rpt]
    assert_device $device
    if {[get_property REGISTER.USR_ACCESS $device] ne "26092701"} {error "Loaded candidate identity differs"}
    set marker [open [file join $out active.txt] w]
    puts $marker "started_utc=[clock format [clock seconds] -gmt 1 -format {%Y-%m-%dT%H:%M:%SZ}]\nhold_seconds=$seconds\nphysical_display_verified=false"
    close $marker
    puts "CANDIDATE_ACTIVE_1080P60: observe DELL OSD, moving frame number, borders and colors."
    flush stdout
    for {set n 0} {$n < $seconds} {incr n} {
        if {[file exists [file join $out stop.request]]} {break}
        after 1000
        if {$n % 30 == 29} {
            refresh_hw_device -update_hw_probes false $device
            assert_device $device
            puts "SRAM_EXPERIMENT elapsed=[expr {$n + 1}] seconds; config status checked; display acceptance pending"
            flush stdout
        }
    }
    report_property -all $device -file [file join $out device_end.rpt]
} test_message test_options]
set recovery_code -1
set recovery_message "load not attempted"
if {$attempted} {
    set recovery_code [catch {
        boot_hw_device $device
        after 5000
        refresh_hw_device -update_hw_probes false $device
        report_property -all $device -file [file join $out device_restored.rpt]
        assert_device $device
        if {[get_property REGISTER.USR_ACCESS $device] ne "00000000"} {error "Factory identity not restored"}
    } recovery_message]
}
set result [open [file join $out operation.txt] w]
puts $result "target=$target\nflash_write_called=false\nload_attempted=$attempted\nloaded=$loaded\ntest_exit=$test_code\ntest_message=$test_message\nrecovery_exit=$recovery_code\nrecovery_message=$recovery_message\nphysical_display_verified=false\nvisual_recovery_confirmed=false"
close $result
close_hw_target $target
disconnect_hw_server
close_hw_manager
if {$recovery_code != 0} {error "Factory recovery needs attention: $recovery_message"}
if {$test_code} {return -options $test_options $test_message}
puts "FACTORY_CONFIG_RESTORED: user must confirm original 1280x1024 color pattern."
