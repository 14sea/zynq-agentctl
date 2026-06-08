#!/usr/bin/env python3
"""ro_adapt — the P1 adaptive loop: an agent tunes the ring oscillator's frequency
to a target by reading it back and searching the tap settings via ICAP, with NO
pre-known count<->tap map (it depends on this die's process + temperature, so the
agent MUST measure). Form A: runs on the host, drives the board over UART.

Board prereqs (loaded by F0/setup): ro_tune design in PL (FCLK0 on via the dtb
fix), /tmp/icaphw (the /dev/mem HWICAP executor), /tmp/set_tap0..5.bin (the
multi-frame ICAP "set tap k" sequences from host/lut-tune.py).

  ro_adapt.py sweep                 measure count at every tap (the freq map)
  ro_adapt.py measure               read the current RO count
  ro_adapt.py set <k>               ICAP-set tap k, report count
  ro_adapt.py adapt <target> [tol]  search taps to hit target count (+/- tol)
  ro_adapt.py watch <target> [tol]  hold target; re-adapt when it drifts out
"""
import argparse, re, statistics, sys, time
import serial

PORT = "/dev/ebaz-uart"
BAUD = 115200
ICAPHW = "/tmp/icaphw"
NTAP = 6
PROMPT = b"# "


def _open():
    return serial.Serial(PORT, BAUD, timeout=0.2)


def _cmd(s, line, timeout=6.0):
    s.reset_input_buffer()
    s.write(line.encode() + b"\r")
    buf, t0 = b"", time.time()
    while time.time() - t0 < timeout:
        c = s.read(256)
        if c:
            buf += c
            if buf.rstrip().endswith(b"#"):
                break
    return buf.decode(errors="replace")


def _count(s):
    """one RO count read (AXI-GPIO ch1)."""
    out = _cmd(s, f"{ICAPHW} gpio")
    m = re.search(r"=\s*0x([0-9a-fA-F]{8})", out)
    if not m:
        raise RuntimeError(f"count parse fail: {out!r}")
    return int(m.group(1), 16)


def measure(s, n=4):
    """median of n reads; drop the first (may be a stale window)."""
    vals = [_count(s) for _ in range(n)]
    return int(statistics.median(vals[1:] if n > 1 else vals))


def set_tap(s, k):
    """ICAP-write tap k (PCAP_PR=0 -> multi-frame writeseq -> PCAP_PR=1)."""
    _cmd(s, f"{ICAPHW} edit /tmp/set_tap{k}.bin", timeout=10)
    _count(s)              # one throwaway read so the next window reflects tap k


def sweep(s):
    m = {}
    for k in range(NTAP):
        set_tap(s, k)
        m[k] = measure(s)
        print(f"  tap{k}: count={m[k]}")
    return m


def adapt(s, target, tol):
    """binary search over the (monotonically decreasing) count-vs-tap curve.
    Prints each perceive->decide->act step; converges to the tap closest to target."""
    print(f"[adapt] target={target} +/-{tol}")
    lo, hi, best = 0, NTAP - 1, None
    seen = {}
    while lo <= hi:
        mid = (lo + hi) // 2
        set_tap(s, mid)
        c = measure(s)
        seen[mid] = c
        err = c - target
        print(f"  perceive tap{mid}: count={c} (err={err:+d}) -> "
              f"{'within tol' if abs(err) <= tol else ('too fast, raise tap' if err > 0 else 'too slow, lower tap')}")
        if best is None or abs(c - target) < abs(seen[best] - target):
            best = mid
        if abs(err) <= tol:
            break
        if err > 0:      # count too high (freq too fast) -> longer loop -> higher tap
            lo = mid + 1
        else:            # too slow -> lower tap
            hi = mid - 1
    # land on the best-seen tap
    set_tap(s, best)
    c = measure(s)
    ok = abs(c - target) <= tol
    print(f"[adapt] {'CONVERGED' if ok else 'closest'}: tap{best} count={c} "
          f"(err={c-target:+d}); {len(seen)} probes")
    return best, c, ok


def watch(s, target, tol, period=3.0):
    print(f"[watch] holding count~{target} +/-{tol}; re-adapt on drift. Ctrl-C to stop.")
    best, _, _ = adapt(s, target, tol)
    while True:
        time.sleep(period)
        c = measure(s)
        if abs(c - target) > tol:
            print(f"[watch] DRIFT: count={c} (err={c-target:+d}) out of tol -> re-adapting")
            best, _, _ = adapt(s, target, tol)
        else:
            print(f"[watch] ok: tap{best} count={c} (err={c-target:+d})")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="op", required=True)
    sub.add_parser("sweep"); sub.add_parser("measure")
    p = sub.add_parser("set"); p.add_argument("k", type=int)
    for v in ("adapt", "watch"):
        q = sub.add_parser(v); q.add_argument("target", type=int)
        q.add_argument("tol", type=int, nargs="?", default=1500)
    args = ap.parse_args()
    s = _open()
    if args.op == "sweep":
        sweep(s)
    elif args.op == "measure":
        print(f"count = {measure(s)}")
    elif args.op == "set":
        set_tap(s, args.k); print(f"tap{args.k}: count={measure(s)}")
    elif args.op == "adapt":
        b, c, ok = adapt(s, args.target, args.tol); sys.exit(0 if ok else 3)
    elif args.op == "watch":
        try: watch(s, args.target, args.tol)
        except KeyboardInterrupt: print("\n[watch] stopped")
    s.close()


if __name__ == "__main__":
    main()
