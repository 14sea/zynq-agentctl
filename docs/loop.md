# The closed loop — agent controls FPGA live state

The agent (a Claude session on the host) drives a **perceive → decide → act →
verify** loop over `/dev/ebaz-uart`, mutating a live PL LUT-INIT bit via ICAP
with no reset. All hardware-verified on the EBAZ4205 (XC7Z010), 2026-06-08.

## One-time persistence (already flashed to NAND)

- **`nand-device-tree` (mtd2)** holds our dtb with `&clkc { fclk-enable = <9>; }`
  (= FCLK0 + FCLK3). The stock board dts used `<8>` (FCLK3 only), so Linux
  `clk_disable_unused` gated **FCLK0** → the PL AXI interconnect (clocked by
  FCLK0) froze → every PL-AXI access hung the A9 hard. `<9>` keeps FCLK0 on.
- **`nand-bitstream` (mtd5)** holds `lut_A.bin` (byte-swapped .bin, 2083740 B).
  Loaded at runtime with `dd if=/dev/mtd5 bs=4096 count=509` + fpgautil — the dd
  reads 509×4096 = 2084864 B (page-aligned; the ~1 KB past EOF is 0xFF NAND pad,
  which fpga_manager accepts).

Rebuild the dtb: `host/bit2bin.py` is unrelated; the dtb is built with the kernel
`scripts/dtc/dtc` from `board/dts/zynq-ebaz4205.dts` (the one-line `<8>`→`<9>` edit).
Reflash either partition with `host/nand-flash.py --only dtb|bitstream` (ymodem,
error-corrected — reliable for 2 MB unlike the base64 UART push).

## Per-session workflow (host)

```bash
cd /home/test/zynq_agentctl
P=.env/bin/python    # repo venv (see docs/mcp.md Setup); any python with pyserial works
$P host/agentctl.py ensure-linux     # boot to Linux root shell if needed
$P host/agentctl.py setup            # push icaphw+seqs; dd mtd5 -> fpgautil; health
$P host/agentctl.py perceive         # read LUT INIT[0]
$P host/agentctl.py loop 1           # drive INIT[0]->1 (act + verify, 1 retry)
$P host/agentctl.py loop 0           # drive INIT[0]->0
```

`setup` pushes only the small artifacts (`icaphw` 9.6 KB, two 932 B seqs) — the
2 MB bitstream comes from mtd5, not UART.

## What one `act` does (in `icaphw edit`)

1. read GPIO@0x41200000 bit0 (the live LUT6 INIT[0]) — *perceive*
2. `devcfg.CTRL[PCAP_PR]=0` (0x4c00e07f→0x4400e07f) — hand the config MUX to ICAP
3. stream the 233-word single-frame WCFG/FDRI seq into AXI HWICAP@0x41400000
4. `devcfg.CTRL[PCAP_PR]=1` (restore; the edit persists)
5. re-read GPIO bit0 — *verify*

`seqAB.bin` writes INIT[0]=1, `seqBA.bin` writes INIT[0]=0 (frames extracted from
`lut_A.bit`/`lut_B.bit` by `host/hwicap-make-framewrite.py`, FAR=0x00400d9a wofs=73).

## Verified result

```
perceive -> 0
loop 1   -> act 0→1, verify OK, converged in 1 attempt
loop 0   -> act 1→0, verify OK, converged in 1 attempt
loop 0   -> already at target; nothing to do
```

Bidirectional live LUT-INIT edit from Linux userspace, no reset — the agent
reads hardware state, decides, rewrites the FPGA fabric, and confirms.

## Gotchas (see docs/plan.md + project memory)

- Reading PL-AXI from Linux **before** the FCLK0 fix hangs the A9 so hard JTAG
  halt times out → only Type-C power-cycle recovers. Never probe PL-AXI from the
  CPU unless FCLK0 is confirmed on. Safe probe = openocd DAP mem-AP.
- 2 MB single-shot base64 UART push is ~50% flaky; use mtd5 (above) or chunked push.
- Recovery to U-Boot: openocd SLCR soft-reset (halt cpu0/cpu1 → `mww phys
  0xF8000008 0xDF0D; mww phys 0xF8000200 1`) + `uboot-intercept.py` hammering 'd'.
