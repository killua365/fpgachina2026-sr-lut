# Fail before export when setup, hold or pulse-width checks violate limits.
# A successfully written bitstream does not imply timing closure.
proc sr_lut_hdmi_require_clean_timing {report_path} {
    set handle [open $report_path r]
    set text [read $handle]
    close $handle
    set start [string first "Design Timing Summary" $text]
    set end [string first "Clock Summary" $text $start]
    if {$start < 0 || $end < 0} { error "HDMI timing summary is missing" }
    set summary [string range $text $start $end]
    set row_pattern {^\s*(-?\d+\.\d+\s+-?\d+\.\d+\s+\d+\s+\d+\s+-?\d+\.\d+\s+-?\d+\.\d+\s+\d+\s+\d+\s+-?\d+\.\d+\s+-?\d+\.\d+\s+\d+\s+\d+)\s*$}
    if {![regexp -line $row_pattern $summary all row]} {
        error "HDMI timing summary cannot be parsed"
    }
    set fields [regexp -all -inline {\S+} $row]
    foreach index {0 4 8} name {setup hold pulse_width} {
        if {[lindex $fields $index] < 0} {
            error "HDMI $name violation: [lindex $fields $index] ns; no waiver allowed"
        }
    }
    foreach index {1 2 5 6 9 10} {
        if {[lindex $fields $index] != 0} { error "HDMI timing has failing endpoints or violation totals" }
    }
    return $fields
}
