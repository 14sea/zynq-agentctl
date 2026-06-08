# Phase 3 DFX (RTL-top structure): PS-only BD + dfx_top (RTL) instantiating
# neorv32_soc_dfx, whose TPU is the reconfigurable partition `tpu_rp`.
# Produces a full bitstream (static + RM1) + partial bitstreams for RM1 and RM2.
#   vivado -mode batch -source build_dfx.tcl
#
# PROVENANCE COPY — this is the as-built DFX flow from the zynq_xpart project; it
# is kept here so the P3 static-design recipe is self-documenting. It references
# RTL ($root/rtl/neorv32_soc_dfx.vhd, rtl/dfx/*.v, the NEORV32 source tree) that
# lives in zynq_xpart, NOT in zynq-agentctl. To REBUILD, run it inside zynq_xpart;
# zynq-agentctl consumes only the prebuilt bitstreams (board/dfx/, gitignored).
# See docs/dfx.md.

set proj   dfx
set part   xc7z010clg400-1
set origin [file normalize [file dirname [info script]]]
set root   [file normalize $origin/../..]
set nhome  $root/rtl_src/neorv32_tpu/neorv32
set bdir   $origin/build

create_project $proj $bdir -part $part -force
set_property PR_FLOW 1 [current_project]

# --- static + RM1 RTL sources ---
set fl [read [open $nhome/rtl/file_list_soc.f r]]
set fl [string map [list NEORV32_RTL_PATH_PLACEHOLDER $nhome/rtl] $fl]
add_files $fl
set_property library neorv32 [get_files $fl]
add_files $root/rtl/neorv32_soc_dfx.vhd
add_files $root/rtl/dfx_top.v
add_files [list $root/rtl/dfx/tpu_rp_rm1_tpu.v $root/rtl/pe.v \
                $root/rtl/systolic_array_4x4.v $root/rtl/tpu_accel.v $root/rtl/wb_tpu_accel.v]

# --- PS-only block design (PS7 + AXI-GPIO mailbox), exposing fclk/rstn/mbox ---
create_bd_design "ps"
set ps7 [create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7 ps7_0]
set_property -dict [list CONFIG.PCW_USE_M_AXI_GP0 {1} CONFIG.PCW_EN_CLK0_PORT {1} CONFIG.PCW_FCLK_CLK0_BUF {TRUE}] $ps7
set gpio [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio axi_gpio_0]
set_property -dict [list CONFIG.C_GPIO_WIDTH {32} CONFIG.C_ALL_INPUTS {1} \
  CONFIG.C_IS_DUAL {1} CONFIG.C_GPIO2_WIDTH {1} CONFIG.C_ALL_INPUTS_2 {1}] $gpio
set ic [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect axi_ic_0]
set_property CONFIG.NUM_MI {1} $ic
create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset rst_0
create_bd_port -dir O fclk_o
create_bd_port -dir O rstn_o
create_bd_port -dir I -from 31 -to 0 mbox_i
create_bd_port -dir I mbox_valid_i
set clk [get_bd_pins ps7_0/FCLK_CLK0]
foreach p {ps7_0/M_AXI_GP0_ACLK axi_ic_0/ACLK axi_ic_0/S00_ACLK axi_ic_0/M00_ACLK \
           axi_gpio_0/s_axi_aclk rst_0/slowest_sync_clk} {
  connect_bd_net $clk [get_bd_pins $p]
}
connect_bd_net $clk [get_bd_ports fclk_o]
connect_bd_net [get_bd_pins ps7_0/FCLK_RESET0_N] [get_bd_pins rst_0/ext_reset_in]
foreach p {axi_ic_0/ARESETN axi_ic_0/S00_ARESETN axi_ic_0/M00_ARESETN} {
  connect_bd_net [get_bd_pins rst_0/interconnect_aresetn] [get_bd_pins $p]
}
connect_bd_net [get_bd_pins rst_0/peripheral_aresetn] [get_bd_pins axi_gpio_0/s_axi_aresetn]
connect_bd_net [get_bd_pins rst_0/peripheral_aresetn] [get_bd_ports rstn_o]
connect_bd_intf_net [get_bd_intf_pins ps7_0/M_AXI_GP0]  [get_bd_intf_pins axi_ic_0/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_ic_0/M00_AXI] [get_bd_intf_pins axi_gpio_0/S_AXI]
connect_bd_net [get_bd_ports mbox_i]       [get_bd_pins axi_gpio_0/gpio_io_i]
connect_bd_net [get_bd_ports mbox_valid_i] [get_bd_pins axi_gpio_0/gpio2_io_i]
assign_bd_address
validate_bd_design
save_bd_design
make_wrapper -files [get_files ps.bd] -top -import

# --- top = RTL dfx_top ---
set_property top dfx_top [current_fileset]
update_compile_order -fileset sources_1

# --- DFX: partition def + reconfig modules + configurations ---
set rp_cell u_soc/wb_tpu_inst
create_partition_def -name tpu_pd -module tpu_rp
create_reconfig_module -name rm1_tpu -partition_def [get_partition_defs tpu_pd] -define_from tpu_rp
create_reconfig_module -name rm2_alt -partition_def [get_partition_defs tpu_pd] -top tpu_rp
add_files -norecurse -of_objects [get_reconfig_modules rm2_alt] $root/rtl/dfx/tpu_rp_rm2_alt.v
create_reconfig_module -name rm_lut -partition_def [get_partition_defs tpu_pd] -top tpu_rp
add_files -norecurse -of_objects [get_reconfig_modules rm_lut] $root/rtl/dfx/tpu_rp_rm_lut.v
create_reconfig_module -name rm_lut_b -partition_def [get_partition_defs tpu_pd] -top tpu_rp
add_files -norecurse -of_objects [get_reconfig_modules rm_lut_b] $root/rtl/dfx/tpu_rp_rm_lut_b.v
create_pr_configuration -name cfg1 -partitions [list $rp_cell:rm1_tpu]
create_pr_configuration -name cfg2 -partitions [list $rp_cell:rm2_alt]
create_pr_configuration -name cfg3 -partitions [list $rp_cell:rm_lut]
create_pr_configuration -name cfg4 -partitions [list $rp_cell:rm_lut_b]
add_files -fileset constrs_1 -norecurse $origin/pblock_rp.xdc

set_property PR_CONFIGURATION cfg1 [get_runs impl_1]
create_run impl_2 -parent_run impl_1 -flow [get_property FLOW [get_runs impl_1]] -pr_config cfg2
create_run impl_3 -parent_run impl_1 -flow [get_property FLOW [get_runs impl_1]] -pr_config cfg3
create_run impl_4 -parent_run impl_1 -flow [get_property FLOW [get_runs impl_1]] -pr_config cfg4

launch_runs synth_1 -jobs 8
wait_on_run synth_1
launch_runs impl_1 -to_step write_bitstream -jobs 8
wait_on_run impl_1
puts "=== impl_1 (cfg1): [get_property STATUS [get_runs impl_1]] ==="
launch_runs impl_2 -to_step write_bitstream -jobs 8
wait_on_run impl_2
puts "=== impl_2 (cfg2): [get_property STATUS [get_runs impl_2]] ==="
launch_runs impl_3 -to_step write_bitstream -jobs 8
wait_on_run impl_3
puts "=== impl_3 (cfg3 lut A): [get_property STATUS [get_runs impl_3]] ==="
launch_runs impl_4 -to_step write_bitstream -jobs 8
wait_on_run impl_4
puts "=== impl_4 (cfg4 lut B): [get_property STATUS [get_runs impl_4]] ==="
puts "=== bitstreams ==="
foreach b [glob -nocomplain $bdir/$proj.runs/impl_1/*.bit $bdir/$proj.runs/impl_2/*.bit $bdir/$proj.runs/impl_3/*.bit $bdir/$proj.runs/impl_4/*.bit] { puts "  $b" }
exit
