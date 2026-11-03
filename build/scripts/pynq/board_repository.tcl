set sr_lut_board_repository [file normalize [file join [file dirname [info script]] boards]]
if {![file isfile [file join $sr_lut_board_repository pynq_z2 1.0 board.xml]]} {
    error "Project PYNQ-Z2 board files are missing: $sr_lut_board_repository"
}
set_param board.repoPaths [list $sr_lut_board_repository]
