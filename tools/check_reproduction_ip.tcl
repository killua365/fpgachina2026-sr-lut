# Only create/import IP in a new scratch project; never open hardware.
if {$argc != 2} {error "Usage: check_reproduction_ip.tcl NEW_OUTPUT IP_ROOT"}
set out [file normalize [lindex $argv 0]]
set ips [file normalize [lindex $argv 1]]
if {[file exists $out]} {error "Refuse existing output"}
create_project ip_probe $out -part xc7a200tsbg484-1
foreach name {n3_mig n3_vdma} {import_ip -files [file join $ips $name ${name}.xci]}
set mig [get_ips n3_mig]
set cfg [get_property CONFIG.XML_INPUT_FILE $mig]
set xci [get_files -all */n3_mig.xci]
set resolved [file normalize [file join [file dirname $xci] $cfg]]
if {![file isfile $resolved]} {error "Relocated MIG project missing: $resolved"}
if {[get_property CONFIG.c_m_axi_s2mm_data_width [get_ips n3_vdma]] != 128} {error "Unexpected VDMA data width"}
report_ip_status -file [file join $out ip_status.rpt]
puts "DELIVERY_RELOCATED_IP_IMPORT_PASS_NO_HARDWARE"
close_project
