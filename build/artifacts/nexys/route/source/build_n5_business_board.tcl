# Complete N5 implementation review. Does not generate BIT or open hardware.
if {$argc!=3} {error "Usage: build_n5_business_board.tcl NEW_OUT CANDIDATE_ID_HEX IP_SOURCE_ROOT"}
set candidate_id [lindex $argv 1]
if {$candidate_id ne "26101201"} {error "Continuous clock profile requires 26101201"}
if {![regexp {^[0-9A-Fa-f]{8}$} $candidate_id]} {error "Candidate ID must be eight hexadecimal digits"}
if {![string match "2026.1*" [version -short]]} {error "Requires Vivado 2026.1"}
set root [file normalize [file join [file dirname [info script]] ../..]]
set out [file normalize [lindex $argv 0]]
set ip_root [file normalize [lindex $argv 2]]
if {[file exists $out]} {error "Refuse existing output"}
file mkdir $out
file mkdir [file join $out source]
file copy [info script] [file join $out source]
set identity [open [file join $out build_identity.json] w]
puts $identity "{\"candidate_id\":\"$candidate_id\",\"hardware_tested\":false}"
close $identity
create_project n5_bridge [file join $out project] -part xc7a200tsbg484-1
set_property XPM_LIBRARIES {XPM_CDC XPM_MEMORY XPM_FIFO} [current_project]
set_property target_language Verilog [current_project]
set_property simulator_language Mixed [current_project]
proc snapshot {relative language} {
    global root out
    set dest [file join $out source $relative]
    file mkdir [file dirname $dest]
    file copy [file join $root $relative] $dest
    if {$language eq "sv"} {read_verilog -sv $dest
    } elseif {$language eq "vhdl"} {read_vhdl -vhdl2008 $dest
    } elseif {$language eq "xdc"} {read_xdc $dest}
    return $dest
}
foreach name {axi_lite_command vdma_frame_control vdma_axi_fence video_pair_fifo} {
    snapshot src/rtl/nexys_framebuffer/${name}.sv sv
}
foreach name {bridge_frame_owner hdmi_geometry hdmi_frame_input hdmi_token_cdc atomic_status_cdc bridge_pipeline_control
    sr_identity_marker_rx sr_identity_tracker video_crc_check bridge_core bridge_uart bridge_telemetry bridge_board_top_continuous} {
    snapshot src/rtl/nexys_hdmi/${name}.sv sv
}
snapshot src/rtl/nexys_hdmi/frame_crc32.vh text
snapshot src/rtl/nexys_hdmi/hdmi_receiver.vhd vhdl
set edid [snapshot src/rtl/nexys_hdmi/n4_1080p30.data text]
set_property include_dirs [list [file join $out source/src/rtl/nexys_hdmi]] [get_filesets sources_1]
foreach name {DVI_Constants SyncAsync SyncAsyncReset SyncBase GlitchFilter TWI_SlaveCtl EEPROM_8b
    InputSERDES PhaseAlign ChannelBond TMDS_Clocking TMDS_Decoder ResyncToBUFG dvi2rgb} {
    if {$name in {TWI_SlaveCtl EEPROM_8b}} {
        snapshot src/rtl/nexys_hdmi/${name}.vhd vhdl
    } else {
        snapshot src/third_party/digilent_hdmi_rx/ip/dvi2rgb/src/${name}.vhd vhdl
    }
}
set receiver_xdc [snapshot src/third_party/digilent_hdmi_rx/ip/dvi2rgb/src/dvi2rgb.xdc text]
read_xdc -ref dvi2rgb $receiver_xdc
foreach name {dp_framebuffer_source frame_idle_pattern_inserter frame_main_stream_processing dp_framebuffer_link_continuous} {snapshot src/rtl/nexys_framebuffer/${name}.vhd vhdl}
foreach name {edid_observer diagnostic_mux enhanced_framing epoch_scrambler_reset ser_pattern_source
    ser_measurement_window ser_diagnostic_mux diagnostic_uart stream_diagnostic gtp_health_monitor
    gtp_diagnostic_mux stream_window_capture stream_capture_mux aux_io gtp_tx_drive gtp_ref_monitor} {
    snapshot src/rtl/nexys_bridge/${name}.vhd vhdl
}
foreach source {
    dp_aux_messages.vhd aux_interface.vhd artix7/gtx_tx_reset_controller.vhd
    scrambler_reset_inserter.vhd link_signal_mgmt.vhd idle_pattern_inserter.vhd
    hotplug_decode.vhd edid_decode.vhd dp_register_decode.vhd aux_channel.vhd
    data_to_8b10b.vhd scrambler_all_channels.vhd skew_channels.vhd
    insert_training_pattern.vhd main_stream_processing.vhd channel_managemnt.vhd artix7/transceiver.vhd
} {
    set path src/third_party/fpga_displayport/src/$source
    if {[file tail $source] in {hotplug_decode.vhd aux_channel.vhd aux_interface.vhd channel_managemnt.vhd
        gtx_tx_reset_controller.vhd transceiver.vhd link_signal_mgmt.vhd edid_decode.vhd dp_register_decode.vhd
        dp_aux_messages.vhd main_stream_processing.vhd idle_pattern_inserter.vhd}} {
        set path src/rtl/nexys_bridge/[file tail $source]
    }
    snapshot $path vhdl
}
import_ip -files [file join $ip_root build/nexys_bridge/work/n3_shared_mig_r1/project/n3_shared.srcs/sources_1/ip/n3_mig/n3_mig.xci]
import_ip -files [file join $ip_root build/nexys_bridge/work/n3_vdma_frame_r3/project/n3_vdma_frame.srcs/sources_1/ip/n3_vdma/n3_vdma.xci]
generate_target all [get_ips n3_vdma]
set vdma_xci [get_files -all */n3_vdma.xci]
if {[llength $vdma_xci]!=1} {error "Expected exactly one VDMA XCI"}
create_ip_run $vdma_xci
launch_runs n3_vdma_synth_1 -jobs 4
wait_on_run n3_vdma_synth_1
if {[get_property STATUS [get_runs n3_vdma_synth_1]] ne "synth_design Complete!"} {
    error "VDMA out-of-context synthesis did not complete"
}
snapshot build/nexys_bridge/frame_board_pins.xdc xdc
snapshot build/nexys_bridge/bridge_hdmi_pins.xdc xdc
set clocks [snapshot build/nexys_bridge/frame_board_clocks.tcl text]
set cdc [snapshot build/nexys_bridge/frame_board_cdc.tcl text]
set hdmi_cdc [snapshot build/nexys_bridge/bridge_hdmi_cdc.tcl text]
synth_design -top bridge_board_top_continuous -part xc7a200tsbg484-1 -generic [list EDID_FILE=$edid CANDIDATE_ID=32'h$candidate_id BUSINESS_IDENTITY=1]
set_property CONFIG_VOLTAGE 3.3 [current_design]
set_property CFGBVS VCCO [current_design]
source $clocks
source $hdmi_cdc
if {[catch {source $cdc} message]} {
    write_checkpoint [file join $out cdc_failure.dcp]
    error $message
}
set ref_reset_chain [get_cells -hier -filter {NAME =~ *u_iodelay_ctrl/rst_ref_sync_r_reg*}]
if {[llength $ref_reset_chain]!=15} {error "Unexpected MIG reference reset chain"}
set_property ASYNC_REG TRUE $ref_reset_chain
write_checkpoint [file join $out synthesized.dcp]
report_drc -file [file join $out drc_synth.rpt]
report_cdc -details -file [file join $out cdc_synth.rpt]
report_clocks -file [file join $out clocks_synth.rpt]
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
report_bus_skew -file [file join $out bus_skew_route.rpt]
set f [open [file join $out completion.txt] w]
puts $f "board_routed=true\nbitstream_generated=false\nboard_tested=false\nn5_passed=false"
close $f
puts "N5_BRIDGE_BOARD_ROUTED_NO_PROGRAMMING"
