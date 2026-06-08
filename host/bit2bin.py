#!/usr/bin/env python3
"""Convert a Vivado .bit into a byte-swapped .bin for the Zynq Linux fpga_manager.

The Linux zynq-fpga driver (fpgautil A-route) rejects a raw .bit:
  "Invalid bitstream, could not find a sync word. Bitstream must be a byte
   swapped .bin file"
A .bit body carries the sync word as AA 99 55 66 (big-endian); the PCAP/.bin
format the driver expects is each 32-bit word byte-reversed, so the sync reads
66 55 99 AA. (The miner's top.bit happened to already be in this .bin order
because it was sliced out of BOOT.BIN, which is why fpgautil accepted it.)

Method: locate the 'e' field in the .bit header (4-byte big-endian length),
take the body, byte-swap every 32-bit word.

  bit2bin.py <in.bit> <out.bin>
"""
import struct, sys


def bit2bin(data: bytes) -> bytes:
    # find the 'e' field marker: a single 'e' byte followed by a uint32 length
    # that matches the remaining file size.
    for p in range(len(data) - 5):
        if data[p:p + 1] == b'e':
            ln = struct.unpack('>I', data[p + 1:p + 5])[0]
            if abs(ln - (len(data) - (p + 5))) < 8:
                body = data[p + 5:p + 5 + ln]
                n = len(body) // 4
                return struct.pack('<%dI' % n, *struct.unpack('>%dI' % n, body[:n * 4]))
    raise SystemExit("could not locate .bit 'e' data field")


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: bit2bin.py <in.bit> <out.bin>")
    out = bit2bin(open(sys.argv[1], 'rb').read())
    open(sys.argv[2], 'wb').write(out)
    s = out.find(bytes.fromhex('665599aa'))
    print(f"wrote {len(out)} bytes -> {sys.argv[2]}  (sync 66 55 99 aa at {s})")


if __name__ == '__main__':
    main()
