#!/usr/bin/env python3
"""Load a full/partial bitstream into the Zynq PL via U-Boot's `fpga` command,
shipping it over UART ymodem (loady + sb).  M1 helper; reused in Phase 3 for
partial bitstreams (--op loadbp).

Sequence:  loady <addr>  ->  sb -k <bit>  ->  fpga <op> 0 <addr> <size>
           [-> md <read> 4  twice, to watch a live PL register change]

Modeled on nand-flash.py's loady/sb handshake. Default port /dev/ebaz-uart.
"""
import argparse, os, re, subprocess, sys, time
import serial

READY_RE = re.compile(rb'Ready for binary|CC')
PROMPT   = re.compile(rb'zynq-uboot>\s*$')


def read_until(s, pat, timeout):
    buf, t0 = b'', time.time()
    while time.time() - t0 < timeout:
        chunk = s.read(512)
        if chunk:
            buf += chunk
            if pat.search(buf):
                break
    return buf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', default='/dev/ebaz-uart')
    ap.add_argument('--bit', required=True)
    ap.add_argument('--addr', default='0x4000000')
    ap.add_argument('--op', default='loadb', choices=['loadb', 'loadbp', 'load', 'loadp'])
    ap.add_argument('--read', help='hex AXI addr to md after load, e.g. 0x41200000')
    args = ap.parse_args()

    size = os.path.getsize(args.bit)
    addr = int(args.addr, 16)
    print(f"[*] {args.bit}: {size} bytes -> 0x{addr:08x}, then `fpga {args.op}`", flush=True)

    # 1. tell U-Boot to receive ymodem
    s = serial.Serial(args.port, 115200, timeout=0.3)
    s.write(b'\r'); time.sleep(0.3); s.reset_input_buffer()
    s.write(f"loady 0x{addr:08x}\r".encode())
    read_until(s, READY_RE, 6)
    s.close()

    # 2. ymodem transfer (~3 min @ 115200 for ~2 MB)
    print("[*] ymodem transfer via sb ...", flush=True)
    with open(args.port, 'r+b', buffering=0) as tty, open('/tmp/sb-fpga.log', 'wb') as log:
        rc = subprocess.run(['sb', '-k', args.bit], stdin=tty, stdout=tty, stderr=log)
    if rc.returncode != 0:
        sys.exit(f"sb failed rc={rc.returncode} (see /tmp/sb-fpga.log)")

    # 3. consume transfer-complete + prompt
    s = serial.Serial(args.port, 115200, timeout=0.3)
    tail = read_until(s, PROMPT, 20)
    print("[xfer]", tail[-160:].decode(errors='replace').strip())

    # 4. program the PL
    line = f"fpga {args.op} 0 0x{addr:08x} 0x{size:08x}\r"
    print(f"[*] {line.strip()}", flush=True)
    s.reset_input_buffer(); s.write(line.encode())
    print("[fpga]", read_until(s, PROMPT, 30).decode(errors='replace').strip())

    # 5. read a PL register twice -> a live counter shows different values
    if args.read:
        for i in range(2):
            s.reset_input_buffer()
            s.write(f"md {args.read} 4\r".encode())
            print(f"[md#{i}]", read_until(s, PROMPT, 5).decode(errors='replace').strip())
            time.sleep(0.8)
    s.close()


if __name__ == '__main__':
    main()
