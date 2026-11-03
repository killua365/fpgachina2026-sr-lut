# Applied after synthesis; all unlisted crossings remain visible in CDC reports.
# These are asynchronous level synchronizers, not video/data timing waivers.
# Every endpoint must match exactly; new crossings remain timed and visible.
proc require_cell {name} {
    set matches [get_cells -quiet [list $name]]
    if {[llength $matches] != 1} {error "Expected exactly one CDC cell: $name"}
    return $matches
}
set synchronizers {
    {Inst_main_stream_processing/i_insert_training_pattern/align_train_meta_reg Inst_main_stream_processing/i_insert_training_pattern/align_train_i_reg}
    {Inst_main_stream_processing/i_insert_training_pattern/clock_train_meta_reg Inst_main_stream_processing/i_insert_training_pattern/clock_train_i_reg}
    {i_tx0/g_tx[0].i_gtx_tx_reset_controller/txresetdone_meta_reg i_tx0/g_tx[0].i_gtx_tx_reset_controller/txresetdone_i_reg}
    {i_channel_management/i_aux_channel/i_channel/rx_meta_reg i_channel_management/i_aux_channel/i_channel/rx_synced_reg}
}
if {[info exists ser_mode] && $ser_mode} {
    lappend synchronizers {Inst_main_stream_processing/g_ser.ser_arm_meta_reg Inst_main_stream_processing/g_ser.ser_arm_sync_reg}
    lappend synchronizers {i_channel_management/i_aux_channel/ser_ready_meta_reg i_channel_management/i_aux_channel/ser_ready_sync_reg}
} else {
    lappend synchronizers {Inst_main_stream_processing/i_idle_pattern_inserter/channel_ready_meta_reg Inst_main_stream_processing/i_idle_pattern_inserter/channel_ready_i_reg}
}
if {[info exists variant] && $variant in {bridge_control dp1080}} {
    lappend synchronizers {i_tx0/i_ref_monitor/toggle_meta_reg i_tx0/i_ref_monitor/toggle_sync_reg}
} else {
    lappend synchronizers {i_tx0/g_tx[0].i_gtx_tx_reset_controller/ref_clk_detect_meta_reg i_tx0/g_tx[0].i_gtx_tx_reset_controller/ref_clk_detect_reg}
}
if {[info exists variant] && $variant eq "dp1080"} {
    lappend synchronizers {i_tx0/g_tx[1].i_gtx_tx_reset_controller/txresetdone_meta_reg i_tx0/g_tx[1].i_gtx_tx_reset_controller/txresetdone_i_reg}
}
if {[info exists gtp_health] && $gtp_health} {
    foreach bit {0 1 2} {
        lappend synchronizers [list [format {g_health.i_gtp_health/state_meta_reg[%d]} $bit] [format {g_health.i_gtp_health/state_sync_reg[%d]} $bit]]
    }
}
foreach pair $synchronizers {
    set first [require_cell [lindex $pair 0]]
    set second [require_cell [lindex $pair 1]]
    set_property ASYNC_REG TRUE [concat $first $second]
    set_property SHREG_EXTRACT NO [concat $first $second]
    set first_d [get_pins -of_objects $first -filter {REF_PIN_NAME == D}]
    # Do not exempt the first-Q -> second-D metastability settling path.
    set_false_path -to $first_d
}
if {[info exists variant] && $variant in {bridge_control dp1080}} {
    foreach pair {
        {i_tx0/drive_inhibit_meta_reg i_tx0/drive_inhibit_sync_reg}
        {i_channel_management/i_hotplug_decode/hpd_meta_reg i_channel_management/i_hotplug_decode/hpd_sync_reg}
        {i_tx0/g_tx[0].i_gtx_tx_reset_controller/plllock_meta_reg i_tx0/g_tx[0].i_gtx_tx_reset_controller/plllock_i_reg}
    } {
        set first [require_cell [lindex $pair 0]]
        set second [require_cell [lindex $pair 1]]
        set_property ASYNC_REG TRUE [concat $first $second]
        set_false_path -to [get_pins -of_objects $first -filter {REF_PIN_NAME == D}]
    }
}
