# File generation only. A rebuilt BIT is NOT eligible for the existing demo gate.
if {$argc!=2} {error "Usage: nexys_write_bit.tcl ROUTED_DCP NEW_OUTPUT"}
if {![string match "2026.1*" [version -short]]} {error "Vivado 2026.1 required"}
set dcp [file normalize [lindex $argv 0]]
set out [file normalize [lindex $argv 1]]
if {[file exists $out]} {error "Refuse existing output"}
open_checkpoint $dcp
if {[get_property PART [current_design]] ne "xc7a200tsbg484-1"} {error "Wrong FPGA part"}
set setup [get_timing_paths -delay_type max -max_paths 1]
set hold [get_timing_paths -delay_type min -max_paths 1]
if {[llength $setup]!=1 || [llength $hold]!=1} {error "Missing timing paths"}
if {[get_property SLACK $setup]<0 || [get_property SLACK $hold]<0} {error "Timing failed"}
file mkdir $out
report_timing_summary -delay_type min_max -report_unconstrained -file [file join $out timing.rpt]
report_drc -file [file join $out drc.rpt]
if {[llength [get_drc_violations -quiet -filter {SEVERITY == Error || SEVERITY == "Critical Warning"}]]} {error "DRC failed"}
set_property BITSTREAM.CONFIG.USR_ACCESS 0x26101201 [current_design]
write_bitstream [file join $out pattern.bit]
set f [open [file join $out qualification_required.txt] w]
puts $f "New build only. Re-run RTL/host/pixel/on-board qualification before adopting it. Existing demo continues using the immutable qualified BIT."
close $f
close_design
puts "NEXYS_BIT_FILE_CREATED_NO_HARDWARE"
