#!/usr/bin/env python3
"""Build a MULTI-frame ICAP write seq that sets the RO tuning LUT to a target tap,
writing the tuning LUT's whole INIT-frame region from the target .bit
(zynq-agentctl P1).

A LUT6's 64 INIT bits are spread across several consecutive config frames in
7-series (for ro_tune's tune_lut @SLICE_X22Y50: 4 frames, FAR 0x1420..0x1423,
word[0], found via prjxray bitread). To SET an arbitrary tap robustly (from any
current state), we must write ALL those frames unconditionally with the target's
content -- a plain from->to diff is unsafe because two taps can coincidentally
share a frame (e.g. tap0==tap4 in frame 0x1420), which would skip/misalign it.

So the INIT-frame region is located ONCE from two anchor bitstreams that are
guaranteed to differ in every INIT frame (tap0 vs tap5), then the frames are
taken from <target.bit> (RAW .bit FDRI data -- NOT prjxray .bits, which omits the
per-frame ECC word).

  lut-tune.py <target.bit> <anchor_a.bit> <anchor_b.bit> <start_far_hex> <out.bin>
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
    if len(sys.argv) != 6:
        sys.exit(__doc__)
    target, anch_a, anch_b, far_hex, out = sys.argv[1:6]
    start_far = int(far_hex, 16)

    # locate the INIT-frame region from the two anchors (must differ in all frames)
    WA, WB = config_words(anch_a), config_words(anch_b)
    n = min(len(WA), len(WB))
    diffs = [i for i in range(n) if WA[i] != WB[i]]
    if not diffs:
        sys.exit("anchors identical -- pick two taps that differ (e.g. tap0/tap5)")
    frame_start = min(diffs)
    n_target = (max(diffs) - frame_start) // FRAME_WORDS + 1
    if n_target > 8:
        sys.exit(f"refusing: anchors differ over {n_target} frames (>8)")
    n_frames = n_target + 1          # +1 flush pad commits the last target frame

    WT = config_words(target)
    frames = WT[frame_start:frame_start + n_frames * FRAME_WORDS]
    assert len(frames) == n_frames * FRAME_WORDS, "frame extraction out of range"
    print(f"INIT region: raw idx {frame_start}, {n_target} frames +1 pad, "
          f"FAR 0x{start_far:08x}; data from {target}")

    seq = [0xFFFFFFFF] * 8 + [
        0xAA995566,
        0x20000000,
        0x30008001, 0x00000007,              # RCRC
        0x20000000, 0x20000000,
        0x30018001, IDCODE,
        0x30008001, 0x00000001,              # WCFG
        0x20000000,
        0x30002001, start_far,               # FAR
        0x30004000,                          # FDRI type1, 0 words
        0x50000000 | (n_frames * FRAME_WORDS),  # type2 write
    ] + list(frames) + [
        0x30000001, 0x00000000,              # CRC = 0 (CRC disabled)
        0x30008001, 0x0000000D,              # DESYNC
        0x20000000, 0x20000000, 0x20000000, 0x20000000,
    ]
    open(out, 'wb').write(struct.pack('>%dI' % len(seq), *seq))
    print(f"wrote {len(seq)} words -> {out}")


if __name__ == '__main__':
    main()
