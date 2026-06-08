#!/usr/bin/env python3
"""frameguard — host-side mirror of icaphw's structural guard. Validates that an
ICAP write sequence only touches the sandbox tuning-LUT frames, so the agent can
pre-check a seq before sending (defense in depth; the board-side guard in
firmware/icaphw.c is the real enforcement -- the host cannot bypass it).

Refuses if the seq's FAR is outside [FAR_LO,FAR_HI) or the FDRI is too large.

  frameguard.py <seq.bin> [--far-lo 0x1420] [--far-hi 0x1424] [--max-fdri 606]
exit 0 = within sandbox, 3 = refused.
"""
import argparse, struct, sys


def words(path):
    b = open(path, "rb").read()
    return list(struct.unpack(">%dI" % (len(b) // 4), b[:len(b)//4*4]))


def check(seq, lo, hi, maxf):
    saw_far = False
    for i, w in enumerate(seq):
        if w == 0x30002001 and i + 1 < len(seq):
            far = seq[i + 1]; saw_far = True
            if not (lo <= far < hi):
                return f"FAR 0x{far:08x} outside sandbox [0x{lo:x},0x{hi:x})"
        if (w & 0xFF000000) == 0x50000000:
            fn = w & 0x00FFFFFF
            if fn > maxf:
                return f"FDRI {fn} words > max {maxf}"
    if not saw_far:
        return "no FAR write found in seq"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("seq")
    ap.add_argument("--far-lo", type=lambda x: int(x, 0), default=0x1420)
    ap.add_argument("--far-hi", type=lambda x: int(x, 0), default=0x1424)
    ap.add_argument("--max-fdri", type=int, default=606)
    a = ap.parse_args()
    err = check(words(a.seq), a.far_lo, a.far_hi, a.max_fdri)
    if err:
        print(f"[guard] REFUSED: {err}"); sys.exit(3)
    print(f"[guard] OK: {a.seq} confined to sandbox [0x{a.far_lo:x},0x{a.far_hi:x})")


if __name__ == "__main__":
    main()
