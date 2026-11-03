# Offline-only audit: never writes a BIT or connects a hardware target.
if {$argc < 2 || $argc > 11} {error "Usage: audit_reference.tcl NEW_OUTPUT_DIR synth|route ?variant? ?timestamp_scale? ?link_probe? ?ser? ?scrambling_bypass? ?gtp_health? ?stream_capture? ?tu_size? ?sink_power?"}
set root [file normalize [file join [file dirname [info script]] ../../..]]
set out [file normalize [lindex $argv 0]]
set step [lindex $argv 1]
set variant [expr {$argc >= 3 ? [lindex $argv 2] : "upstream"}]
set timestamp_scale [expr {$argc >= 4 ? [lindex $argv 3] : 1}]
set link_probe [expr {$argc >= 5 ? [lindex $argv 4] : 0}]
set ser_mode [expr {$argc >= 6 ? [lindex $argv 5] : 0}]
set scrambling_bypass [expr {$argc >= 7 ? [lindex $argv 6] : 0}]
set gtp_health [expr {$argc >= 8 ? [lindex $argv 7] : 0}]
set stream_capture [expr {$argc >= 9 ? [lindex $argv 8] : 0}]
set tu_size [expr {$argc >= 10 ? [lindex $argv 9] : 40}]
set sink_power [expr {$argc >= 11 ? [lindex $argv 10] : 0}]
if {$sink_power ni {0 1} || ($sink_power && !$gtp_health)} {error "Sink power requires GTP health telemetry"}
if {$tu_size ni {40 64} || ($tu_size != 40 && ($variant ne "dp1080" || $ser_mode || $scrambling_bypass || $timestamp_scale != 1))} {error "Unreviewed TU profile"}
if {$stream_capture ni {0 1} || ($stream_capture && !$gtp_health)} {error "Stream capture requires GTP health"}
if {$gtp_health ni {0 1} || ($gtp_health && ($ser_mode || $scrambling_bypass || $variant ne "dp1080" || !$link_probe || $timestamp_scale != 1))} {error "Unreviewed GTP health configuration"}
if {$scrambling_bypass ni {0 1} || ($scrambling_bypass && ($ser_mode || $variant ne "dp1080" || !$link_probe || $timestamp_scale != 1))} {error "Unreviewed scrambling bypass configuration"}
if {$ser_mode ni {0 1} || ($ser_mode && ($variant ne "dp1080" || !$link_probe || $timestamp_scale != 1))} {error "Unreviewed SER configuration"}
if {$link_probe ni {0 1} || ($link_probe && $variant ne "dp1080")} {error "Unreviewed link probe configuration"}
if {$timestamp_scale ni {1 2048} || ($timestamp_scale != 1 && $variant ne "dp1080")} {error "Unreviewed timestamp configuration"}
set cdc_review [expr {$variant in {cdc_review bridge_control dp1080}}]
set adapted [expr {$variant in {bridge_control dp1080}}]
if {$argc >= 3 && !$cdc_review} {error "Unknown audit variant"}
if {$step ni {synth route}} {error "Unknown step $step"}
if {[file exists $out]} {error "Output exists; use a fresh audit directory: $out"}
if {![string match "2026.1*" [version -short]]} {error "This audit is pinned to installed Vivado 2026.1"}
file mkdir $out
cd $out
set vendor [file join $root src/third_party/fpga_displayport]
set metadata [open [file join $out audit_identity.txt] w]
set profile [expr {$variant eq "dp1080" ? "1920x1080_60_rgb888_2lane_offline_audit" : "800x600_offline_audit"}]
if {$ser_mode} {set profile "hbr_2lane_enhanced_ser_diagnostic_only"}
if {$scrambling_bypass} {set profile "1920x1080_60_rgb888_2lane_paired_scrambling_bypass_diagnostic"}
puts $metadata "tool=[version -short]\npart=xc7a200tsbg484-1\nprofile=$profile\nvariant=$variant\ntimestamp_scale=$timestamp_scale\nlink_probe=$link_probe\nser_mode=$ser_mode\ndeployable=false"
close $metadata
set metadata [open [file join $out audit_identity.txt] a]
puts $metadata "scrambling_bypass=$scrambling_bypass\ngtp_health=$gtp_health"
puts $metadata "stream_capture=$stream_capture"
puts $metadata "transfer_unit_size=$tu_size"
puts $metadata "sink_power=$sink_power"
close $metadata
create_project nexys_dp_reference [file join $out project] -part xc7a200tsbg484-1
set_property XPM_LIBRARIES {XPM_CDC} [current_project]
set_property target_language VHDL [current_project]
set_property simulator_language Mixed [current_project]
set sources {
    dp_aux_messages.vhd aux_interface.vhd artix7/gtx_tx_reset_controller.vhd
    scrambler_reset_inserter.vhd link_signal_mgmt.vhd idle_pattern_inserter.vhd
    hotplug_decode.vhd edid_decode.vhd dp_register_decode.vhd aux_channel.vhd
    data_to_8b10b.vhd scrambler_all_channels.vhd skew_channels.vhd
    insert_training_pattern.vhd main_stream_processing.vhd channel_managemnt.vhd
    artix7/transceiver.vhd top_level.vhd test_streams/test_source.vhd
    test_streams/test_source_800_600_RGB_444_colourbars_ch1.vhd
    test_streams/insert_main_stream_attrbutes_one_channel.vhd
}
if {$adapted} {
    read_vhdl [file join $root src/rtl/nexys_bridge/edid_observer.vhd]
    read_vhdl [file join $root src/rtl/nexys_bridge/diagnostic_mux.vhd]
    read_vhdl [file join $root src/rtl/nexys_bridge/enhanced_framing.vhd]
    read_vhdl [file join $root src/rtl/nexys_bridge/epoch_scrambler_reset.vhd]
    read_vhdl [file join $root src/rtl/nexys_bridge/ser_pattern_source.vhd]
    read_vhdl [file join $root src/rtl/nexys_bridge/ser_measurement_window.vhd]
    read_vhdl [file join $root src/rtl/nexys_bridge/ser_diagnostic_mux.vhd]
    read_vhdl [file join $root src/rtl/nexys_bridge/diagnostic_uart.vhd]
    read_vhdl [file join $root src/rtl/nexys_bridge/stream_diagnostic.vhd]
    read_vhdl -vhdl2008 [file join $root src/rtl/nexys_bridge/gtp_health_monitor.vhd]
    read_vhdl [file join $root src/rtl/nexys_bridge/gtp_diagnostic_mux.vhd]
    read_vhdl -vhdl2008 [file join $root src/rtl/nexys_bridge/stream_window_capture.vhd]
    read_vhdl -vhdl2008 [file join $root src/rtl/nexys_bridge/stream_capture_mux.vhd]
    read_vhdl [file join $root src/rtl/nexys_bridge/aux_io.vhd]
    read_vhdl [file join $root src/rtl/nexys_bridge/gtp_tx_drive.vhd]
    read_vhdl [file join $root src/rtl/nexys_bridge/gtp_ref_monitor.vhd]
    read_vhdl -vhdl2008 [file join $root src/rtl/nexys_bridge/dp_1080p60_source.vhd]
}
foreach source $sources {
    set path [file join $vendor src $source]
    if {$adapted && [file tail $source] in {hotplug_decode.vhd aux_channel.vhd aux_interface.vhd channel_managemnt.vhd gtx_tx_reset_controller.vhd transceiver.vhd link_signal_mgmt.vhd top_level.vhd edid_decode.vhd dp_register_decode.vhd dp_aux_messages.vhd main_stream_processing.vhd idle_pattern_inserter.vhd}} {
        set path [file join $root src/rtl/nexys_bridge [file tail $source]]
    }
    if {![file isfile $path]} {error "Missing pinned source: $path"}
    if {[file tail $source] eq "main_stream_processing.vhd" && $adapted} {
        read_vhdl -vhdl2008 $path
    } else {
        read_vhdl $path
    }
}
if {$adapted} {
    read_xdc [file join $root build/scripts/nexys/reference_pins.xdc]
} else {
    read_xdc [file join $vendor constraints/NexysVideo.xdc]
}
set_property top top_level [current_fileset]
update_compile_order -fileset sources_1
if {$variant eq "dp1080"} {
    synth_design -top top_level -part xc7a200tsbg484-1 -generic [list stream_1080p60=true timestamp_scale=$timestamp_scale transfer_unit_size=$tu_size link_probe_mode=[expr {$link_probe ? "true" : "false"}] ser_mode=[expr {$ser_mode ? "true" : "false"}] disable_scrambling=[expr {$scrambling_bypass ? "true" : "false"}] gtp_health_mode=[expr {$gtp_health ? "true" : "false"}] stream_capture_mode=[expr {$stream_capture ? "true" : "false"}] initialize_sink_power=[expr {$sink_power ? "true" : "false"}]]
} else {
    synth_design -top top_level -part xc7a200tsbg484-1
}
if {$adapted} {
    # Digilent Nexys-Video-Master.xdc configuration bank settings.
    # Reference SHA256: 2DEBF0AFB537C0A9F8F2C1569AA99E0366629A354F2FBDF369BD5142670086CC.
    set_property CONFIG_VOLTAGE 3.3 [current_design]
    set_property CFGBVS VCCO [current_design]
    source [file join $root build/scripts/nexys/reference_clocks.tcl]
}
if {$cdc_review} {
    if {[catch {source [file join $root build/scripts/nexys/reference_cdc.tcl]} cdc_error]} {
        # Preserve the actual netlist for inspection; this is not a passed audit.
        write_checkpoint [file join $out cdc_failure.dcp]
        set names [open [file join $out cdc_failure_cells.txt] w]
        foreach cell [lsort [get_cells -hier -filter {NAME =~ *ser_arm* || NAME =~ *ser_ready*}]] {
            puts $names "$cell [get_property REF_NAME $cell]"
        }
        close $names
        error $cdc_error
    }
}
write_checkpoint [file join $out synthesized.dcp]
report_utilization -file [file join $out utilization_synth.rpt]
report_clocks -file [file join $out clocks_synth.rpt]
report_drc -file [file join $out drc_synth.rpt]
report_cdc -details -file [file join $out cdc_synth.rpt]
if {$step eq "route"} {
    opt_design
    place_design
    phys_opt_design
    route_design
    write_checkpoint [file join $out routed.dcp]
    report_timing_summary -delay_type min_max -report_unconstrained -check_timing_verbose -file [file join $out timing_route.rpt]
    report_utilization -file [file join $out utilization_route.rpt]
    report_drc -file [file join $out drc_route.rpt]
    report_cdc -details -file [file join $out cdc_route.rpt]
    report_methodology -file [file join $out methodology_route.rpt]
    report_clock_interaction -file [file join $out clock_interaction.rpt]
    report_clocks -file [file join $out clocks_route.rpt]
    report_exceptions -coverage -file [file join $out exceptions_route.rpt]
}
puts "OFFLINE_AUDIT_FINISHED: no bitstream generated; no hardware programmed"
close_project
