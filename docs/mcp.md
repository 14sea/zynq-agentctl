# P4 — the physical arm as MCP tools

`mcp/server.py` exposes the EBAZ4205 ring-oscillator adaptation loop as MCP tools,
so any MCP client (another Claude session, an agent, an IDE) can perceive/decide/
act on live FPGA fabric over UART — the "agent controls hardware" loop, composable.

## Tools
- `board_status()` — board reachable? RO alive (count != 0)?
- `measure_freq()` — current RO count (higher = faster).
- `set_tap(tap)` — ICAP-set tuning LUT to tap 0..5 (no reload); returns new count.
- `sweep()` — count at every tap (the PVT-dependent frequency map).
- `adapt_freq(target, tol=1500)` — feedback search to a target count; returns the
  converged tap + count + search log. The map is discovered by measuring, not assumed.

Every ICAP write goes through the board-side guard (`firmware/icaphw.c`), which
confines writes to the tuning-LUT sandbox — the tools cannot brick the fabric.

## Setup
```bash
cd /home/test/zynq_agentctl
python3 -m venv .env && .env/bin/pip install mcp pyserial
# board prereqs (see docs/adapt.md): ro_tune in PL + /tmp/icaphw + /tmp/set_tap0..5.bin
```

## Use from Claude Code
`.mcp.json` (project root) registers the server:
```json
{ "mcpServers": { "zynq-agentctl": {
    "command": "/home/test/zynq_agentctl/.env/bin/python",
    "args": ["/home/test/zynq_agentctl/mcp/server.py"] } } }
```
Then a session can call e.g. `adapt_freq(target=95000)` and watch the board converge.

## Smoke test
```bash
.env/bin/python mcp/test_client.py
# lists tools, then board_status / measure_freq / adapt_freq against the live board
```
Verified (2026-06-08): tools list OK; `adapt_freq(95000)` → converged tap2, count 95025.

## Notes
- MCP stdio uses stdout for the protocol; the server captures `ro_adapt`'s human
  logs (which print to stdout) and returns them in the tool result instead.
- The server drives `/dev/ebaz-uart`; only one client/agent should hold the board
  at a time.
