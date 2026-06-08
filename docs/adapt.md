# P1 — the adaptive loop (agent tunes a real oscillator by feedback)

The agent drives the ring oscillator (`rtl/ro_tune.v`) to a **target frequency**
by reading the live count back and searching the tuning-LUT tap settings via
multi-frame ICAP writes — with **no pre-known count↔tap map** (it depends on this
die's process + temperature, so the agent must measure). This is the jump from
phase-1's "execute a known edit" to "discover the edit that meets a goal".

## Pieces
- `rtl/ro_tune.v` — RO whose loop length (freq) = one DONT_TOUCH LUT6's selected
  tap; gated counter → AXI-GPIO ch1@0x41200000 (count), ch2@0x41200008 (meas id).
- `vivado/ro_tune/gen_taps.tcl` — emits `tap0..tap5.bit` (same routing, 6 tap INITs).
- `host/lut-tune.py` — turns a tap bitstream into a multi-frame ICAP "set tap k"
  seq (writes the 4 INIT frames FAR 0x1420-0x1423, located via prjxray bitread).
- `host/ro_adapt.py` — the loop: `sweep` / `measure` / `set k` / `adapt <target>`
  / `watch <target>`.

## Per-session board prep
1. Load `ro_tune` into PL via **U-Boot `fpga loadb`** (reliable; Linux fpgautil can
   wedge DEVCFG): `uboot-fpga-load.py --bit vivado/ro_tune/build/tap0.bit --op loadb`,
   then custom-boot Linux preserving it (skip nandboot's own fpga loadb, add
   `clk_ignore_unused` — already in the dtb fix). 2. Push `icaphw` + `set_tap0..5.bin`.

## Verified (2026-06-08, hardware)
Frequency map (count/524µs window, ~FCLK0 125MHz): tap0≈103.5k, tap1≈101.4k,
tap2≈94.8k, tap3≈90.4k, tap4≈82.1k, tap5≈81.8k (monotonic; ~5 distinct levels).

Feedback-driven binary search converges from any start:
```
adapt 90000  -> tap2(95056) tap4(82074) tap3(89898)  CONVERGED tap3 (err -110), 3 probes
adapt 102000 -> tap2(95043) tap0(103533) tap1(101413) CONVERGED tap1 (err -575), 3 probes
adapt 83000  -> tap2(95032) tap4(82088)               CONVERGED tap4 (err -926), 2 probes
```

## Drift self-repair (`watch <target>`)
Holds the target; re-adapts when the measured count drifts out of tolerance
(e.g. you warm the board → freq drops → agent re-searches). Note: the 6 taps are
coarse (~2-13k apart), so correction is coarse; a finer programmable-delay tuning
element would make drift tracking smooth (future work).
