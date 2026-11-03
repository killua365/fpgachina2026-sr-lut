set script_dir [file dirname [file normalize [info script]]]
set project_root [file normalize [file join $script_dir .. .. ..]]
source [file join $script_dir board_repository.tcl]
if {[version -short] ne "2026.1"} {
    error "This entry point requires Vivado 2026.1"
}
if {$argc != 1 || [lindex $argv 0] ni {media_x2 media_x4 camera_x4}} {
    error "Usage: vivado -mode gui -source open_stage39_project.tcl -tclargs media_x2|media_x4|camera_x4"
}
set component [lindex $argv 0]
set project_file [file join $project_root build stage39_${component}_vivado2026_1_work sr_lut_s39_dyn.xpr]
if {![file isfile $project_file]} {
    error "Build this component first; project missing: $project_file"
}
open_project $project_file
puts "SR_LUT_2026_1_PROJECT_OPEN component=$component part=[get_property PART [current_project]]"
