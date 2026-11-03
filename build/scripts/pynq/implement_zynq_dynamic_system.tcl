set script_dir [file dirname [file normalize [info script]]]
set project_root [file normalize [file join $script_dir .. .. ..]]
source [file join $script_dir board_repository.tcl]
source [file join $script_dir stage_config.tcl]
set stage_config [sr_lut_stage_config $project_root dynamic_system]
set stage_number [dict get $stage_config stage_number]
set results_folder [dict get $stage_config results_folder]
set stage_marker "STAGE${stage_number}"
set build_dir [dict get $stage_config build_dir]
set results_dir [file join $project_root board $results_folder]
set overlay_dir [dict get $stage_config overlay_dir]
set implementation_strategy [dict get $stage_config implementation_strategy]
set scale [dict get $stage_config scale]
set project_name sr_lut_s${stage_number}_dyn
set design_name $project_name
set top_name ${design_name}_wrapper
set project_file [file join $build_dir ${project_name}.xpr]

if {![file isfile $project_file]} {
  error "Stage $stage_number scale-$scale optimized Vivado project is missing; run its build script first"
}

file mkdir $results_dir
file mkdir $overlay_dir
open_project $project_file

if {$implementation_strategy ne ""} {
  set_property strategy $implementation_strategy [get_runs impl_1]
}
puts "${stage_marker}_SCALE${scale}_IMPLEMENTATION_STRATEGY [get_property STRATEGY [get_runs impl_1]]"

reset_run synth_1
set implementation_jobs 2
if {[info exists ::env(SR_LUT_HDMI)] && $::env(SR_LUT_HDMI) eq "1"} {
  set implementation_jobs 1
  foreach ip_run [get_runs -filter {IS_SYNTHESIS == 1 && NAME != synth_1}] {
    if {[regexp -nocase {error|failed} [get_property STATUS $ip_run]]} {
      reset_run $ip_run
    }
  }
}
launch_runs synth_1 -jobs $implementation_jobs
wait_on_run synth_1
set synth_status [get_property STATUS [get_runs synth_1]]
puts "${stage_marker}_SCALE${scale}_OPTIMIZED_SYNTH_STATUS $synth_status"
if {![string match "*Complete*" $synth_status]} {
  error "Stage $stage_number x$scale synthesis did not complete: $synth_status"
}

open_run synth_1
report_utilization -hierarchical \
    -file [file join $results_dir stage_${stage_number}i_scale${scale}_system_synth_utilization.rpt]
report_timing_summary -delay_type min_max -report_unconstrained \
    -check_timing_verbose -max_paths 20 \
    -file [file join $results_dir stage_${stage_number}j_scale${scale}_system_synth_timing.rpt]
report_drc -file [file join $results_dir stage_${stage_number}k_scale${scale}_system_synth_drc.rpt]
close_design

reset_run impl_1
launch_runs impl_1 -to_step write_bitstream -jobs $implementation_jobs
wait_on_run impl_1
set impl_status [get_property STATUS [get_runs impl_1]]
puts "${stage_marker}_SCALE${scale}_OPTIMIZED_IMPL_STATUS $impl_status"
if {![string match "*Complete*" $impl_status]} {
  error "Stage $stage_number x$scale implementation did not complete: $impl_status"
}

open_run impl_1
report_utilization -hierarchical \
    -file [file join $results_dir stage_${stage_number}l_scale${scale}_system_route_utilization.rpt]
report_timing_summary -delay_type min_max -report_unconstrained \
    -check_timing_verbose -max_paths 20 \
    -file [file join $results_dir stage_${stage_number}m_scale${scale}_system_route_timing.rpt]
report_methodology \
    -file [file join $results_dir stage_${stage_number}n_scale${scale}_system_route_methodology.rpt]
report_drc \
    -file [file join $results_dir stage_${stage_number}o_scale${scale}_system_route_drc.rpt]
if {[info exists ::env(SR_LUT_HDMI)] && $::env(SR_LUT_HDMI) eq "1"} {
  report_cdc -details -file [file join $results_dir hdmi_x${scale}_cdc.rpt]
  report_clock_interaction -file [file join $results_dir hdmi_x${scale}_clock_interaction.rpt]
  report_bus_skew -file [file join $results_dir hdmi_x${scale}_bus_skew.rpt]
}
set setup_path [get_timing_paths -setup -max_paths 1]
set hold_path [get_timing_paths -hold -max_paths 1]
set setup_wns [get_property SLACK $setup_path]
set hold_wns [get_property SLACK $hold_path]
puts "${stage_marker}_SCALE${scale}_OPTIMIZED_ROUTE_TIMING setup_wns=$setup_wns hold_wns=$hold_wns"
if {$setup_wns < [dict get $stage_config minimum_setup_wns] || $hold_wns < 0.0} {
  error "Stage $stage_number x$scale optimized routed design does not meet timing"
}
if {[info exists ::env(SR_LUT_HDMI)] && $::env(SR_LUT_HDMI) eq "1"} {
  source [file join $script_dir check_hdmi_timing.tcl]
  sr_lut_hdmi_require_clean_timing [file join $results_dir stage_${stage_number}m_scale${scale}_system_route_timing.rpt]
}

set run_dir [get_property DIRECTORY [get_runs impl_1]]
set bit_file [file join $run_dir ${top_name}.bit]
set hwh_file [file join $build_dir ${project_name}.gen sources_1 bd \
    $design_name hw_handoff ${design_name}.hwh]
foreach artifact [list $bit_file $hwh_file] {
  if {![file isfile $artifact]} {
    error "Expected Stage $stage_number x$scale hardware artifact is missing: $artifact"
  }
}

file copy -force $bit_file [file join $overlay_dir sr_lut_x${scale}.bit]
file copy -force $hwh_file [file join $overlay_dir sr_lut_x${scale}.hwh]
write_hw_platform -fixed -include_bit -force \
    -file [file join $overlay_dir sr_lut_x${scale}.xsa]

puts "${stage_marker}_SCALE${scale}_IMPLEMENTATION_PASS"
puts "${stage_marker}_SCALE${scale}_BITSTREAM [file join $overlay_dir sr_lut_x${scale}.bit]"
puts "${stage_marker}_SCALE${scale}_HWH [file join $overlay_dir sr_lut_x${scale}.hwh]"
