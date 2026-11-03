# Loaded by the common BD builder only for the isolated HDMI configuration.
set_property -dict [list CONFIG.PCW_USE_S_AXI_HP1 {1} CONFIG.PCW_S_AXI_HP1_DATA_WIDTH {64}] $ps7
set video_dma [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_vdma:* hdmi_vdma]
set_property -dict [list CONFIG.c_include_s2mm {0} CONFIG.c_include_mm2s {1} \
    CONFIG.c_m_axi_mm2s_data_width {64} CONFIG.c_m_axis_mm2s_tdata_width {32} \
    CONFIG.c_num_fstores {3} CONFIG.c_include_mm2s_dre {1} \
    CONFIG.c_mm2s_genlock_mode {0} CONFIG.c_use_mm2s_fsync {0}] $video_dma
set hdmi_mode "720p"
set hdmi_pixel_mhz 74.250
set hdmi_serial_mhz 371.250
if {[info exists ::env(SR_LUT_HDMI_MODE)] && ($::env(SR_LUT_HDMI_MODE) eq "1080p30" || $::env(SR_LUT_HDMI_MODE) eq "1080p60")} {
  set hdmi_mode "1080p"
}
if {[info exists ::env(SR_LUT_HDMI_MODE)] && $::env(SR_LUT_HDMI_MODE) eq "1080p60"} {
  set hdmi_pixel_mhz 148.500
  set hdmi_serial_mhz 742.500
}
set video_timing [create_bd_cell -type ip -vlnv xilinx.com:ip:v_tc:* hdmi_vtc]
set_property -dict [list CONFIG.enable_detection {false} CONFIG.enable_generation {true} \
    CONFIG.HAS_AXI4_LITE {true} CONFIG.VIDEO_MODE $hdmi_mode] $video_timing
set video_out [create_bd_cell -type ip -vlnv xilinx.com:ip:v_axi4s_vid_out:* hdmi_video_out]
set_property -dict [list CONFIG.C_HAS_ASYNC_CLK {1} CONFIG.C_ADDR_WIDTH {11} \
    CONFIG.C_S_AXIS_VIDEO_FORMAT {2} CONFIG.C_S_AXIS_VIDEO_DATA_WIDTH {8} \
    CONFIG.C_VTG_MASTER_SLAVE {0}] $video_out
set video_rgb [create_bd_cell -type module -reference sr_lut_rgbx_video_adapter hdmi_rgbx]
set video_monitor [create_bd_cell -type module -reference sr_lut_hdmi_monitor hdmi_monitor]
set video_status [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:* hdmi_status]
set_property -dict [list CONFIG.C_GPIO_WIDTH {32} CONFIG.C_ALL_INPUTS {1} \
    CONFIG.C_IS_DUAL {1} CONFIG.C_GPIO2_WIDTH {32} CONFIG.C_ALL_INPUTS_2 {1}] $video_status
set video_clock [create_bd_cell -type ip -vlnv xilinx.com:ip:clk_wiz:* hdmi_clock]
set_property -dict [list CONFIG.PRIM_SOURCE {Global_buffer} CONFIG.PRIM_IN_FREQ {100.000} \
    CONFIG.CLKOUT1_REQUESTED_OUT_FREQ $hdmi_pixel_mhz CONFIG.CLKOUT2_USED {true} \
    CONFIG.CLKOUT2_REQUESTED_OUT_FREQ $hdmi_serial_mhz CONFIG.USE_RESET {true} \
    CONFIG.RESET_TYPE {ACTIVE_LOW}] $video_clock
set video_reset [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:* hdmi_reset]
set_property CONFIG.C_AUX_RESET_HIGH {0} $video_reset
set video_dvi [create_bd_cell -type ip -vlnv digilentinc.com:ip:rgb2dvi:* hdmi_rgb2dvi]
set_property -dict [list CONFIG.kGenerateSerialClk {false} CONFIG.kRstActiveHigh {false}] $video_dvi
set one [create_bd_cell -type inline_hdl -vlnv xilinx.com:inline_hdl:ilconstant:1.0 hdmi_one]
set zero [create_bd_cell -type inline_hdl -vlnv xilinx.com:inline_hdl:ilconstant:1.0 hdmi_zero]
set_property CONFIG.CONST_VAL {0} $zero
set video_mem [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:* hdmi_memory]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {1}] $video_mem

set old_masters [get_property CONFIG.NUM_MI $ctrl_smc]
set_property CONFIG.NUM_MI [expr {$old_masters + 3}] $ctrl_smc
foreach peripheral [list $video_dma $video_timing $video_status] iface {S_AXI_LITE ctrl S_AXI} {
  set master [format M%02d_AXI $old_masters]
  connect_bd_intf_net [get_bd_intf_pins $ctrl_smc/$master] [get_bd_intf_pins $peripheral/$iface]
  incr old_masters
}
connect_bd_intf_net [get_bd_intf_pins $video_dma/M_AXI_MM2S] [get_bd_intf_pins $video_mem/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins $video_mem/M00_AXI] [get_bd_intf_pins $ps7/S_AXI_HP1]
connect_bd_intf_net [get_bd_intf_pins $video_dma/M_AXIS_MM2S] [get_bd_intf_pins $video_rgb/S_AXIS]
connect_bd_intf_net [get_bd_intf_pins $video_rgb/M_AXIS] [get_bd_intf_pins $video_out/video_in]
connect_bd_intf_net [get_bd_intf_pins $video_timing/vtiming_out] [get_bd_intf_pins $video_out/vtiming_in]
foreach output_pin {vid_data vid_active_video vid_hsync vid_vsync} input_pin {vid_pData vid_pVDE vid_pHSync vid_pVSync} {
  connect_bd_net [get_bd_pins $video_out/$output_pin] [get_bd_pins $video_dvi/$input_pin]
}

foreach pin [list $video_clock/clk_in1 $video_dma/s_axi_lite_aclk \
    $video_timing/s_axi_aclk $video_status/s_axi_aclk $video_monitor/aclk] {
  connect_bd_net $fclk [get_bd_pins $pin]
}
# The qualified 1080p30 design shares the 100 MHz fabric clock. Only the
# 1080p60 experiment needs FCLK1 (150 MHz requested, normally 142.857 MHz).
set video_mem_clk $fclk
if {$hdmi_fast_stream} {
  set video_mem_clk [get_bd_pins $ps7/FCLK_CLK1]
}
connect_bd_net $video_mem_clk \
    [get_bd_pins $video_dma/m_axi_mm2s_aclk] \
    [get_bd_pins $video_dma/m_axis_mm2s_aclk] \
    [get_bd_pins $video_mem/aclk] \
    [get_bd_pins $ps7/S_AXI_HP1_ACLK] \
    [get_bd_pins $video_rgb/aclk] \
    [get_bd_pins $video_out/aclk]
foreach pin [list $video_clock/resetn $video_dma/axi_resetn $video_timing/s_axi_aresetn \
    $video_out/aresetn $video_rgb/aresetn $video_mem/aresetn $video_reset/ext_reset_in \
    $video_status/s_axi_aresetn $video_monitor/aresetn] {
  connect_bd_net $resetn [get_bd_pins $pin]
}
set pixel_clock [get_bd_pins $video_clock/clk_out1]
foreach pin [list $video_timing/clk $video_out/vid_io_out_clk $video_dvi/PixelClk $video_reset/slowest_sync_clk] {
  connect_bd_net $pixel_clock [get_bd_pins $pin]
}
connect_bd_net [get_bd_pins $video_clock/clk_out2] [get_bd_pins $video_dvi/SerialClk]
connect_bd_net [get_bd_pins $video_clock/locked] [get_bd_pins $video_reset/dcm_locked]
connect_bd_net [get_bd_pins $video_reset/peripheral_aresetn] [get_bd_pins $video_timing/resetn] [get_bd_pins $video_dvi/aRst_n]
connect_bd_net [get_bd_pins $video_reset/peripheral_reset] [get_bd_pins $video_out/vid_io_out_reset]
connect_bd_net [get_bd_pins $video_out/vtg_ce] [get_bd_pins $video_timing/gen_clken]
foreach pin [list $video_timing/clken $video_out/aclken $video_out/vid_io_out_ce $video_timing/s_axi_aclken] {
  connect_bd_net [get_bd_pins $one/dout] [get_bd_pins $pin]
}
# Auxiliary reset is active low, independently of the debug reset polarity.
connect_bd_net [get_bd_pins $one/dout] [get_bd_pins $video_reset/aux_reset_in]
connect_bd_net [get_bd_pins $zero/dout] [get_bd_pins $video_reset/mb_debug_sys_rst]
connect_bd_net [get_bd_pins $video_out/vid_vsync] [get_bd_pins $video_dvi/vid_pVSync] [get_bd_pins $video_monitor/video_vsync_async]
foreach signal {locked underflow overflow} {
  connect_bd_net [get_bd_pins $video_out/$signal] [get_bd_pins $video_monitor/video_$signal]
}
connect_bd_net [get_bd_pins $video_monitor/scan_frames] [get_bd_pins $video_status/gpio_io_i]
connect_bd_net [get_bd_pins $video_monitor/errors] [get_bd_pins $video_status/gpio2_io_i]
set_property CONFIG.NUM_PORTS {3} $irq_concat
connect_bd_net [get_bd_pins $video_dma/mm2s_introut] [get_bd_pins $irq_concat/In2]

foreach suffix {p n} {
  create_bd_port -dir O hdmi_out_clk_$suffix
  connect_bd_net [get_bd_ports hdmi_out_clk_$suffix] [get_bd_pins $video_dvi/TMDS_Clk_$suffix]
}
foreach suffix {p n} {
  create_bd_port -dir O -from 2 -to 0 hdmi_out_data_$suffix
  connect_bd_net [get_bd_ports hdmi_out_data_$suffix] [get_bd_pins $video_dvi/TMDS_Data_$suffix]
}
