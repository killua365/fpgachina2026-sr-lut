# Shared only by the new long-test entries. Never programs configuration memory.
proc long_assert {device expected} {
    foreach {property value} {
        IDCODE_HEX 13636093
        REGISTER.CONFIG_STATUS.BIT00_CRC_ERROR 0
        REGISTER.CONFIG_STATUS.BIT12_INIT_B_PIN 1
        REGISTER.CONFIG_STATUS.BIT14_DONE_PIN 1
        REGISTER.CONFIG_STATUS.BIT15_IDCODE_ERROR 0
        {REGISTER.CONFIG_STATUS.BIT08_MODE_PIN_M[0]} 1
        {REGISTER.CONFIG_STATUS.BIT09_MODE_PIN_M[1]} 0
        {REGISTER.CONFIG_STATUS.BIT10_MODE_PIN_M[2]} 0
    } {
        if {[get_property $property $device] ne $value} {error "Device status mismatch: $property"}
    }
    if {[get_property REGISTER.USR_ACCESS $device] ne $expected} {error "Device identity mismatch"}
}
proc long_connect {} {
    open_hw_manager
    connect_hw_server -url localhost:3121
    set targets [get_hw_targets -quiet *210276B3E2AD*]
    if {[llength $targets]!=1} {error "Recorded board missing or ambiguous"}
    set target [lindex $targets 0]
    current_hw_target $target
    set_property PARAM.FREQUENCY 15000000 $target
    open_hw_target $target
    set devices [get_hw_devices -quiet -filter {PART == xc7a200t}]
    if {[llength $devices]!=1 || [llength [get_hw_devices]]!=1} {error "Unexpected chain"}
    set device [lindex $devices 0]
    current_hw_device $device
    refresh_hw_device -update_hw_probes false $device
    if {[get_property IDCODE_HEX $device] ne "13636093"} {error "Wrong device ID"}
    return [list $target $device]
}
proc long_recover {device out} {
    boot_hw_device $device
    after 5000
    refresh_hw_device -update_hw_probes false $device
    report_property -all $device -file [file join $out device_restored.rpt]
    long_assert $device 00000000
}
proc long_cleanup {} {
    catch {close_hw_target}
    catch {disconnect_hw_server}
    catch {close_hw_manager}
}
proc long_write {name contents} {
    set f [open $name {WRONLY CREAT EXCL}]
    puts $f $contents
    close $f
}
