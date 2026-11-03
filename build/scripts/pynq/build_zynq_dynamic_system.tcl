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
set scheduler_file [dict get $stage_config scheduler_file]
set wrapper_variant [dict get $stage_config wrapper_variant]
set s2mm_port [dict get $stage_config s2mm_port]
set mode_gpio_enabled [dict get $stage_config mode_gpio_enabled]
set scale_gpio_enabled [dict get $stage_config scale_gpio_enabled]
set scale [dict get $stage_config scale]
set sample_bits [dict get $stage_config sample_bits]
set q [dict get $stage_config q]
set levels [dict get $stage_config levels]
set lut_depth [dict get $stage_config lut_depth]
set addr_bits [dict get $stage_config addr_bits]
set scheduler_cores [dict get $stage_config scheduler_cores]
set project_name sr_lut_s${stage_number}_dyn
set design_name $project_name

set part_name xc7z020clg400-1
set board_part tul.com.tw:pynq-z2:part0:1.0
set lut_mem [sr_lut_resolve_path $project_root [dict get $stage_config lut_mem_rel]]

set rtl_files [list \
    [file join $project_root src/rtl sr_lut_pkg.sv] \
    [file join $project_root src/rtl sr_lut_quantizer.sv] \
    [file join $project_root src/rtl sr_lut_simplex_selector.sv] \
    [file join $project_root src/rtl sr_lut_address_gen.sv] \
    [file join $project_root src/rtl sr_lut_rom.sv] \
    [file join $project_root src/rtl sr_lut_interpolator.sv] \
    [file join $project_root src/rtl sr_lut_line_buffer_dynamic.sv] \
    [file join $project_root src/rtl sr_lut_rotation_map.sv] \
    [file join $project_root src/rtl sr_lut_result_pipeline.sv] \
    [file join $project_root src/rtl sr_lut_task_pipeline.sv] \
    $scheduler_file \
    [file join $project_root src/rtl sr_lut_fusion.sv] \
    [file join $project_root src/rtl sr_lut_block_fifo.sv] \
    [file join $project_root src/rtl sr_lut_output_fifo_dynamic.sv] \
    [file join $project_root src/rtl sr_lut_axis_top_dynamic.sv] \
    [file join $project_root src/rtl sr_lut_perf_monitor.sv] \
]

if {$wrapper_variant eq "video420"} {
  lappend rtl_files \
      [file join $project_root src/rtl sr_lut_crc32_word.sv] \
      [file join $project_root src/rtl sr_lut_video420_packer.sv] \
      [file join $project_root src/rtl sr_lut_axis_wrapper_video.v]
  set wrapper_module sr_lut_axis_wrapper_video
} elseif {$wrapper_variant eq "mjpeg"} {
  lappend rtl_files \
      [file join $project_root src/rtl sr_lut_axis_wrapper_dynamic.v] \
      [file join $project_root src/rtl sr_lut_rgb_to_yuyv_axis.v] \
      [file join $project_root src/rtl sr_lut_axis_async_fifo_16.v] \
      [file join $project_root src/rtl sr_lut_mjpeg_axis_wrapper.v] \
      [file join $project_root src/rtl sr_lut_jpeg_packer_async.v] \
      [file join $project_root src/rtl sr_lut_mjpeg_status.v] \
      [file join $project_root src/third_party mjpegzero rtl bram_sdp.v] \
      [file join $project_root src/third_party mjpegzero rtl dct_1d.v] \
      [file join $project_root src/third_party mjpegzero rtl dct_2d.v] \
      [file join $project_root src/third_party mjpegzero rtl input_buffer.v] \
      [file join $project_root src/third_party mjpegzero rtl quantizer.v] \
      [file join $project_root src/third_party mjpegzero rtl zigzag_reorder.v] \
      [file join $project_root src/third_party mjpegzero rtl huffman_encoder.v] \
      [file join $project_root src/third_party mjpegzero rtl bitstream_packer.v] \
      [file join $project_root src/third_party mjpegzero rtl jfif_writer.v] \
      [file join $project_root src/third_party mjpegzero rtl axi4_lite_regs.v] \
      [file join $project_root src/third_party mjpegzero rtl mjpegzero_enc_top.v] \
      [file join $project_root src/third_party mjpegzero rtl rgb_to_ycbcr.v]
  set wrapper_module sr_lut_axis_wrapper_dynamic
} else {
  lappend rtl_files \
      [file join $project_root src/rtl sr_lut_axis_wrapper_dynamic.v]
  set wrapper_module sr_lut_axis_wrapper_dynamic
}

foreach required_file [concat $rtl_files [list $lut_mem]] {
  if {![file isfile $required_file]} {
    error "Required Stage $stage_number input is missing: $required_file"
  }
}

file mkdir $build_dir
file mkdir $results_dir
cd $build_dir

create_project -force $project_name $build_dir -part $part_name
if {[info exists ::env(SR_LUT_IP_CACHE)] && $::env(SR_LUT_IP_CACHE) ne ""} {
  # Optional vendor-managed cache: only matching IP checksums are reused.
  # Full top-level synthesis, placement, routing and timing checks still run.
  set shared_ip_cache [file normalize $::env(SR_LUT_IP_CACHE)]
  if {![file isdirectory $shared_ip_cache]} {error "IP cache directory does not exist"}
  config_ip_cache -use_cache_location $shared_ip_cache
}
set_property board_part $board_part [current_project]
set_property target_language Verilog [current_project]
set_property simulator_language Mixed [current_project]

set hdmi_enabled [expr {[info exists ::env(SR_LUT_HDMI)] && $::env(SR_LUT_HDMI) eq "1"}]
set hdmi_fast_stream [expr {$hdmi_enabled && [info exists ::env(SR_LUT_HDMI_MODE)] && $::env(SR_LUT_HDMI_MODE) eq "1080p60"}]
if {$hdmi_enabled} {
  if {$wrapper_variant ne "dynamic" || $s2mm_port ne "acp"} {
    error "HDMI requires the RGB dynamic wrapper and ACP SR output"
  }
  set_property ip_repo_paths [list [file join $project_root src/third_party/digilent_video]] [current_project]
  update_ip_catalog
  add_files -norecurse [list \
      [file join $project_root src/rtl/sr_lut_rgbx_video_adapter.v] \
      [file join $project_root src/rtl/sr_lut_hdmi_monitor.v]]
  add_files -fileset constrs_1 -norecurse [file join $script_dir pynq_z2_hdmi_out.xdc]
}

if {$mode_gpio_enabled || $scale_gpio_enabled} {
  set front_panel_xdc [file join $script_dir pynq_z2_front_panel_gpio.xdc]
  if {![file isfile $front_panel_xdc]} {
    error "PYNQ-Z2 front-panel GPIO constraints are missing: $front_panel_xdc"
  }
  add_files -fileset constrs_1 -norecurse $front_panel_xdc
  set_property USED_IN_SYNTHESIS false [get_files $front_panel_xdc]
}

add_files -norecurse $rtl_files
add_files -norecurse $lut_mem
set_property file_type {Memory Initialization Files} [get_files $lut_mem]
update_compile_order -fileset sources_1

create_bd_design $design_name

set ps7 [create_bd_cell -type ip \
    -vlnv xilinx.com:ip:processing_system7:* processing_system7_0]
apply_bd_automation -rule xilinx.com:bd_rule:processing_system7 \
    -config {apply_board_preset "1" make_external "FIXED_IO, DDR"} $ps7
set ps_properties [list \
    CONFIG.PCW_USE_M_AXI_GP0 {1} \
    CONFIG.PCW_USE_S_AXI_HP0 {1} \
    CONFIG.PCW_EN_CLK0_PORT {1} \
    CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ {100.000000} \
    CONFIG.PCW_S_AXI_HP0_DATA_WIDTH {64} \
    CONFIG.PCW_USE_FABRIC_INTERRUPT {1} \
    CONFIG.PCW_IRQ_F2P_INTR {1} \
]
if {$hdmi_fast_stream} {
  # 1080p60 scanout reads nearly 500 MB/s of RGBX data.  Keep the SR/control
  # fabric at 100 MHz, but give the HDMI HP1 and pixel-stream path a separate
  # high-speed fabric clock so 1080p60 is not forced through a 100 MHz stream.
  lappend ps_properties \
      CONFIG.PCW_EN_CLK1_PORT {1} \
      CONFIG.PCW_FPGA1_PERIPHERAL_FREQMHZ {150.000000}
}
if {$s2mm_port eq "acp"} {
  lappend ps_properties \
      CONFIG.PCW_USE_S_AXI_ACP {1} \
      CONFIG.PCW_USE_DEFAULT_ACP_USER_VAL {1} \
      CONFIG.PCW_S_AXI_ACP_ARUSER_VAL {31} \
      CONFIG.PCW_S_AXI_ACP_AWUSER_VAL {31}
} elseif {$s2mm_port eq "hp1"} {
  lappend ps_properties \
      CONFIG.PCW_USE_S_AXI_HP1 {1} \
      CONFIG.PCW_S_AXI_HP1_DATA_WIDTH {64}
}
set_property -dict $ps_properties $ps7

set dma [create_bd_cell -type ip \
    -vlnv xilinx.com:ip:axi_dma:* axi_dma_0]
set_property -dict [list \
    CONFIG.c_include_sg {0} \
    CONFIG.c_include_mm2s {1} \
    CONFIG.c_include_s2mm {1} \
    CONFIG.c_include_mm2s_dre {1} \
    CONFIG.c_include_s2mm_dre {1} \
    CONFIG.c_m_axis_mm2s_tdata_width {32} \
    CONFIG.c_s_axis_s2mm_tdata_width {32} \
    CONFIG.c_sg_length_width {26} \
] $dma

set sr_lut [create_bd_cell -type module \
    -reference $wrapper_module sr_lut_scale_dynamic_0]
set_property -dict [list \
    CONFIG.SCALE $scale \
    CONFIG.SAMPLE_BITS $sample_bits \
    CONFIG.Q $q \
    CONFIG.LEVELS $levels \
    CONFIG.LUT_DEPTH $lut_depth \
    CONFIG.ADDR_BITS $addr_bits \
    CONFIG.MAX_FRAME_WIDTH {480} \
    CONFIG.MAX_FRAME_HEIGHT {320} \
    CONFIG.MIN_FRAME_WIDTH {32} \
    CONFIG.MIN_FRAME_HEIGHT {32} \
    CONFIG.SCHEDULER_CORES $scheduler_cores \
    CONFIG.MEM_FILE $lut_mem \
] $sr_lut

if {$wrapper_variant eq "mjpeg"} {
  set rgb_to_yuyv [create_bd_cell -type module \
      -reference sr_lut_rgb_to_yuyv_axis rgb_to_yuyv_0]
  set_property CONFIG.OUTPUT_WIDTH {1920} $rgb_to_yuyv

  set yuyv_cdc [create_bd_cell -type module \
      -reference sr_lut_axis_async_fifo_16 yuyv_cdc_0]
  set_property CONFIG.FIFO_DEPTH {64} $yuyv_cdc

  set mjpeg [create_bd_cell -type module \
      -reference sr_lut_mjpeg_axis_wrapper mjpeg_encoder_0]
  set_property -dict [list \
      CONFIG.IMG_WIDTH {1920} \
      CONFIG.IMG_HEIGHT {1080} \
      CONFIG.QUALITY {80} \
  ] $mjpeg

  set jpeg_packer [create_bd_cell -type module \
      -reference sr_lut_jpeg_packer_async jpeg_packer_0]
  set_property CONFIG.FIFO_DEPTH_WORDS {256} $jpeg_packer

  set mjpeg_status [create_bd_cell -type module \
      -reference sr_lut_mjpeg_status mjpeg_status_0]
}

set rst [create_bd_cell -type ip \
    -vlnv xilinx.com:ip:proc_sys_reset:* rst_ps7_0_100M]
if {$wrapper_variant eq "mjpeg"} {
  set jpeg_clock [create_bd_cell -type ip \
      -vlnv xilinx.com:ip:clk_wiz:* jpeg_clock_150M]
  set_property -dict [list \
      CONFIG.PRIM_SOURCE {Global_buffer} \
      CONFIG.PRIM_IN_FREQ {100.000} \
      CONFIG.CLKOUT1_REQUESTED_OUT_FREQ {150.000} \
      CONFIG.USE_LOCKED {true} \
      CONFIG.USE_RESET {true} \
      CONFIG.RESET_TYPE {ACTIVE_LOW} \
  ] $jpeg_clock
  set rst_150 [create_bd_cell -type ip \
      -vlnv xilinx.com:ip:proc_sys_reset:* rst_ps7_1_150M]
}

set ctrl_smc [create_bd_cell -type ip \
    -vlnv xilinx.com:ip:smartconnect:* axi_ctrl_smc]
set ctrl_num_mi [expr {$wrapper_variant eq "mjpeg" ? 5 : 4}]
if {$mode_gpio_enabled} {
  incr ctrl_num_mi
}
if {$scale_gpio_enabled} {
  incr ctrl_num_mi
}
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI $ctrl_num_mi] $ctrl_smc

set perf_gpio_0 [create_bd_cell -type ip \
    -vlnv xilinx.com:ip:axi_gpio:* axi_gpio_perf_0]
set_property -dict [list \
    CONFIG.C_GPIO_WIDTH {32} \
    CONFIG.C_ALL_INPUTS {1} \
    CONFIG.C_IS_DUAL {1} \
    CONFIG.C_GPIO2_WIDTH {32} \
    CONFIG.C_ALL_INPUTS_2 {1} \
] $perf_gpio_0

set perf_gpio_1 [create_bd_cell -type ip \
    -vlnv xilinx.com:ip:axi_gpio:* axi_gpio_perf_1]
set_property -dict [list \
    CONFIG.C_GPIO_WIDTH {32} \
    CONFIG.C_ALL_INPUTS {1} \
    CONFIG.C_IS_DUAL {1} \
    CONFIG.C_GPIO2_WIDTH {32} \
    CONFIG.C_ALL_INPUTS_2 {1} \
] $perf_gpio_1

if {$wrapper_variant eq "mjpeg"} {
  set mjpeg_gpio [create_bd_cell -type ip \
      -vlnv xilinx.com:ip:axi_gpio:* axi_gpio_mjpeg_status]
  set_property -dict [list \
      CONFIG.C_GPIO_WIDTH {32} \
      CONFIG.C_ALL_INPUTS {1} \
  ] $mjpeg_gpio
}

if {$mode_gpio_enabled} {
  set mode_gpio [create_bd_cell -type ip \
      -vlnv xilinx.com:ip:axi_gpio:* axi_gpio_mode_0]
  set_property -dict [list \
      CONFIG.C_GPIO_WIDTH {4} \
      CONFIG.C_ALL_INPUTS {1} \
      CONFIG.C_IS_DUAL {1} \
      CONFIG.C_GPIO2_WIDTH {4} \
      CONFIG.C_ALL_OUTPUTS_2 {1} \
  ] $mode_gpio
  set mode_buttons [create_bd_port -dir I -from 3 -to 0 btns_4bits]
  set mode_leds [create_bd_port -dir O -from 3 -to 0 leds_4bits]
  connect_bd_net $mode_buttons [get_bd_pins $mode_gpio/gpio_io_i]
  connect_bd_net $mode_leds [get_bd_pins $mode_gpio/gpio2_io_o]
}

if {$scale_gpio_enabled} {
  set scale_gpio [create_bd_cell -type ip \
      -vlnv xilinx.com:ip:axi_gpio:* axi_gpio_scale_0]
  set_property -dict [list \
      CONFIG.C_GPIO_WIDTH {2} \
      CONFIG.C_ALL_INPUTS {1} \
  ] $scale_gpio
  set scale_switches [create_bd_port -dir I -from 1 -to 0 sws_2bits]
  connect_bd_net $scale_switches [get_bd_pins $scale_gpio/gpio_io_i]
}

set mm2s_smc [create_bd_cell -type ip \
    -vlnv xilinx.com:ip:smartconnect:* axi_mm2s_smc]
set mm2s_num_si [expr {$s2mm_port eq "hp0" ? 2 : 1}]
set_property -dict [list CONFIG.NUM_SI $mm2s_num_si CONFIG.NUM_MI {1}] $mm2s_smc

if {$s2mm_port eq "acp"} {
  set s2mm_smc [create_bd_cell -type ip \
      -vlnv xilinx.com:ip:smartconnect:* axi_s2mm_acp_smc]
  set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {1}] $s2mm_smc
} elseif {$s2mm_port eq "hp1"} {
  set s2mm_smc [create_bd_cell -type ip \
      -vlnv xilinx.com:ip:smartconnect:* axi_s2mm_hp1_smc]
  set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {1}] $s2mm_smc
}

set irq_concat [create_bd_cell -type inline_hdl \
    -vlnv xilinx.com:inline_hdl:ilconcat:1.0 irq_concat]
set_property CONFIG.NUM_PORTS {2} $irq_concat

connect_bd_intf_net [get_bd_intf_pins $dma/M_AXIS_MM2S] \
    [get_bd_intf_pins $sr_lut/S_AXIS]
if {$wrapper_variant eq "mjpeg"} {
  connect_bd_intf_net [get_bd_intf_pins $sr_lut/M_AXIS] \
      [get_bd_intf_pins $rgb_to_yuyv/S_AXIS]
  connect_bd_intf_net [get_bd_intf_pins $rgb_to_yuyv/M_AXIS] \
      [get_bd_intf_pins $yuyv_cdc/S_AXIS]
  connect_bd_intf_net [get_bd_intf_pins $yuyv_cdc/M_AXIS] \
      [get_bd_intf_pins $mjpeg/S_AXIS]
  connect_bd_intf_net [get_bd_intf_pins $mjpeg/M_AXIS] \
      [get_bd_intf_pins $jpeg_packer/S_AXIS]
  connect_bd_intf_net [get_bd_intf_pins $jpeg_packer/M_AXIS] \
      [get_bd_intf_pins $dma/S_AXIS_S2MM]
} else {
  connect_bd_intf_net [get_bd_intf_pins $sr_lut/M_AXIS] \
      [get_bd_intf_pins $dma/S_AXIS_S2MM]
}

connect_bd_intf_net [get_bd_intf_pins $ps7/M_AXI_GP0] \
    [get_bd_intf_pins $ctrl_smc/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins $ctrl_smc/M00_AXI] \
    [get_bd_intf_pins $dma/S_AXI_LITE]
connect_bd_intf_net [get_bd_intf_pins $ctrl_smc/M01_AXI] \
    [get_bd_intf_pins $perf_gpio_0/S_AXI]
connect_bd_intf_net [get_bd_intf_pins $ctrl_smc/M02_AXI] \
    [get_bd_intf_pins $perf_gpio_1/S_AXI]
connect_bd_intf_net [get_bd_intf_pins $ctrl_smc/M03_AXI] \
    [get_bd_intf_pins $sr_lut/S_AXI_CTRL]
set next_ctrl_mi [expr {$wrapper_variant eq "mjpeg" ? 5 : 4}]
if {$wrapper_variant eq "mjpeg"} {
  connect_bd_intf_net [get_bd_intf_pins $ctrl_smc/M04_AXI] \
      [get_bd_intf_pins $mjpeg_gpio/S_AXI]
  if {$mode_gpio_enabled} {
    connect_bd_intf_net [get_bd_intf_pins $ctrl_smc/M0${next_ctrl_mi}_AXI] \
        [get_bd_intf_pins $mode_gpio/S_AXI]
    incr next_ctrl_mi
  }
} elseif {$mode_gpio_enabled} {
  connect_bd_intf_net [get_bd_intf_pins $ctrl_smc/M0${next_ctrl_mi}_AXI] \
      [get_bd_intf_pins $mode_gpio/S_AXI]
  incr next_ctrl_mi
}
if {$scale_gpio_enabled} {
  connect_bd_intf_net [get_bd_intf_pins $ctrl_smc/M0${next_ctrl_mi}_AXI] \
      [get_bd_intf_pins $scale_gpio/S_AXI]
}

connect_bd_net [get_bd_pins $sr_lut/perf_frame_cycles] \
    [get_bd_pins $perf_gpio_0/gpio_io_i]
connect_bd_net [get_bd_pins $sr_lut/perf_input_backpressure_cycles] \
    [get_bd_pins $perf_gpio_0/gpio2_io_i]
connect_bd_net [get_bd_pins $sr_lut/perf_output_backpressure_cycles] \
    [get_bd_pins $perf_gpio_1/gpio_io_i]
connect_bd_net [get_bd_pins $sr_lut/perf_input_starvation_cycles] \
    [get_bd_pins $perf_gpio_1/gpio2_io_i]
if {$wrapper_variant eq "mjpeg"} {
  connect_bd_net [get_bd_pins $rgb_to_yuyv/frame_geometry_error] \
      [get_bd_pins $mjpeg_status/frame_geometry_error]
  connect_bd_net [get_bd_pins $mjpeg/jpeg_overflow] \
      [get_bd_pins $mjpeg_status/encoder_overflow]
  connect_bd_net [get_bd_pins $jpeg_packer/jpeg_overflow] \
      [get_bd_pins $mjpeg_status/packer_overflow]
  connect_bd_net [get_bd_pins $jpeg_packer/jpeg_byte_count] \
      [get_bd_pins $mjpeg_status/jpeg_byte_count]
  connect_bd_net [get_bd_pins $mjpeg_status/status] \
      [get_bd_pins $mjpeg_gpio/gpio_io_i]
}

connect_bd_intf_net [get_bd_intf_pins $dma/M_AXI_MM2S] \
    [get_bd_intf_pins $mm2s_smc/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins $mm2s_smc/M00_AXI] \
    [get_bd_intf_pins $ps7/S_AXI_HP0]
if {$s2mm_port eq "acp"} {
  connect_bd_intf_net [get_bd_intf_pins $dma/M_AXI_S2MM] \
      [get_bd_intf_pins $s2mm_smc/S00_AXI]
  connect_bd_intf_net [get_bd_intf_pins $s2mm_smc/M00_AXI] \
      [get_bd_intf_pins $ps7/S_AXI_ACP]
} elseif {$s2mm_port eq "hp1"} {
  connect_bd_intf_net [get_bd_intf_pins $dma/M_AXI_S2MM] \
      [get_bd_intf_pins $s2mm_smc/S00_AXI]
  connect_bd_intf_net [get_bd_intf_pins $s2mm_smc/M00_AXI] \
      [get_bd_intf_pins $ps7/S_AXI_HP1]
} else {
  connect_bd_intf_net [get_bd_intf_pins $dma/M_AXI_S2MM] \
      [get_bd_intf_pins $mm2s_smc/S01_AXI]
}

set fclk [get_bd_pins $ps7/FCLK_CLK0]
set resetn [get_bd_pins $rst/peripheral_aresetn]
connect_bd_net $fclk [get_bd_pins $rst/slowest_sync_clk]
connect_bd_net [get_bd_pins $ps7/FCLK_RESET0_N] \
    [get_bd_pins $rst/ext_reset_in]
if {$wrapper_variant eq "mjpeg"} {
  set jpeg_clk [get_bd_pins $jpeg_clock/clk_out1]
  set jpeg_resetn [get_bd_pins $rst_150/peripheral_aresetn]
  connect_bd_net $fclk [get_bd_pins $jpeg_clock/clk_in1]
  connect_bd_net [get_bd_pins $ps7/FCLK_RESET0_N] \
      [get_bd_pins $jpeg_clock/resetn]
  connect_bd_net $jpeg_clk [get_bd_pins $rst_150/slowest_sync_clk]
  connect_bd_net [get_bd_pins $ps7/FCLK_RESET0_N] \
      [get_bd_pins $rst_150/ext_reset_in]
  connect_bd_net [get_bd_pins $jpeg_clock/locked] \
      [get_bd_pins $rst_150/dcm_locked]
}
connect_bd_net $fclk \
    [get_bd_pins $ps7/M_AXI_GP0_ACLK] \
    [get_bd_pins $ps7/S_AXI_HP0_ACLK] \
    [get_bd_pins $dma/s_axi_lite_aclk] \
    [get_bd_pins $dma/m_axi_mm2s_aclk] \
    [get_bd_pins $dma/m_axi_s2mm_aclk] \
    [get_bd_pins $perf_gpio_0/s_axi_aclk] \
    [get_bd_pins $perf_gpio_1/s_axi_aclk] \
    [get_bd_pins $ctrl_smc/aclk] \
    [get_bd_pins $mm2s_smc/aclk] \
    [get_bd_pins $sr_lut/aclk]
if {$mode_gpio_enabled} {
  connect_bd_net $fclk [get_bd_pins $mode_gpio/s_axi_aclk]
}
if {$scale_gpio_enabled} {
  connect_bd_net $fclk [get_bd_pins $scale_gpio/s_axi_aclk]
}
if {$wrapper_variant eq "mjpeg"} {
  connect_bd_net $fclk \
      [get_bd_pins $mjpeg_gpio/s_axi_aclk] \
      [get_bd_pins $rgb_to_yuyv/aclk] \
      [get_bd_pins $yuyv_cdc/s_clk] \
      [get_bd_pins $jpeg_packer/m_clk]
  connect_bd_net $jpeg_clk \
      [get_bd_pins $yuyv_cdc/m_clk] \
      [get_bd_pins $mjpeg/aclk] \
      [get_bd_pins $jpeg_packer/s_clk]
}
if {$s2mm_port eq "acp"} {
  connect_bd_net $fclk \
      [get_bd_pins $ps7/S_AXI_ACP_ACLK] \
      [get_bd_pins $s2mm_smc/aclk]
} elseif {$s2mm_port eq "hp1"} {
  connect_bd_net $fclk \
      [get_bd_pins $ps7/S_AXI_HP1_ACLK] \
      [get_bd_pins $s2mm_smc/aclk]
}
connect_bd_net $resetn \
    [get_bd_pins $dma/axi_resetn] \
    [get_bd_pins $perf_gpio_0/s_axi_aresetn] \
    [get_bd_pins $perf_gpio_1/s_axi_aresetn] \
    [get_bd_pins $ctrl_smc/aresetn] \
    [get_bd_pins $mm2s_smc/aresetn] \
    [get_bd_pins $sr_lut/aresetn]
if {$mode_gpio_enabled} {
  connect_bd_net $resetn [get_bd_pins $mode_gpio/s_axi_aresetn]
}
if {$scale_gpio_enabled} {
  connect_bd_net $resetn [get_bd_pins $scale_gpio/s_axi_aresetn]
}
if {$wrapper_variant eq "mjpeg"} {
  connect_bd_net $resetn \
      [get_bd_pins $mjpeg_gpio/s_axi_aresetn] \
      [get_bd_pins $rgb_to_yuyv/aresetn] \
      [get_bd_pins $yuyv_cdc/s_resetn] \
      [get_bd_pins $jpeg_packer/m_resetn]
  connect_bd_net $jpeg_resetn \
      [get_bd_pins $yuyv_cdc/m_resetn] \
      [get_bd_pins $mjpeg/aresetn] \
      [get_bd_pins $jpeg_packer/s_resetn]
}
if {$s2mm_port in {acp hp1}} {
  connect_bd_net $resetn [get_bd_pins $s2mm_smc/aresetn]
}

connect_bd_net [get_bd_pins $dma/mm2s_introut] \
    [get_bd_pins $irq_concat/In0]
connect_bd_net [get_bd_pins $dma/s2mm_introut] \
    [get_bd_pins $irq_concat/In1]
connect_bd_net [get_bd_pins $irq_concat/dout] \
    [get_bd_pins $ps7/IRQ_F2P]

if {$hdmi_enabled} {
  source [file join $script_dir add_hdmi_output.tcl]
}

assign_bd_address

# Keep the software-visible register map stable as optional peripherals are
# added.  The performance counters retain their stable addresses; the two
# camera/mode blocks use the next available 64 KiB windows.
set ps_data_space [get_bd_addr_spaces $ps7/Data]
assign_bd_address -offset 0x41200000 -range 0x00010000 \
    -target_address_space $ps_data_space \
    [get_bd_addr_segs $perf_gpio_0/S_AXI/Reg] -force
assign_bd_address -offset 0x41210000 -range 0x00010000 \
    -target_address_space $ps_data_space \
    [get_bd_addr_segs $perf_gpio_1/S_AXI/Reg] -force
if {$wrapper_variant eq "mjpeg"} {
  assign_bd_address -offset 0x41220000 -range 0x00010000 \
      -target_address_space $ps_data_space \
      [get_bd_addr_segs $mjpeg_gpio/S_AXI/Reg] -force
}
if {$mode_gpio_enabled} {
  assign_bd_address -offset 0x41230000 -range 0x00010000 \
      -target_address_space $ps_data_space \
      [get_bd_addr_segs $mode_gpio/S_AXI/Reg] -force
}
if {$scale_gpio_enabled} {
  assign_bd_address -offset 0x41240000 -range 0x00010000 \
      -target_address_space $ps_data_space \
      [get_bd_addr_segs $scale_gpio/S_AXI/Reg] -force
}
if {$hdmi_enabled} {
  assign_bd_address -offset 0x41250000 -range 0x00010000 \
      -target_address_space $ps_data_space [get_bd_addr_segs $video_status/S_AXI/Reg] -force
  assign_bd_address -offset 0x43010000 -range 0x00010000 \
      -target_address_space $ps_data_space [get_bd_addr_segs $video_dma/S_AXI_LITE/Reg] -force
  assign_bd_address -offset 0x43C10000 -range 0x00010000 \
      -target_address_space $ps_data_space [get_bd_addr_segs $video_timing/ctrl/Reg] -force
}
validate_bd_design
if {$hdmi_enabled} {
  if {[get_property CONFIG.C_AUX_RESET_HIGH $video_reset] != 0 ||
      [get_bd_nets -of_objects [get_bd_pins $video_reset/aux_reset_in]] ne
      [get_bd_nets -of_objects [get_bd_pins $one/dout]]} {
    error "HDMI active-low auxiliary reset must be tied high after BD propagation"
  }
}
save_bd_design

set bd_file [get_files [file join $build_dir $project_name.srcs \
    sources_1 bd $design_name ${design_name}.bd]]
generate_target all $bd_file
make_wrapper -files $bd_file -top
set wrapper_file [file join $build_dir $project_name.gen sources_1 bd \
    $design_name hdl ${design_name}_wrapper.v]
add_files -norecurse $wrapper_file
set_property top ${design_name}_wrapper [current_fileset]
update_compile_order -fileset sources_1

report_ip_status -file [file join $results_dir stage_${stage_number}g_scale${scale}_ip_status.rpt]
write_bd_tcl -force [file join $results_dir stage_${stage_number}h_scale${scale}_recreated_bd.tcl]

puts "${stage_marker}_SCALE${scale}_OPTIMIZED_BD_VALIDATE_PASS"
puts "${stage_marker}_X${scale}_OPTIMIZED_PROJECT $build_dir"
puts "${stage_marker}_X${scale}_WRAPPER_VARIANT $wrapper_variant"
puts "${stage_marker}_X${scale}_S2MM_PORT [string toupper $s2mm_port]"
