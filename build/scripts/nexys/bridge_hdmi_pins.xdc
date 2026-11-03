# Digilent fixed Rev A.0/1.2 part0_pins.xml; receive connector only.
set_property -dict {PACKAGE_PIN V4 IOSTANDARD TMDS_33} [get_ports hdmi_clk_p]
set_property -dict {PACKAGE_PIN W4 IOSTANDARD TMDS_33} [get_ports hdmi_clk_n]
set_property -dict {PACKAGE_PIN Y3 IOSTANDARD TMDS_33} [get_ports {hdmi_data_p[0]}]
set_property -dict {PACKAGE_PIN AA3 IOSTANDARD TMDS_33} [get_ports {hdmi_data_n[0]}]
set_property -dict {PACKAGE_PIN W2 IOSTANDARD TMDS_33} [get_ports {hdmi_data_p[1]}]
set_property -dict {PACKAGE_PIN Y2 IOSTANDARD TMDS_33} [get_ports {hdmi_data_n[1]}]
set_property -dict {PACKAGE_PIN U2 IOSTANDARD TMDS_33} [get_ports {hdmi_data_p[2]}]
set_property -dict {PACKAGE_PIN V2 IOSTANDARD TMDS_33} [get_ports {hdmi_data_n[2]}]
# The board XML has a stale LVCMOS33 value. Digilent's master XDC and
# the physical bank-13 supply specify 2.5 V for HPA (AB12).
set_property -dict {PACKAGE_PIN AB12 IOSTANDARD LVCMOS25 SLEW SLOW} [get_ports hdmi_hpd]
set_property -dict {PACKAGE_PIN R3 IOSTANDARD LVCMOS33 SLEW SLOW} [get_ports hdmi_rx_txen]
set_property -dict {PACKAGE_PIN Y4 IOSTANDARD LVCMOS33 SLEW SLOW} [get_ports hdmi_scl]
set_property -dict {PACKAGE_PIN AB5 IOSTANDARD LVCMOS33 SLEW SLOW} [get_ports hdmi_sda]
create_clock -name hdmi_7425 -period 13.468013 [get_ports hdmi_clk_p]
# The vendor receiver calibrates serial sampling with IDELAY/phase alignment.
# No guessed external delay is supplied. Preserve the resulting port timing
# warnings for explicit receiver review and physical qualification.
# HPD/DDC are slow asynchronous protocol signals, never DDR video data.
set_false_path -to [get_ports {hdmi_hpd hdmi_rx_txen hdmi_scl hdmi_sda}]
