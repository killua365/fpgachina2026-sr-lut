# Clock review for the fixed HBR reference profile, applied after synthesis.
# UG482 eq. 2-1/2-2: 135 MHz * 5 * 4 / 1 = 2700 MHz PLL;
# line rate = 2700 * 2 / TXOUT_DIV(2) = 2700 Mbit/s.
# UG949: 7 Series GT output clocks are defined at the output pins.
# Never model the hard PLL VCO itself as a 135 MHz fabric clock.
proc clock_require_property {cell property expected} {
    if {[get_property $property $cell] ne $expected} {
        error "Clock profile mismatch: $cell $property expected $expected"
    }
}
proc clock_require_bit {pin expected} {
    set nets [get_nets -quiet -of_objects [get_pins [list $pin]]]
    set drivers [get_pins -quiet -of_objects $nets -filter {DIRECTION == OUT}]
    set cells [get_cells -quiet -of_objects $drivers]
    set primitive [expr {$expected ? "VCC" : "GND"}]
    if {[llength $cells] != 1 || [get_property REF_NAME $cells] ne $primitive} {
        error "Clock selection is not fixed as reviewed: $pin expected $expected"
    }
}
set common [get_cells -hier -filter {REF_NAME == GTPE2_COMMON}]
if {[llength $common] != 1} {error "Expected one GTP common block"}
foreach {property value} {PLL0_FBDIV 4 PLL0_FBDIV_45 5 PLL0_REFCLK_DIV 1} {
    clock_require_property $common $property $value
}
clock_require_bit "$common/PLL1PD" 1
clock_require_bit "$common/GTREFCLK1" 0
if {[llength [get_cells -hier -filter {REF_NAME == IBUFDS_GTE2}]] != 1} {
    error "Only the populated board reference input is allowed"
}
foreach {bit value} {0 1 1 0 2 0} {
    clock_require_bit [format {%s/PLL0REFCLKSEL[%d]} $common $bit] $value
}
set channels [get_cells -hier -filter {REF_NAME == GTPE2_CHANNEL}]
if {[llength $channels] != 2} {error "Expected two physical GTP channel primitives"}
foreach channel $channels {
    foreach {property value} {TXOUT_DIV 2 TX_DATA_WIDTH 20 TXBUF_EN TRUE TX_XCLK_SEL TXOUT} {
        clock_require_property $channel $property $value
    }
    foreach {pin value} {TXSYSCLKSEL[0] 0 TXSYSCLKSEL[1] 0 TXOUTCLKSEL[0] 0 TXOUTCLKSEL[1] 1 TXOUTCLKSEL[2] 0 TXRATE[0] 0 TXRATE[1] 0 TXRATE[2] 0} {
        clock_require_bit "$channel/$pin" $value
    }
}
# The adapted XDC defines only the two physical board input clocks.
set expected_clocks [list sys_clk_pin refclk0_p]
if {[lsort [get_property NAME [get_clocks]]] ne [lsort $expected_clocks]} {
    error "Unexpected clock set; the reviewed pin XDC must be used"
}
create_clock -name i_tx0/TXOUTCLK -period 7.407407 [get_pins {i_tx0/g_tx[0].gtpe2_i/TXOUTCLK}]
# The reference monitor now uses IBUFDS_GTE2.O -> BUFG, timed by refclk0_p.
# TXOUTCLKFABRIC has no fabric loads and needs no invented output clock.
foreach channel $channels {
    set test_clock [get_pins [list "$channel/TXOUTCLKFABRIC"]]
    if {[llength [get_pins -quiet -of_objects [get_nets -quiet -of_objects $test_clock] -filter {DIRECTION == IN}]] != 0} {
        error "Test-only TXOUTCLKFABRIC must not drive fabric"
    }
}
# No external FMC refclk1 is present; PLL1 is powered down. Neither is used
# by TXSYSCLKSEL/PLL0REFCLKSEL above. They are not free-running fabric clocks.
puts "CLOCK_PROFILE_CHECKED: refclk0=135MHz PLL0=2700MHz HBR=2.7Gbps TX=135MHz; offline only"
