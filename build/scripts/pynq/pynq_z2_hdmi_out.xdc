# HDMI TX pins verified against Xilinx/PYNQ v3.0.1 Pynq-Z2 base.xdc.
# Local board.xml interface indices are swapped; do not apply its HDMI automation.
set_property IOSTANDARD TMDS_33 [get_ports {hdmi_out_clk_p hdmi_out_clk_n hdmi_out_data_p[*] hdmi_out_data_n[*]}]
set_property PACKAGE_PIN L16 [get_ports hdmi_out_clk_p]
set_property PACKAGE_PIN L17 [get_ports hdmi_out_clk_n]
set_property PACKAGE_PIN K17 [get_ports {hdmi_out_data_p[0]}]
set_property PACKAGE_PIN K18 [get_ports {hdmi_out_data_n[0]}]
set_property PACKAGE_PIN K19 [get_ports {hdmi_out_data_p[1]}]
set_property PACKAGE_PIN J19 [get_ports {hdmi_out_data_n[1]}]
set_property PACKAGE_PIN J18 [get_ports {hdmi_out_data_p[2]}]
set_property PACKAGE_PIN H18 [get_ports {hdmi_out_data_n[2]}]
