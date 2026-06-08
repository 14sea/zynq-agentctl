#!/usr/bin/env python3
"""Build a MULTI-frame ICAP write sequence to retune the RO tuning LUT, by taking
the differing frames' data from the RAW target .bit (zynq-agentctl F0.2).

Phase-1's hwicap-make-framewrite was single-frame (enough for 1 INIT bit). A
LUT6's 64 INIT bits are spread across several config frames in 7-series, so
retuning the ring-oscillator tuning LUT (tap select) touches multiple frames.
For ro_tune's tuning LUT (SLICE_X22Y50) the tap0<->tap5 change lives in word[0]
of 4 CONSECUTIVE frames (FAR 0x1420..0x1423, found via prjxray bitread), so a
single FAR + FDRI auto-increment write of those frames (+1 flush pad) does it.

Frame DATA comes from the RAW .bit FDRI stream (NOT prjxray .bits, which omits
the per-frame ECC word) -- same rule as hwicap-make-framewrite.

  lut-tune.py <from.bit> <to.bit> <start_far_hex> <out.bin>
emits big-endian uint32 words -> icaphw writeseq <out.bin>
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
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    a, bb, far_hex, out = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    start_far = int(far_hex, 16)
    WA, WB = config_words(a), config_words(bb)
    n = min(len(WA), len(WB))
    diffs = [i for i in range(n) if WA[i] != WB[i]]
    if not diffs:
        sys.exit("no differing config words -- from/to identical?")

    frame_start = min(diffs)
    last = max(diffs)
    n_target = (last - frame_start) // FRAME_WORDS + 1
    # sanity: differing words must lie on the same word-in-frame (here word 0)
    # and span a small, contiguous frame range -- a guard against writing junk.
    span = last - frame_start
    if n_target > 8:
        sys.exit(f"refusing: diff spans {n_target} frames (>8); not a confined LUT edit")
    n_frames = n_target + 1   # +1 flush pad commits the last target frame
    frames = WB[frame_start:frame_start + n_frames * FRAME_WORDS]
    assert len(frames) == n_frames * FRAME_WORDS, "frame extraction out of range"

    print(f"diff words: {len(diffs)} at raw idx {[hex(d) for d in diffs]}")
    print(f"frame_start raw idx {frame_start}, target frames {n_target}, "
          f"+1 pad = {n_frames}; start FAR 0x{start_far:08x}")

    seq = [0xFFFFFFFF] * 8 + [
        0xAA995566,                          # sync
        0x20000000,                          # NOP
        0x30008001, 0x00000007,              # CMD = RCRC
        0x20000000, 0x20000000,
        0x30018001, IDCODE,                  # write IDCODE
        0x30008001, 0x00000001,              # CMD = WCFG
        0x20000000,
        0x30002001, start_far,               # FAR = first target frame
        0x30004000,                          # FDRI type1, 0 words (type2 follows)
        0x50000000 | (n_frames * FRAME_WORDS),  # type2 write n_frames*101 words
    ] + list(frames) + [
        0x30000001, 0x00000000,              # write CRC reg = 0 (CRC disabled)
        0x30008001, 0x0000000D,              # CMD = DESYNC
        0x20000000, 0x20000000, 0x20000000, 0x20000000,
    ]
    open(out, 'wb').write(struct.pack('>%dI' % len(seq), *seq))
    print(f"wrote {len(seq)} words -> {out}")


if __name__ == '__main__':
    main()
