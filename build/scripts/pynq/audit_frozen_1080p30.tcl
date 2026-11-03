# Read existing routed checkpoints only; never run synthesis or implementation.
set root [file normalize [file join [file dirname [info script]] ../../..]]
set out [file normalize [lindex $argv 0]]
if {[file exists $out]} { error "Audit destination already exists: $out" }
file mkdir $out
foreach scale {2 4} {
    set dcp "$root/build/hdmi_x${scale}_1080p30_work/sr_lut_s40_dyn.runs/impl_1/sr_lut_s40_dyn_wrapper_routed.dcp"
    open_checkpoint $dcp
    report_cdc -details -file "$out/x${scale}_cdc.rpt"
    report_clocks -file "$out/x${scale}_clocks.rpt"
    report_bus_skew -warn_on_violation -file "$out/x${scale}_bus_skew.rpt"
    report_timing_summary -report_unconstrained -file "$out/x${scale}_timing.rpt"
    write_bitstream -force "$out/x${scale}_audit.bit"
    close_design
}
exit
