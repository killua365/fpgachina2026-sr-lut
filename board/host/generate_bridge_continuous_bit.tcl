# Offline BIT generation from the separately reviewed 26100901 route only.
if {$argc!=2} {error "Usage: generate_bridge_continuous_bit.tcl PYTHON NEW_DIRECTORY"}
if {![string match "2026.1*" [version -short]]} {error "Requires Vivado 2026.1"}
set scripts [file dirname [file normalize [info script]]]
set root [file normalize [file join $scripts ../..]]
set python [file normalize [lindex $argv 0]]
set out [file normalize [lindex $argv 1]]
set verdict [exec $python -E -B [file join $scripts bridge_continuous_candidate.py] preflight]
puts $verdict
if {![regexp {"dcp_sha256": "([0-9a-f]{64})"} $verdict match dcp_sha]} {error "Missing reviewed DCP identity"}
if {[file exists $out]} {error "Refuse existing candidate"}
set relative build/frozen/nexys_2026-10-11_n4_continuous_board_2026_r1/route/routed.dcp
file mkdir $out
open_checkpoint [file join $root $relative]
if {[get_property PART [current_design]] ne "xc7a200tsbg484-1"} {error "Wrong part"}
set_property BITSTREAM.CONFIG.USR_ACCESS 0x26100901 [current_design]
report_drc -file [file join $out bitgen_drc.rpt]
set identity [open [file join $out bitgen_identity.txt] w]
puts $identity "vivado=[version -short]\npart=[get_property PART [current_design]]\nuser_access=[get_property BITSTREAM.CONFIG.USR_ACCESS [current_design]]\nsource_dcp=$relative\nsource_sha256=$dcp_sha\nflash_write_allowed=false"
close $identity
write_bitstream [file join $out pattern.bit]
close_design
