# F0.1 — tunable ring oscillator + frequency counter, EBAZ4205 (XC7Z010).
# Built on the hwicap_lut pattern (zynq_xpart) so the agent can later retune the
# RO's "tuning LUT" INIT live via HWICAP/ICAP.
#
#   PS7 --GP0--> AXI HWICAP @0x41400000   (icap_clk <- FCLK0; the ICAP write engine)
#            \-> AXI-GPIO(dual,input) @0x41200000  ch1 <- ro_tune.count_o (RO freq)
#                                                  ch2 <- ro_tune.status_o (meas id/state)
#
# Produces lut_A.bit (tuning LUT = tap0) and lut_B.bit (tuning LUT = tap5); the two
# differ only in the tuning LUT6 INIT, so a host diff/prjxray locates its frame.
#
# Run:  source /home/test/Xilinx/2025.2/Vivado/settings64.sh ; vivado -mode batch -source build_ro_tune.tcl

set proj   ro_tune
set part   xc7z010clg400-1
set origin [file normalize [file dirname [info script]]]
set root   [file normalize $origin/../..]
set bdir   $origin/build

create_project $proj $bdir -part $part -force
add_files -norecurse $root/rtl/ro_tune.v
update_compile_order -fileset sources_1

create_bd_design "system"

set ps7 [create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7 ps7_0]
set_property -dict [list \
  CONFIG.PCW_USE_M_AXI_GP0 {1} CONFIG.PCW_EN_CLK0_PORT {1} CONFIG.PCW_FCLK_CLK0_BUF {TRUE} \
] $ps7

# dual-channel AXI-GPIO, both channels 32-bit inputs (ch1=count, ch2=status)
set gpio [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio axi_gpio_0]
set_property -dict [list CONFIG.C_GPIO_WIDTH {32} CONFIG.C_ALL_INPUTS {1} \
  CONFIG.C_IS_DUAL {1} CONFIG.C_GPIO2_WIDTH {32} CONFIG.C_ALL_INPUTS_2 {1}] $gpio

set hwi [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_hwicap axi_hwicap_0]
create_bd_cell -type module -reference ro_tune ro_tune_0

set ic  [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect axi_ic_0]
set_property -dict [list CONFIG.NUM_MI {2}] $ic
create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset rst_0

set clk [get_bd_pins ps7_0/FCLK_CLK0]
foreach p {ps7_0/M_AXI_GP0_ACLK axi_ic_0/ACLK axi_ic_0/S00_ACLK axi_ic_0/M00_ACLK \
           axi_ic_0/M01_ACLK axi_gpio_0/s_axi_aclk axi_hwicap_0/s_axi_aclk \
           axi_hwicap_0/icap_clk rst_0/slowest_sync_clk ro_tune_0/clk} {
  connect_bd_net $clk [get_bd_pins $p]
}

connect_bd_net [get_bd_pins ps7_0/FCLK_RESET0_N] [get_bd_pins rst_0/ext_reset_in]
foreach p {axi_ic_0/ARESETN axi_ic_0/S00_ARESETN axi_ic_0/M00_ARESETN axi_ic_0/M01_ARESETN} {
  connect_bd_net [get_bd_pins rst_0/interconnect_aresetn] [get_bd_pins $p]
}
connect_bd_net [get_bd_pins rst_0/peripheral_aresetn] [get_bd_pins axi_gpio_0/s_axi_aresetn]
connect_bd_net [get_bd_pins rst_0/peripheral_aresetn] [get_bd_pins axi_hwicap_0/s_axi_aresetn]
connect_bd_net [get_bd_pins rst_0/peripheral_aresetn] [get_bd_pins ro_tune_0/resetn]

connect_bd_intf_net [get_bd_intf_pins ps7_0/M_AXI_GP0]  [get_bd_intf_pins axi_ic_0/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_ic_0/M00_AXI] [get_bd_intf_pins axi_gpio_0/S_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_ic_0/M01_AXI] [get_bd_intf_pins axi_hwicap_0/S_AXI_LITE]
connect_bd_net [get_bd_pins ro_tune_0/count_o]  [get_bd_pins axi_gpio_0/gpio_io_i]
connect_bd_net [get_bd_pins ro_tune_0/status_o] [get_bd_pins axi_gpio_0/gpio2_io_i]

assign_bd_address
validate_bd_design
save_bd_design
puts "=== ADDRESS MAP ==="
foreach seg [get_bd_addr_segs -of_objects [get_bd_addr_spaces ps7_0/Data]] {
  puts "  $seg -> [get_property OFFSET $seg]"
}

make_wrapper -files [get_files system.bd] -top -import
set_property top system_wrapper [current_fileset]
update_compile_order -fileset sources_1

# combinational ring: the XDC marks the loop nets ALLOW_COMBINATORIAL_LOOPS
# (impl-only; nets exist post-synth). DONT_TOUCH LUTs keep the ring through synth.
add_files -fileset constrs_1 -norecurse $origin/ro_tune.xdc
set_property USED_IN_SYNTHESIS false [get_files $origin/ro_tune.xdc]

launch_runs synth_1 -jobs 8
wait_on_run synth_1
puts "=== SYNTH STATUS: [get_property STATUS [get_runs synth_1]] ==="

launch_runs impl_1 -to_step route_design -jobs 8
wait_on_run impl_1
puts "=== IMPL STATUS: [get_property STATUS [get_runs impl_1]] ==="

open_run impl_1
# allow the combinational loop in the routed design too, and don't fail on it
catch { set_property ALLOW_COMBINATORIAL_LOOPS TRUE [get_nets -hier -filter {NAME =~ *ro_tune_0*}] }
catch { set_property SEVERITY {Warning} [get_drc_checks LUTLP-1] }
set_property BITSTREAM.GENERAL.CRC Disable [current_design]

set lut [get_cells -hier -filter {REF_NAME == LUT6 && NAME =~ *tune_lut*}]
puts "=== tuning LUT: $lut  LOC=[get_property LOC $lut]  BEL=[get_property BEL $lut] ==="

# setting A : tuning LUT = tap0 (default INIT)
set_property INIT 64'hAAAAAAAAAAAAAAAA $lut
write_bitstream -force $bdir/lut_A.bit
# setting B : tuning LUT = tap5 (long loop -> lower freq)
set_property INIT 64'hFFFFFFFF00000000 $lut
write_bitstream -force $bdir/lut_B.bit
puts "=== A(tap0): $bdir/lut_A.bit   B(tap5): $bdir/lut_B.bit ==="
exit
