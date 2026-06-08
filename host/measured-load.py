#!/usr/bin/env python3
"""M5 -- measured-boot trust anchor (PS-side).

Hash-gate a bitstream against an allowlist BEFORE loading it. A full/partial
bitstream whose SHA-256 is not in the allowlist is REFUSED -- so an unauthorized
edit (e.g. a hand-patched LUT partial from Phase 4) cannot reach the fabric. Only
allowlisted, "measured" images load. Continues the rot_tpu_handoff modes g/G/c
"CRC / whitelist -> load" pipeline, here run on the host orchestrating the PS.

On a passing measurement it hands off to uboot-fpga-load.py to do the actual
`fpga loadb|loadbp` over UART.

  measured-load.py --bit X.bit --op loadb|loadbp [--read 0x41200000]
                   [--allowlist board/allowlist.sha256]
"""
import argparse, hashlib, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 16), b''):
            h.update(chunk)
    return h.hexdigest()


def load_allowlist(path):
    allow = {}
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            p = line.split(None, 1)
            allow[p[0].lower()] = p[1] if len(p) > 1 else ''
    return allow


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bit', required=True)
    ap.add_argument('--op', default='loadbp', choices=['loadb', 'loadbp', 'load', 'loadp'])
    ap.add_argument('--read')
    ap.add_argument('--port', default='/dev/ebaz-uart')
    ap.add_argument('--allowlist', default=os.path.join(HERE, '..', 'board', 'allowlist.sha256'))
    args = ap.parse_args()

    digest = sha256(args.bit)
    allow = load_allowlist(args.allowlist)
    print(f"[measure] sha256({os.path.basename(args.bit)}) = {digest}")
    if digest in allow:
        print(f"[trust]   OK -- allowlisted as '{allow[digest]}'. Proceeding to load.")
    else:
        print(f"[trust]   REJECTED -- not in allowlist ({len(allow)} trusted entries).")
        print("[trust]   Refusing to load an unmeasured/tampered bitstream.")
        sys.exit(2)

    cmd = [sys.executable, os.path.join(HERE, 'uboot-fpga-load.py'),
           '--bit', args.bit, '--op', args.op, '--port', args.port]
    if args.read:
        cmd += ['--read', args.read]
    sys.exit(subprocess.call(cmd))


if __name__ == '__main__':
    main()
