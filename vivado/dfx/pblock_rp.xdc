# Pblock for the reconfigurable partition (the TPU), Phase 3 DFX.
# Defined by SITE RANGES (not the whole clock region) so the bounding box stays
# over the logic columns and excludes the central clock spine (which is illegal
# in a reconfigurable partition). Covers all logic of region X1Y0: 1100 SLICE +
# 20 DSP48 + BRAM — RM1's TPU (16 DSP, 1 BRAM, ~700 LUT) fits with room to spare.
create_pblock pblock_rp
add_cells_to_pblock [get_pblocks pblock_rp] [get_cells u_soc/wb_tpu_inst]
resize_pblock [get_pblocks pblock_rp] -add {SLICE_X22Y0:SLICE_X43Y49}
resize_pblock [get_pblocks pblock_rp] -add {DSP48_X1Y0:DSP48_X1Y19}
resize_pblock [get_pblocks pblock_rp] -add {RAMB18_X1Y0:RAMB18_X2Y19}
resize_pblock [get_pblocks pblock_rp] -add {RAMB36_X1Y0:RAMB36_X2Y9}
set_property RESET_AFTER_RECONFIG true [get_pblocks pblock_rp]
set_property SNAPPING_MODE ON [get_pblocks pblock_rp]

# Phase 4 (LUT-INIT surgery): disable bitstream CRC so a host-edited LUT INIT in
# the partial bitstream still loads (no CRC recompute needed).
set_property BITSTREAM.GENERAL.CRC Disable [current_design]
