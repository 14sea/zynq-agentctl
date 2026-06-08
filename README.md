# zynq-agentctl

An agent (Claude) controlling the **live electrical state of an FPGA** on the
EBAZ4205 (Xilinx XC7Z010) — rewriting a PL LUT truth table at runtime via ICAP
LUT-INIT surgery, **no reset**, as a closed perceive → decide → act → verify loop.

This is **Form A**: the agent runs on the host (WSL); the board is the actuator,
driven over UART. The host-side Claude session *is* the agent. Form B (agent
running on-board + networking over UART-PPP) is a roadmap only — see `docs/plan.md`.

## Architecture

```
 host (WSL) ── /dev/ebaz-uart ──> EBAZ4205 Buildroot Linux
   agent (Claude)                   |
   host/agentctl.py  ── commands ─> /tmp/icaphw  (this project's executor)
                                      |  /dev/mem mmap
                                      v
                  DEVCFG.PCAP_PR=0  ->  AXI HWICAP @0x41400000  ->  ICAPE2
                                      |                                |
                                      |  writes one CRAM frame         v
                                      +-- AXI-GPIO @0x41200000 <- LUT6 INIT[0]  (the observable)
```

Executor path = **Linux `/dev/mem` + HWICAP** (no A9 AMP, no soft-core in the loop).

## The one-line ICAP recipe (from zynq_xpart T2.2)

1. Hand the config-engine MUX to ICAP: clear `devcfg.CTRL[PCAP_PR]` bit27 at
   `0xF8007000` (`0x4c00e07f` → `0x4400e07f`).
2. Stream a minimal WCFG/FDRI single-frame sequence into AXI HWICAP (no GRESTORE).
3. Restore `PCAP_PR=1` (`0x4c00e07f`). The edit persists.

`icap_clk` must be wired on `axi_hwicap` (it is, in `lut_A/B.bit`).

## Layout

- `firmware/icaphw.c` — board-side `/dev/mem` HWICAP executor (the only from-scratch code).
- `host/agentctl.py` — host command surface: `ensure-linux` / `setup` / `perceive` /
  `act` / `verify` / `loop` (fine: ICAP LUT-INIT edit) + `load-module` (coarse:
  whole-RP hot-swap via Linux partial reconfig — P3, see `docs/dfx.md`).
- `board/dfx/`, `vivado/dfx/`, `board/dfx_allowlist.sha256` — P3 DFX coarse action:
  reuse the zynq_xpart DFX static (NEORV32 + AXI-GPIO mailbox) + two RP modules
  (`rm1_tpu`↔`rm2_alt`); `fpgautil -f Partial` swaps them live (~19 ms, no reset),
  allowlist-gated. **Linux partial reconfig verified working** — no U-Boot fallback needed.
- `host/hwicap-make-framewrite.py` — builds the single-frame write seq from `lut_A.bit`/`lut_B.bit`.
- `host/bit2bin.py` — convert a Vivado `.bit` to the byte-swapped `.bin` the Linux
  `fpga_manager` requires (it rejects a raw `.bit`: "must be a byte swapped .bin file").
  `board/lut_A.bin` is produced from `board/lut_A.bit` this way.
- `host/uart-*.py`, `host/uboot-intercept.py` — UART plumbing + recovery (copied from xilinx bring-up).
- `board/lut_A.bit` / `lut_B.bit` — HWICAP+GPIO demonstrator bitstreams (INIT[0]=0 / =1).
- `seq/seqAB.bin` / `seqBA.bin` — generated frame write sequences (INIT 0↔1).

## Provenance

Copied (not linked) from `xilinx/` (bring-up) and `zynq_xpart/` (ICAP work).
Those projects are never modified from here. Recovery anchor unchanged: openocd
SLCR soft-reset + UART `'d'` → miner U-Boot.
