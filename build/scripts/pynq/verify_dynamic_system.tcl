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
set minimum_setup_wns [dict get $stage_config minimum_setup_wns]
set mode_gpio_enabled [dict get $stage_config mode_gpio_enabled]
set scale_gpio_enabled [dict get $stage_config scale_gpio_enabled]
set scale [dict get $stage_config scale]
set project_file [file join $build_dir sr_lut_s${stage_number}_dyn.xpr]

if {![file isfile $project_file]} {
  error "Stage $stage_number x$scale optimized project is missing"
}

open_project $project_file
set synth_status [get_property STATUS [get_runs synth_1]]
set impl_status [get_property STATUS [get_runs impl_1]]
puts "${stage_marker}_VERIFY_SYNTH_STATUS $synth_status"
puts "${stage_marker}_VERIFY_IMPL_STATUS $impl_status"
if {![string match "*Complete*" $synth_status]
    || ![string match "*Complete*" $impl_status]} {
  error "Stage $stage_number synthesis or implementation is incomplete"
}

open_run impl_1
if {$mode_gpio_enabled} {
  foreach pin_pair [list \
      [list {btns_4bits[0]} D19] \
      [list {btns_4bits[1]} D20] \
      [list {btns_4bits[2]} L20] \
      [list {btns_4bits[3]} L19] \
      [list {leds_4bits[0]} R14] \
      [list {leds_4bits[1]} P14] \
      [list {leds_4bits[2]} N16] \
      [list {leds_4bits[3]} M14]] {
    set port_name [lindex $pin_pair 0]
    set expected_pin [lindex $pin_pair 1]
    set port [get_ports -quiet $port_name]
    if {[llength $port] != 1
        || [get_property PACKAGE_PIN $port] ne $expected_pin
        || [get_property IOSTANDARD $port] ne "LVCMOS33"} {
      error "Stage $stage_number routed pin mismatch for $port_name"
    }
  }
}
if {$scale_gpio_enabled} {
  foreach pin_pair [list \
      [list {sws_2bits[0]} M20] \
      [list {sws_2bits[1]} M19]] {
    set port_name [lindex $pin_pair 0]
    set expected_pin [lindex $pin_pair 1]
    set port [get_ports -quiet $port_name]
    if {[llength $port] != 1
        || [get_property PACKAGE_PIN $port] ne $expected_pin
        || [get_property IOSTANDARD $port] ne "LVCMOS33"} {
      error "Stage $stage_number routed pin mismatch for $port_name"
    }
  }
}
set setup_path [get_timing_paths -setup -max_paths 1]
set hold_path [get_timing_paths -hold -max_paths 1]
set setup_wns [get_property SLACK $setup_path]
set hold_wns [get_property SLACK $hold_path]
puts "${stage_marker}_VERIFY_TIMING setup_wns=$setup_wns hold_wns=$hold_wns"
if {$setup_wns < $minimum_setup_wns || $hold_wns < 0.0} {
  error "Stage $stage_number routed design misses setup/hold targets: setup=$setup_wns target=$minimum_setup_wns hold=$hold_wns"
}

report_drc -file [file join $results_dir stage_${stage_number}q_scale${scale}_postbuild_drc.rpt]
set drc_errors [get_drc_violations -quiet -filter {SEVERITY == Error}]
puts "${stage_marker}_VERIFY_DRC_ERROR_COUNT [llength $drc_errors]"
if {[llength $drc_errors] != 0} {
  error "Stage $stage_number routed design has DRC errors"
}

foreach artifact [list \
    [file join $overlay_dir sr_lut_x${scale}.bit] \
    [file join $overlay_dir sr_lut_x${scale}.hwh] \
    [file join $overlay_dir sr_lut_x${scale}.xsa]] {
  if {![file isfile $artifact] || [file size $artifact] == 0} {
    error "Stage $stage_number hardware artifact is missing or empty: $artifact"
  }
  puts "${stage_marker}_VERIFY_ARTIFACT $artifact [file size $artifact]"
}

set hwh_file [file join $overlay_dir sr_lut_x${scale}.hwh]
set hwh_channel [open $hwh_file r]
set hwh_text [read $hwh_channel]
close $hwh_channel
foreach required_text [list \
    {INSTANCE="axi_gpio_perf_0"} \
    {INSTANCE="axi_gpio_perf_1"} \
    {INSTANCE="sr_lut_scale_dynamic_0"}] {
  if {[string first $required_text $hwh_text] < 0} {
    error "Stage $stage_number HWH is missing $required_text"
  }
}
foreach address_pair [list \
    [list axi_gpio_perf_0 0x41200000] \
    [list axi_gpio_perf_1 0x41210000]] {
  set instance [lindex $address_pair 0]
  set base [lindex $address_pair 1]
  set pattern [format {<MEMRANGE[^>]*BASEVALUE="%s"[^>]*INSTANCE="%s"} \
      $base $instance]
  if {![regexp $pattern $hwh_text]} {
    error "Stage $stage_number HWH address mismatch for $instance: expected $base"
  }
}
if {$mode_gpio_enabled} {
  foreach required_text [list \
      {INSTANCE="axi_gpio_mode_0"} \
      {NAME="btns_4bits"} \
      {NAME="leds_4bits"}] {
    if {[string first $required_text $hwh_text] < 0} {
      error "Stage $stage_number HWH is missing $required_text"
    }
  }
  set mode_pattern {<MEMRANGE[^>]*BASEVALUE="0x41230000"[^>]*INSTANCE="axi_gpio_mode_0"}
  if {![regexp $mode_pattern $hwh_text]} {
    error "Stage $stage_number HWH address mismatch for axi_gpio_mode_0"
  }
}
if {$scale_gpio_enabled} {
  foreach required_text [list \
      {INSTANCE="axi_gpio_scale_0"} \
      {NAME="sws_2bits"}] {
    if {[string first $required_text $hwh_text] < 0} {
      error "Stage $stage_number HWH is missing $required_text"
    }
  }
  set scale_gpio_pattern {<MEMRANGE[^>]*BASEVALUE="0x41240000"[^>]*INSTANCE="axi_gpio_scale_0"}
  if {![regexp $scale_gpio_pattern $hwh_text]} {
    error "Stage $stage_number HWH address mismatch for axi_gpio_scale_0"
  }
}
if {[string first {INSTANCE="axi_gpio_mjpeg_status"} $hwh_text] >= 0} {
  set mjpeg_pattern {<MEMRANGE[^>]*BASEVALUE="0x41220000"[^>]*INSTANCE="axi_gpio_mjpeg_status"}
  if {![regexp $mjpeg_pattern $hwh_text]} {
    error "Stage $stage_number HWH address mismatch for axi_gpio_mjpeg_status"
  }
}

puts "${stage_marker}_X${scale}_OPTIMIZED_POSTBUILD_VERIFY_PASS"
close_project
