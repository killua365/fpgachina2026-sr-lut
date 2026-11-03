# Actual installed VDMA simulation, no hardware access.
if {![string match "2026.1*" [version -short]]} {error "Requires Vivado 2026.1"}
if {$argc!=2} {error "Usage: simulate_n5_core.tcl NEW_OUTPUT IP_SOURCE_ROOT"}
set root [file normalize [file join [file dirname [info script]] ../../..]]
set out [file normalize [lindex $argv 0]]
set ip_root [file normalize [lindex $argv 1]]
if {[file exists $out]} {error "Refuse reused output"}
file mkdir $out
file mkdir [file join $out source]
file copy [file join $root sim/nexys/tb_sr_identity_core.sv] [file join $out source]
file copy [info script] [file join $out source]
create_project vdmatest [file join $out project] -part xc7a200tsbg484-1
set_property target_language Verilog [current_project]
foreach name {axi_lite_command.sv vdma_frame_control.sv vdma_axi_fence.sv video_pair_fifo.sv} {
    file copy [file join $root src/rtl/nexys_framebuffer $name] [file join $out source]
    add_files [file join $out source $name]
}
foreach name {bridge_frame_owner.sv hdmi_geometry.sv hdmi_token_cdc.sv hdmi_frame_input.sv atomic_status_cdc.sv
    bridge_pipeline_control.sv sr_identity_marker_rx.sv sr_identity_tracker.sv video_crc_check.sv bridge_core.sv frame_crc32.vh} {
    file copy [file join $root src/rtl/nexys_hdmi $name] [file join $out source]
    add_files [file join $out source $name]
}
set_property include_dirs [list [file join $out source]] [get_filesets sources_1]
file copy [file join $root src/rtl/nexys_framebuffer/dp_framebuffer_source.vhd] [file join $out source]
read_vhdl -vhdl2008 [file join $out source/dp_framebuffer_source.vhd]
set_property XPM_LIBRARIES {XPM_CDC XPM_FIFO XPM_MEMORY} [current_project]
import_ip -files [file join $ip_root build/scripts/nexys/work/n3_vdma_frame_r3/project/n3_vdma_frame.srcs/sources_1/ip/n3_vdma/n3_vdma.xci]
generate_target simulation [get_ips n3_vdma]
add_files -fileset sim_1 [file join $out source/tb_sr_identity_core.sv]
set_property top tb_sr_identity_core [get_filesets sim_1]
set_property xsim.simulate.runtime all [get_filesets sim_1]
# Do not record every top-level pixel / AXI signal for millions of cycles.
# Assertions and progress logs remain active; this only disables wave capture.
set commands [file join $out source/assertions_only.tcl]
set f [open $commands w]
puts $f "run all"
close $f
set_property xsim.simulate.custom_tcl $commands [get_filesets sim_1]
set_property xsim.elaborate.debug_level off [get_filesets sim_1]
launch_simulation
close_sim
close_project
puts "N5_IDENTITY_SIMULATION_RETURNED_CHECK_LOG"
