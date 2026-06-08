# P3 — DFX coarse action: whole-module hot-swap (Linux partial reconfig)

The agent's **fine** action dimension is the ICAP LUT-INIT edit (`act`/`loop`,
`docs/loop.md`/`docs/adapt.md`): nudge one LUT's truth table. P3 adds a **coarse**
dimension: hot-swap an entire reconfigurable module in/out of a Reconfigurable
Partition (RP) while the PS and the on-fabric soft-core keep running — no reset.

## Feasibility gate result — Linux partial reconfig WORKS

> **VERIFIED ON HARDWARE 2026-06-08.** `fpgautil -b <rm>.bin -f Partial` performs
> a live partial reconfiguration on the EBAZ4205 under Buildroot Linux: ~19 ms,
> `RC=0`, `fpga_manager state=operating`, PS + NEORV32 never reset. Confirmed
> bidirectionally by watching the NEORV32 mailbox @0x41200000 flip
> `0x001E0046` (RM1 TPU) ↔ `0x00BB00CC` (RM2 alt).

So the **Linux** partial-reconfig path is the steady-state coarse action — the
U-Boot `fpga loadbp` fallback (`uboot-fpga-load.py --op loadbp`, verified in
zynq_xpart M3) is *not* needed. Kernel side: `Xilinx Zynq FPGA Manager` driver
exposes `/sys/class/fpga_manager/fpga0/flags`; fpgautil's `-f Partial` sets the
partial flag so the driver streams frames via PCAP without re-initialising /
clearing the whole fabric.

## Static design + modules (reused from zynq_xpart DFX)

- **Static**: PS7 + NEORV32 soft-core + XBUS decode + AXI-GPIO mailbox @0x41200000.
  RP cell = `u_soc/wb_tpu_inst`. The NEORV32 firmware loops matmul→mailbox forever,
  so the mailbox always reflects the *currently loaded* RM with no CPU reset.
- **RM1 `rm1_tpu`** — real 4×4 TPU → mailbox `0x001E0046`.
- **RM2 `rm2_alt`** — alternate stub → mailbox `0x00BB00CC`.

Source-of-truth flow: `vivado/dfx/build_dfx.tcl` + `pblock_rp.xdc` (copied from
zynq_xpart; RP floorplanned by explicit SLICE/DSP/RAMB site ranges in X1Y0,
`RESET_AFTER_RECONFIG`+`SNAPPING_MODE`). Prebuilt bitstreams live in `board/dfx/`
(gitignored): `dfx_full.bit` (static+RM1), `rm{1_tpu,2_alt}_partial.bit`, plus the
byte-swapped `.bin`s (via `host/bit2bin.py`) that the Linux fpga_manager needs.

## One-time setup per session (get the static onto the PL)

Full bitstreams are loaded via **U-Boot `fpga loadb`** (reliable; a Linux
`fpgautil` *full* load can wedge DEVCFG → power-cycle — see project notes). Then
custom-boot into Linux **without** re-loading the PL:

```bash
# 1) SLCR-reset to U-Boot (hammer 'd'), see CLAUDE.md recipe
# 2) load static+RM1 over UART ymodem (~4 min):
.env/bin/python host/uboot-fpga-load.py --bit board/dfx/dfx_full.bit \
    --op loadb --read 0x41200000        # -> md shows 0x001E0046
# 3) custom-boot Linux preserving the PL (dtb already has fclk-enable=<9>):
#    setenv bootargs 'console=ttyPS0,115200 root=/dev/mtdblock6 rootfstype=jffs2 noinitrd rw rootwait'
#    nand read 0x2080000 0x300000 0x500000; nand read 0x2000000 0x800000 0x20000
#    bootm 0x2080000 - 0x2000000
```

## Coarse action — `agentctl.py load-module`

```bash
host/agentctl.py load-module rm2_alt   # swap RP -> RM2, expect mailbox 0x00BB00CC
host/agentctl.py load-module rm1_tpu   # swap RP -> RM1, expect mailbox 0x001E0046
```

What it does (idempotent, observable-checked):
1. **Measured gate** — sha256 of the partial `.bin` must be in
   `board/dfx_allowlist.sha256`, else REFUSED (rc 3). Mirrors the P2 frame guard:
   the agent physically cannot partial-reconfig an unmeasured module.
2. Read the mailbox observable (before).
3. Chunked + md5-verified UART push of the `.bin` to `/tmp/<name>.bin` (skipped if
   the board copy already matches — the CH340 needs chunk+retry for ~0.6 MB).
4. `fpgautil -b /tmp/<name>.bin -f Partial`.
5. Read the mailbox observable (after) and compare to the module's expected value.

Exit 0 = swapped & confirmed; 3 = mismatch/refused; 2 = unknown module / not at shell.

## Acceptance (met)

Agent hot-swaps between two RP modules under Linux; the observable switches
`0x001E0046 ↔ 0x00BB00CC`; `fpga_manager` stays `operating`; PS + NEORV32 are not
reset; no power-cycle. Two action dimensions now exist: **fine** (ICAP LUT-INIT)
and **coarse** (`load-module`), both allowlist-gated.
