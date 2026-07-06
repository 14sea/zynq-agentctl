# zynq-agentctl

An agent (Claude) controlling the **live electrical state of an FPGA** on the
EBAZ4205 (Xilinx XC7Z010) — rewriting a PL LUT truth table at runtime via ICAP
LUT-INIT surgery, **no reset**, as a closed perceive → decide → act → verify loop.

This is **Form A**: the agent runs on the host (WSL); the board is the actuator,
driven over UART. The host-side Claude session *is* the agent. Form B (agent
running on-board + networking over UART-PPP) is a roadmap only — see `docs/plan.md`.

## License

Apache-2.0 (see `LICENSE` / `NOTICE`). NEORV32 (BSD-3) and prjxray/Vivado are external tools (fetched/used, not vendored).

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

## Phases & docs

The loop was built up in phases, all hardware-verified on the EBAZ4205:

- **Live LUT-INIT edit (P1)** — the *fine* action: rewrite one LUT6 INIT bit over
  ICAP, no reset. `agentctl.py act/loop` — [docs/loop.md](docs/loop.md).
- **Adaptive RO-frequency loop (Phase-2 P1)** — a tunable ring oscillator; the agent
  drives its frequency to a target by measuring and searching tap settings with **no
  pre-known map**, plus drift self-repair via `watch`. `host/ro_adapt.py` —
  [docs/adapt.md](docs/adapt.md).
- **Safety guard (Phase-2 P2)** — two enforcement levels, don't conflate them:
  the *fine* path (ICAP LUT edits) is guarded **structurally, board-side**:
  `firmware/icaphw.c` refuses any ICAP write outside the sandbox frames
  (`host/frameguard.py` is an optional host-side pre-check mirror, not in the
  automatic path). sha256 **measured-load gates**: `board/dfx_allowlist.sha256`
  is enforced automatically by `agentctl.py load-module` (unlisted partials are
  REFUSED); `board/allowlist.sha256` and `board/ro_allowlist.sha256` are
  allowlists for *full* bitstream loads, checked via the standalone
  `host/measured-load.py --allowlist <file>` (opt-in tool, nothing invokes it
  implicitly).
- **DFX coarse hot-swap (P3)** — the *coarse* action: swap a whole reconfigurable
  module via Linux partial reconfig. `agentctl.py load-module` — [docs/dfx.md](docs/dfx.md).
- **MCP server (Phase-2 P4)** — exposes the arm (`board_status` / `measure_freq` /
  `set_tap` / `adapt_freq`) as MCP tools so any client can drive it. `mcp/server.py`,
  `.mcp.json` — [docs/mcp.md](docs/mcp.md).

> The drift self-repair is exercised via **fault injection** (a forced tap change):
> a real *thermal* swing can't be induced on this RO safely — cooling an idle die has
> no headroom and PS CPU-load heat doesn't reach the PL RO site (only direct chip
> heating would). The repair control loop is identical either way.

Roadmap & full plan: [docs/plan.md](docs/plan.md).

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
- `host/ro_adapt.py` — adaptive RO loop (`sweep`/`measure`/`set`/`adapt`/`watch`);
  `host/lut-tune.py` builds the per-tap multi-frame ICAP set-sequences.
- `host/frameguard.py` / `host/measured-load.py` — host-side safety mirrors
  (frame-range guard pre-check + sha256 allowlist gate).
- `rtl/ro_tune.v`, `vivado/ro_tune/` — the tunable ring-oscillator design + per-tap
  bitstream generation (`gen_taps.tcl`).
- `mcp/server.py`, `mcp/test_client.py`, `.mcp.json` — MCP wrapper exposing the loop as tools.
- `host/uart-*.py`, `host/uboot-intercept.py` — UART plumbing + recovery (copied from xilinx bring-up).
- `board/lut_A.bit` / `lut_B.bit` — HWICAP+GPIO demonstrator bitstreams (INIT[0]=0 / =1).
- `seq/seqAB.bin` / `seqBA.bin` — generated frame write sequences (INIT 0↔1).

## Fresh clone: reproducing the untracked artifacts

This repo is **source-only**: bitstreams, byte-swapped `.bin`s, frame sequences
and Vivado build outputs are gitignored (see `.gitignore`). A fresh clone runs
the host tools but has no board payloads until you regenerate them:

| Artifact (gitignored)              | Regenerate with |
|------------------------------------|-----------------|
| `firmware/icaphw`                  | `make -C firmware` (default `CC` = the ebaz4205-bringup Buildroot toolchain; override with `CC=<your-armv7-linux-gcc>`) |
| `board/lut_A.bit` / `lut_B.bit`    | Vivado on `zynq_xpart/vivado/hwicap_lut` (external repo — the HWICAP+GPIO demonstrator lives there) |
| `board/lut_A.bin`                  | `host/bit2bin.py board/lut_A.bit board/lut_A.bin` |
| `seq/seqAB.bin` / `seqBA.bin`      | `host/hwicap-make-framewrite.py` from `lut_A.bit`/`lut_B.bit` |
| `vivado/ro_tune/build/tap0..5.bit` | two steps: `vivado -mode batch -source vivado/ro_tune/build_ro_tune.tcl` (creates + routes `build/ro_tune.xpr`; RTL is in-repo: `rtl/ro_tune.v`), **then** `vivado -mode batch -source vivado/ro_tune/gen_taps.tcl` (re-opens that project, stamps the 6 tap INITs) |
| `seq/set_tap0..5.bin`              | `for k in 0 1 2 3 4 5; do host/lut-tune.py vivado/ro_tune/build/tap$k.bit vivado/ro_tune/build/tap0.bit vivado/ro_tune/build/tap5.bit 0x1420 seq/set_tap$k.bin; done` — `0x1420` is the tune_lut INIT-frame FAR of the *shipped* placement; a re-placed rebuild needs its new FAR (locate once via prjxray `bitread`; not a runtime dependency of the script) |
| `board/dfx/*` (static + partials)  | `vivado/dfx/build_dfx.tcl` — **depends on RTL from the sibling `zynq_xpart` repo** (github.com/14sea/zynq-xpart); clone it next to this repo first |

Board-side prerequisites are pushed over UART per session (`/tmp` is tmpfs):
`agentctl.py setup` pushes the fine-path set (`icaphw`, `seqAB.bin`, `seqBA.bin`)
automatically; the RO/MCP path needs a manual push of `icaphw` +
`seq/set_tap0..5.bin` — see `docs/adapt.md` "Per-session board prep" for the
exact commands. Hashes of known-good full bitstreams are recorded in
`board/allowlist.sha256` / `board/ro_allowlist.sha256` so regenerated ones can
be verified with `host/measured-load.py`.

## Provenance

Copied (not linked) from `xilinx/` (bring-up) and `zynq_xpart/` (ICAP work).
Those projects are never modified from here. Recovery anchor unchanged: openocd
SLCR soft-reset + UART `'d'` → miner U-Boot.
