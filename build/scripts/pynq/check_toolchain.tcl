set script_dir [file dirname [file normalize [info script]]]
source [file join $script_dir board_repository.tcl]
set expected_version 2026.1
if {[version -short] ne $expected_version} {
    error "Expected Vivado $expected_version; got [version -short]"
}
set part xc7z020clg400-1
set board tul.com.tw:pynq-z2:part0:1.0
if {[llength [get_parts -quiet $part]] != 1} { error "Device support missing: $part" }
if {[llength [get_board_parts -quiet $board]] != 1} { error "Board support missing: $board" }
create_project -in_memory toolchain_check -part $part
set_property board_part $board [current_project]
foreach ip {processing_system7 axi_dma axi_gpio clk_wiz} {
    set definitions [get_ipdefs -all -quiet xilinx.com:ip:${ip}:*]
    if {[llength $definitions] == 0} { error "Required IP missing: $ip" }
    puts "SR_LUT_TOOLCHAIN_IP $definitions"
}
puts "SR_LUT_TOOLCHAIN_CHECK_PASS version=[version -short] part=$part board=$board"
close_project
exit 0
