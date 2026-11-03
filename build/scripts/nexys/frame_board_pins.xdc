# Nexys Video Rev A board definition, fixed Digilent commit 36f34ab687b7fa9c778b779d027f3bce63b3ace9.
# All DDR IO constraints come from the generated, pin-verified MIG IP.
set_property -dict {PACKAGE_PIN R4 IOSTANDARD LVCMOS33} [get_ports clk100]
create_clock -name board100 -period 10.000 [get_ports clk100]
set_property -dict {PACKAGE_PIN AA19 IOSTANDARD LVCMOS33 SLEW SLOW} [get_ports diagnostic_tx]
set_property -dict {PACKAGE_PIN T14 IOSTANDARD LVCMOS25 SLEW SLOW} [get_ports {led[0]}]
set_property -dict {PACKAGE_PIN T15 IOSTANDARD LVCMOS25 SLEW SLOW} [get_ports {led[1]}]
set_property -dict {PACKAGE_PIN T16 IOSTANDARD LVCMOS25 SLEW SLOW} [get_ports {led[2]}]
set_property -dict {PACKAGE_PIN U16 IOSTANDARD LVCMOS25 SLEW SLOW} [get_ports {led[3]}]
set_property -dict {PACKAGE_PIN V15 IOSTANDARD LVCMOS25 SLEW SLOW} [get_ports {led[4]}]
set_property -dict {PACKAGE_PIN W16 IOSTANDARD LVCMOS25 SLEW SLOW} [get_ports {led[5]}]
set_property -dict {PACKAGE_PIN W15 IOSTANDARD LVCMOS25 SLEW SLOW} [get_ports {led[6]}]
set_property -dict {PACKAGE_PIN Y13 IOSTANDARD LVCMOS25 SLEW SLOW} [get_ports {led[7]}]
# UART is an asynchronous serial output, LEDs are human indicators. Neither is
# sampled by an external FPGA timing clock; this exception does not cover DDR.
set_false_path -to [get_ports {diagnostic_tx led[*]}]
# DDR3 RESET# is asynchronous, with reset/CKE sequencing managed by MIG.
# This is not an exception for the synchronous DDR address/data/control buses.
set_false_path -to [get_ports ddr3_reset_n]

# DP pins and populated 135 MHz reference, from the qualified N2 pin map.
set_property -dict {PACKAGE_PIN AA9 IOSTANDARD LVDS_25} [get_ports dp_rx_aux_p]
set_property -dict {PACKAGE_PIN AB10 IOSTANDARD LVDS_25} [get_ports dp_rx_aux_n]
set_property -dict {PACKAGE_PIN AA10 IOSTANDARD LVDS_25} [get_ports dp_tx_aux_p]
set_property -dict {PACKAGE_PIN AA11 IOSTANDARD LVDS_25} [get_ports dp_tx_aux_n]
set_property -dict {PACKAGE_PIN N15 IOSTANDARD LVCMOS33} [get_ports dp_tx_hp_detect]
set_property PACKAGE_PIN F6 [get_ports refclk0_p]
set_property PACKAGE_PIN E6 [get_ports refclk0_n]
create_clock -name refclk0_p -period 7.407407 [get_ports refclk0_p]
set_property PACKAGE_PIN B4 [get_ports {gtptxp[0]}]
set_property PACKAGE_PIN A4 [get_ports {gtptxn[0]}]
set_property PACKAGE_PIN D5 [get_ports {gtptxp[1]}]
set_property PACKAGE_PIN C5 [get_ports {gtptxn[1]}]
