# Re-implement the same VGA logic using the qualified physical design as reference.
# All hardware changes remain in the separately generated SRAM candidate.
if {![string match "2026.1*" [version -short]]} {error "Vivado 2026.1 required"}
set_param general.maxThreads 2
set root [file normalize [file join [file dirname [info script]] ../../..]]
set work [file join $root build reproduction_work nexys_vga_26102101]
set out [file join $work route_incremental]
if {[file exists $out]} {error "Refuse existing incremental output"}
file mkdir $out
open_checkpoint [file join $work route synthesized.dcp]
if {[get_property PART [current_design]] ne "xc7a200tsbg484-1"} {error "Wrong part"}
opt_design
read_checkpoint -incremental [file join $root build artifacts nexys route routed.dcp]
place_design
phys_opt_design
route_design
report_incremental_reuse -file [file join $out incremental_reuse.rpt]
write_checkpoint [file join $out routed.dcp]
report_timing_summary -delay_type min_max -report_unconstrained -check_timing_verbose -file [file join $out timing_route.rpt]
report_utilization -file [file join $out utilization_route.rpt]
report_drc -file [file join $out drc_route.rpt]
report_cdc -details -file [file join $out cdc_route.rpt]
report_methodology -file [file join $out methodology_route.rpt]
set setup [get_timing_paths -delay_type max -max_paths 1]
set hold [get_timing_paths -delay_type min -max_paths 1]
if {[llength $setup]!=1 || [llength $hold]!=1} {error "Missing candidate timing paths"}
if {[get_property SLACK $setup]<0 || [get_property SLACK $hold]<0} {error "Candidate timing failed"}
if {[llength [get_drc_violations -quiet -filter {SEVERITY == Error || SEVERITY == "Critical Warning"}]]} {error "Candidate DRC failed"}
set_property BITSTREAM.CONFIG.USR_ACCESS 0x26102101 [current_design]
write_bitstream [file join $out pattern.bit]
set f [open [file join $out candidate_bit_created.txt] w]
puts $f "candidate=26102101\nimplementation_revision=2\nnormal_demo_eligible=false\nflash_write_allowed=false"
close $f
puts "VGA_INCREMENTAL_CANDIDATE_BIT_CREATED_NO_HARDWARE"
close_design
