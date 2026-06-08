# Re-open the routed ro_tune impl and emit one bitstream per tuning-LUT tap
# (tap0..tap5 = LUT6 pass-through of input k). Same routing, only the tune_lut
# INIT differs -> host/lut-tune.py turns any pair into a multi-frame ICAP "set tap"
# sequence for the P1 adaptive search.
#   vivado -mode batch -source gen_taps.tcl
set origin [file normalize [file dirname [info script]]]
set bdir   $origin/build
open_project $bdir/ro_tune.xpr
open_run impl_1

catch { set_property ALLOW_COMBINATORIAL_LOOPS TRUE [get_nets -hier -filter {NAME =~ *ro_tune_0*}] }
catch { set_property SEVERITY {Warning} [get_drc_checks LUTLP-1] }
set_property BITSTREAM.GENERAL.CRC Disable [current_design]

set lut [get_cells -hier -filter {REF_NAME == LUT6 && NAME =~ *tune_lut*}]
puts "=== tune_lut: $lut LOC=[get_property LOC $lut] ==="

# tap k -> LUT6 pass-through of input k
array set INIT {
  0 AAAAAAAAAAAAAAAA  1 CCCCCCCCCCCCCCCC  2 F0F0F0F0F0F0F0F0
  3 FF00FF00FF00FF00  4 FFFF0000FFFF0000  5 FFFFFFFF00000000
}
foreach k {0 1 2 3 4 5} {
  set_property INIT 64'h$INIT($k) $lut
  write_bitstream -force $bdir/tap$k.bit
  puts "=== wrote tap$k.bit (INIT=$INIT($k)) ==="
}
exit
