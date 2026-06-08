#!/usr/bin/env python3
"""MCP server exposing the zynq-agentctl physical arm as tools, so any Claude
session (or other MCP client) can perceive/decide/act on the EBAZ4205 ring
oscillator over UART -- the "agent controls live FPGA state" loop as composable
tools.

Wraps host/ro_adapt.py (which drives /tmp/icaphw on the board via /dev/ebaz-uart).
Board prereqs: ro_tune in PL (FCLK0 on via dtb fix), /tmp/icaphw + /tmp/set_tap0..5.bin
loaded (see docs/adapt.md). The board-side guard in firmware/icaphw.c confines all
ICAP writes to the tuning-LUT sandbox, so these tools cannot brick the fabric.

Run:  .env/bin/python mcp/server.py        (stdio MCP server)
Add to a client's .mcp.json -- see docs/mcp.md.
"""
import io, os, sys, contextlib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "host"))
import importlib
ro = importlib.import_module("ro_adapt")

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("zynq-agentctl")


def _run(fn):
    """run an ro_adapt op on a fresh serial connection, capturing its stdout
    (ro_adapt prints human logs to stdout; MCP needs stdout clean for protocol,
    so we capture and return the log instead). Returns (result, log_text)."""
    buf = io.StringIO()
    s = ro._open()
    try:
        with contextlib.redirect_stdout(buf):
            result = fn(s)
    finally:
        s.close()
    return result, buf.getvalue()


@mcp.tool()
def board_status() -> dict:
    """Check the board is reachable and the ring oscillator is alive (count != 0)."""
    try:
        count, log = _run(lambda s: ro.measure(s))
        return {"reachable": True, "ro_count": count, "alive": count not in (0, 0xFFFFFFFF)}
    except Exception as e:
        return {"reachable": False, "error": str(e)}


@mcp.tool()
def measure_freq() -> dict:
    """Read the current ring-oscillator frequency (count per ~524us window; higher = faster)."""
    count, _ = _run(lambda s: ro.measure(s))
    return {"count": count}


@mcp.tool()
def set_tap(tap: int) -> dict:
    """Set the RO tuning LUT to tap 0..5 via a multi-frame ICAP write (no reload),
    then report the resulting count. tap0 = fastest, tap5 = slowest."""
    if not 0 <= tap <= 5:
        return {"error": "tap must be 0..5"}
    def op(s):
        ro.set_tap(s, tap); return ro.measure(s)
    count, _ = _run(op)
    return {"tap": tap, "count": count}


@mcp.tool()
def sweep() -> dict:
    """Measure the count at every tap 0..5 -- the (PVT-dependent) frequency map."""
    m, log = _run(lambda s: ro.sweep(s))
    return {"map": {f"tap{k}": v for k, v in m.items()}, "log": log}


@mcp.tool()
def adapt_freq(target: int, tol: int = 1500) -> dict:
    """Tune the RO to a target count (+/- tol) by feedback search over the taps.
    The count<->tap map is discovered by measuring (it depends on die PVT), not
    assumed. Returns the converged tap, final count, and the search log."""
    (best, count, ok), log = _run(lambda s: ro.adapt(s, target, tol))
    return {"target": target, "tol": tol, "converged": ok,
            "tap": best, "count": count, "log": log}


if __name__ == "__main__":
    mcp.run()
