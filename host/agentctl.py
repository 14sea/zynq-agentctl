#!/usr/bin/env python3
"""agentctl — host-side command surface for the zynq-agentctl Form-A loop.

The agent (a Claude session on the host) drives the EBAZ4205 over UART. This
wrapper collapses the verified board flow into a few idempotent verbs so any
session — or a /loop — can run the perceive -> decide -> act -> verify loop
without re-deriving SLCR magic or HWICAP offsets.

Verbs:
  ensure-linux          get the board to a Buildroot Linux root shell
  setup                 push artifacts (if md5 differs) + fpgautil load + health
  perceive              read the LUT probe -> prints state 0/1
  act <0|1>             drive the LUT INIT[0] to the target state via ICAP edit
  verify <0|1>          perceive and compare to target -> exit 0 ok / 3 mismatch
  loop  <0|1>           act + verify with one retry (full closed loop)

All board mutation goes through /tmp/icaphw (this project's /dev/mem executor).
File pushes reuse uart-push-b64.py; recovery to U-Boot is out of scope here
(use openocd SLCR soft-reset + uboot-intercept.py, see docs/plan.md).
"""
import argparse, hashlib, os, re, subprocess, sys, time
import serial

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PORT = os.environ.get("EBAZ_UART", "/dev/ebaz-uart")
BAUD = 115200

# board paths. The design lives in NAND mtd5 (nand-bitstream); we dd it out and
# fpgautil-load it rather than pushing 2 MB over UART each session. The FCLK0
# fix (DTB fclk-enable=<9>, flashed to nand-device-tree) keeps the PL AXI clocked
# so fpgautil + /dev/mem actually work under Linux.
BIT = "/tmp/lut_A.bin"
MTD5_BLOCKS = 509  # 509*4096 = 2084864 = our lut_A.bin in mtd5
ICAPHW = "/tmp/icaphw"
SEQ_SET = "/tmp/seqAB.bin"   # INIT[0] 0 -> 1
SEQ_CLR = "/tmp/seqBA.bin"   # INIT[0] 1 -> 0

# host artifacts -> board dest
# Small artifacts pushed each session (tmpfs wiped on boot); the 2 MB bitstream
# is NOT here — it comes from mtd5 (see setup()).
ARTIFACTS = [
    (os.path.join(ROOT, "firmware/icaphw"), ICAPHW),
    (os.path.join(ROOT, "seq/seqAB.bin"), SEQ_SET),
    (os.path.join(ROOT, "seq/seqBA.bin"), SEQ_CLR),
]

PROMPT = b"# "


def open_port():
    return serial.Serial(PORT, BAUD, timeout=0.2)


def read_until(s, needle, timeout):
    buf, t0 = b"", time.time()
    while time.time() - t0 < timeout:
        c = s.read(512)
        if c:
            buf += c
            if needle in buf:
                break
    return buf


def sh(s, line, timeout=8.0):
    """run one shell command on the board, return stdout text (echo stripped)."""
    s.reset_input_buffer()
    s.write(line.encode() + b"\r")
    buf = read_until(s, PROMPT, timeout)
    txt = buf.decode(errors="replace")
    # drop the echoed command line and the trailing prompt
    lines = txt.splitlines()
    if lines and line in lines[0]:
        lines = lines[1:]
    return "\n".join(l for l in lines if l.strip() not in ("#", ""))


def at_prompt(s):
    s.reset_input_buffer()
    s.write(b"\r")
    return PROMPT in read_until(s, PROMPT, 2.0)


def ensure_linux():
    s = open_port()
    s.write(b"\r")
    r = read_until(s, b">", 2.5) + s.read(256)
    if b"zynq-uboot>" in r:
        print("[ensure-linux] at U-Boot, booting Linux ...")
        s.write(b"boot\r")
        read_until(s, b"buildroot login:", 60)
        s.write(b"root\r")
        read_until(s, PROMPT, 6)
        print("[ensure-linux] logged in")
    elif at_prompt(s):
        print("[ensure-linux] already at Linux shell")
    else:
        # maybe sitting at a login prompt
        s.write(b"root\r")
        if PROMPT in read_until(s, PROMPT, 6):
            print("[ensure-linux] logged in")
        else:
            print("[ensure-linux] UNKNOWN state — recover via U-Boot manually")
            s.close(); sys.exit(2)
    s.close()


def board_md5(s, path):
    out = sh(s, f"md5sum {path} 2>/dev/null")
    m = re.search(r"([0-9a-f]{32})", out)
    return m.group(1) if m else None


def host_md5(path):
    return hashlib.md5(open(path, "rb").read()).hexdigest()


def push(src, dest):
    print(f"[setup] push {os.path.basename(src)} -> {dest}")
    subprocess.run(
        [sys.executable, os.path.join(HERE, "uart-push-b64.py"),
         "--in", src, "--dest", dest, "--timeout", "400"],
        check=True)


def setup():
    s = open_port()
    if not at_prompt(s):
        s.close()
        print("[setup] not at Linux shell — run `ensure-linux` first"); sys.exit(2)
    # push only artifacts whose board md5 differs (idempotent)
    for src, dest in ARTIFACTS:
        want = host_md5(src)
        have = board_md5(s, dest)
        if have == want:
            print(f"[setup] {dest} up-to-date ({want[:8]})")
        else:
            s.close()
            push(src, dest)
            s = open_port()
    sh(s, f"chmod +x {ICAPHW}")
    print("[setup] dd design from mtd5 + fpgautil load ...")
    sh(s, f"dd if=/dev/mtd5 bs=4096 count={MTD5_BLOCKS} of={BIT} 2>/dev/null")
    out = sh(s, f"fpgautil -b {BIT}", timeout=20)
    print("  " + out.replace("\n", "\n  "))
    state = sh(s, "cat /sys/class/fpga_manager/fpga0/state")
    print(f"[setup] fpga_manager state = {state.strip()}")
    print("[setup] HWICAP health:")
    print("  " + sh(s, f"{ICAPHW} regs"))
    s.close()


def _probe(s):
    out = sh(s, f"{ICAPHW} gpio")
    m = re.search(r"bit0=(\d)", out)
    if not m:
        raise RuntimeError(f"perceive parse fail: {out!r}")
    return int(m.group(1))


def perceive():
    s = open_port()
    st = _probe(s)
    s.close()
    print(f"[perceive] LUT INIT[0] = {st}")
    return st


def act(target):
    seq = SEQ_SET if target == 1 else SEQ_CLR
    s = open_port()
    print(f"[act] driving LUT INIT[0] -> {target} (edit {seq})")
    out = sh(s, f"{ICAPHW} edit {seq}", timeout=15)
    print("  " + out.replace("\n", "\n  "))
    s.close()


def verify(target):
    st = perceive()
    if st == target:
        print(f"[verify] OK — state {st} == target {target}")
        return 0
    print(f"[verify] MISMATCH — state {st} != target {target}")
    return 3


def loop(target):
    """full closed loop: perceive -> (decide) -> act -> verify, one retry."""
    s = open_port(); cur = _probe(s); s.close()
    print(f"[loop] current={cur} target={target}")
    if cur == target:
        print("[loop] already at target; nothing to do")
        return 0
    for attempt in (1, 2):
        act(target)
        rc = verify(target)
        if rc == 0:
            print(f"[loop] converged in {attempt} attempt(s)")
            return 0
        print(f"[loop] attempt {attempt} did not converge, retrying ...")
    print("[loop] FAILED to converge")
    return 3


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="op", required=True)
    sub.add_parser("ensure-linux")
    sub.add_parser("setup")
    sub.add_parser("perceive")
    for v in ("act", "verify", "loop"):
        p = sub.add_parser(v); p.add_argument("target", type=int, choices=(0, 1))
    args = ap.parse_args()

    if args.op == "ensure-linux":
        ensure_linux()
    elif args.op == "setup":
        setup()
    elif args.op == "perceive":
        perceive()
    elif args.op == "act":
        act(args.target)
    elif args.op == "verify":
        sys.exit(verify(args.target))
    elif args.op == "loop":
        sys.exit(loop(args.target))


if __name__ == "__main__":
    main()
