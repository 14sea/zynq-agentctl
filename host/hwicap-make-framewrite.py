#!/usr/bin/env python3
"""Build a single-frame ICAP write sequence by extracting the target frame from the
RAW bitstream FDRI word stream (task #8 T2.2). Companion to hwicap-uart.py.

IMPORTANT: extract from the raw .bit, NOT from a prjxray .bits reconstruction -- the
.bits model omits/!=  the per-frame ECC word (word 50) and any bits prjxray doesn't
model, so a reconstructed frame writes wrong data. icap-build-frame.py extracts raw;
so do we.

Method (controlled diff at the raw-word level):
  - read A.bit and B.bit config-word streams (from the AA995566 sync).
  - the two differ by exactly one LUT INIT bit -> among differing words, the INIT word
    is the one whose xor is a single bit; (a 2nd differing word is the frame ECC).
  - prjxray names the INIT at frame-internal word <wofs> (e.g. 73), so the frame starts
    <wofs> words before the INIT word. Extract that frame + its neighbour (202 words)
    from B (real content -> writing the pad frame F+1 cannot corrupt it).
  - wrap in a minimal 7-series WCFG/FDRI sequence, NO GRESTORE/GTS.

  hwicap-make-framewrite.py <A.bit> <B.bit> <FAR_hex> <wofs> <out.bin>
Emits big-endian uint32 words -> hwicap-uart.py writeseq <out.bin>
"""
import struct, sys

SYNC = b'\xaa\x99\x55\x66'
FRAME_WORDS = 101
IDCODE = 0x03722093          # xc7z010 config IDCODE


def config_words(path):
    b = open(path, 'rb').read()
    s = b.find(SYNC)
    n = (len(b) - s) // 4
    return list(struct.unpack('>%dI' % n, b[s:s + n * 4]))


def main():
    a, bb, far_hex, wofs, out = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5]
    far = int(far_hex, 16)
    WA, WB = config_words(a), config_words(bb)
    n = min(len(WA), len(WB))
    diffs = [(i, WA[i] ^ WB[i]) for i in range(n) if WA[i] != WB[i]]
    single = [i for i, x in diffs if x and (x & (x - 1)) == 0]   # single-bit xor = INIT
    print(f"differing config words: {[(hex(i), hex(WA[i])+'->'+hex(WB[i])) for i,_ in diffs]}")
    if not single:
        sys.exit("no single-bit (INIT) differing word found")
    initw = single[0]
    start = initw - wofs
    frames2 = WB[start:start + 2 * FRAME_WORDS]
    assert len(frames2) == 2 * FRAME_WORDS, "frame extraction out of range"
    print(f"INIT word idx {initw}, frame start idx {start}, "
          f"frame[{wofs}] A=0x{WA[initw]:08x} B=0x{WB[initw]:08x}")

    seq = [0xFFFFFFFF] * 8 + [
        0xAA995566,                     # sync
        0x20000000,                     # NOP
        0x30008001, 0x00000007,         # CMD = RCRC
        0x20000000, 0x20000000,         # NOP x2
        0x30018001, IDCODE,             # write IDCODE
        0x30008001, 0x00000001,         # CMD = WCFG
        0x20000000,                     # NOP
        0x30002001, far,                # FAR = target frame
        0x30004000,                     # FDRI type1, 0 words (type2 follows)
        0x50000000 | (2 * FRAME_WORDS), # type2 write 202 words
    ] + list(frames2) + [
        0x30000001, 0x00000000,         # write CRC reg = 0 (CRC disabled)
        0x30008001, 0x0000000D,         # CMD = DESYNC
        0x20000000, 0x20000000, 0x20000000, 0x20000000,
    ]
    open(out, 'wb').write(struct.pack('>%dI' % len(seq), *seq))
    print(f"wrote {len(seq)} words -> {out}  (FAR=0x{far:08x})")


if __name__ == '__main__':
    main()
