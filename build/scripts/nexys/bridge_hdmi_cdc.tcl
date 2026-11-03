# Only the asynchronous preset pins of the explicit RX reset synchronizer.
# RX write-control registers use its final synchronous output. Data paths and
# synchronizer settling paths are not exempted.
set chain [get_cells -hier -filter {NAME =~ core/ingress_fifo/rx_reset_chain_reg*}]
if {[llength $chain]!=3} {error "Expected three ingress RX reset synchronizer stages"}
set_property ASYNC_REG TRUE $chain
set presets [get_pins -of_objects $chain -filter {REF_PIN_NAME == PRE}]
if {[llength $presets]!=3} {error "Unexpected RX reset preset structure"}
set_false_path -to $presets
