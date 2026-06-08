/* icaphw — board-side /dev/mem HWICAP executor for EBAZ4205 (XC7Z010).
 *
 * Drives a live ICAP LUT-INIT edit from a *running Linux* (Form A executor for
 * the zynq-agentctl project). This is the /dev/mem port of zynq_xpart's
 * hwicap-uart.py, which proved the same write under miner U-Boot (T2.2). The new
 * variable here is doing it under the active xilinx-devcfg / fpga_manager driver.
 *
 * Register map (default base 0x41400000, AXI HWICAP):
 *   WF=0x100 RF=0x104 SZ=0x108 CR=0x10C SR=0x110 WFV=0x114 RFO=0x118 ASR=0x11C
 *   CR bit0=Write(WF->ICAP)  SR bit0=DONE  SR bit2=EOS
 * GPIO probe:  AXI-GPIO input @0x41200000, bit0 = target LUT6 INIT[0].
 * Config-engine MUX: devcfg.CTRL @0xF8007000, PCAP_PR = bit27 (0x08000000).
 *   clear -> ICAP owns the engine; set -> PCAP owns it (restore after).
 *
 * The writeseq input is a big-endian uint32 stream (same format hwicap-uart.py
 * `writeseq` consumes), produced by host/hwicap-make-framewrite.py.
 *
 * Subcommands:
 *   gpio                 print GPIO data word (perceive)
 *   regs                 print SR/WFV/CR/ASR/RFO (health: SR=0x5, WFV=0x3f)
 *   pcap-pr <0|1>        clear/set DEVCFG.CTRL[PCAP_PR] (read-modify-write)
 *   writeseq <file.bin>  stream a BE uint32 .bin into WF (WFV-chunked CR.Write)
 *   edit <file.bin>      gpio(before) -> pcap-pr 0 -> writeseq -> pcap-pr 1 -> gpio(after)
 */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/mman.h>
#include <time.h>

#define HWICAP_BASE 0x41400000UL
#define GPIO_BASE   0x41200000UL
#define DEVCFG_BASE 0xF8007000UL
#define PAGE        0x1000UL

/* HWICAP register byte offsets within its page */
#define R_WF  0x100
#define R_RF  0x104
#define R_SZ  0x108
#define R_CR  0x10C
#define R_SR  0x110
#define R_WFV 0x114
#define R_RFO 0x118
#define R_ASR 0x11C

#define CR_WRITE 0x1u
#define PCAP_PR_BIT (1u << 27)

static volatile uint32_t *hw;     /* HWICAP page base */
static volatile uint32_t *gpio;   /* GPIO page base   */
static volatile uint32_t *devcfg; /* DEVCFG page base (CTRL at offset 0) */

static inline uint32_t rd(volatile uint32_t *base, unsigned off) {
    return base[off / 4];
}
static inline void wr(volatile uint32_t *base, unsigned off, uint32_t v) {
    base[off / 4] = v;
}

static volatile uint32_t *map_page(int fd, unsigned long phys) {
    void *p = mmap(NULL, PAGE, PROT_READ | PROT_WRITE, MAP_SHARED, fd,
                   (off_t)phys);
    if (p == MAP_FAILED) {
        perror("mmap");
        exit(2);
    }
    return (volatile uint32_t *)p;
}

/* poll CR until it self-clears (write/read complete); returns 1 ok, 0 timeout */
static int wait_cr_clear(double timeout_s) {
    struct timespec t0, now;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    for (;;) {
        if (rd(hw, R_CR) == 0)
            return 1;
        clock_gettime(CLOCK_MONOTONIC, &now);
        double dt = (now.tv_sec - t0.tv_sec) +
                    (now.tv_nsec - t0.tv_nsec) / 1e9;
        if (dt > timeout_s)
            return 0;
        usleep(200);
    }
}

static void dump_regs(void) {
    printf("SR=0x%08x  WFV=0x%08x  CR=0x%08x  ASR=0x%08x  RFO=0x%08x\n",
           rd(hw, R_SR), rd(hw, R_WFV), rd(hw, R_CR),
           rd(hw, R_ASR), rd(hw, R_RFO));
}

/* set (val!=0) or clear (val==0) PCAP_PR; prints CTRL before/after */
static void set_pcap_pr(int val) {
    uint32_t c = rd(devcfg, 0);
    uint32_t n = val ? (c | PCAP_PR_BIT) : (c & ~PCAP_PR_BIT);
    wr(devcfg, 0, n);
    uint32_t v = rd(devcfg, 0);
    printf("[pcap-pr=%d] CTRL 0x%08x -> 0x%08x (read 0x%08x)\n",
           val, c, n, v);
}

/* stream BE uint32 words from buf into WF, WFV-chunked, CR.Write per chunk.
   The board's HWICAP write FIFO is 64-deep; we never push more than WFV vacancy
   before triggering, then wait for CR to clear (mirrors hwicap-uart.py wf_write). */
static int wf_write(const uint8_t *buf, size_t nbytes) {
    size_t nwords = nbytes / 4;
    size_t i = 0;
    while (i < nwords) {
        uint32_t vac = rd(hw, R_WFV) & 0xffff;
        if (vac == 0) {
            wait_cr_clear(2.0);
            vac = rd(hw, R_WFV) & 0xffff;
            if (vac == 0)
                vac = 1; /* still drain one at a time rather than spin forever */
        }
        size_t chunk = vac;
        if (chunk > nwords - i)
            chunk = nwords - i;
        for (size_t k = 0; k < chunk; k++) {
            const uint8_t *p = buf + (i + k) * 4;
            uint32_t w = ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) |
                         ((uint32_t)p[2] << 8) | (uint32_t)p[3];
            wr(hw, R_WF, w);
        }
        wr(hw, R_CR, CR_WRITE);
        if (!wait_cr_clear(2.0))
            fprintf(stderr, "  [warn] CR did not clear after chunk @word %zu\n", i);
        i += chunk;
    }
    return (int)i;
}

/* ---- structural safety guard: confine ICAP writes to the sandbox LUT frames ----
   A lut-tune.py seq has a Type1 FAR write (0x30002001, FAR in the next word) and
   a Type2 FDRI write (0x50xxxxxx, word count in low bits). We refuse any seq whose
   FAR is outside [FAR_LO,FAR_HI) or whose FDRI exceeds MAX_FDRI words, so the agent
   physically cannot rewrite CRAM outside the ring-oscillator tuning LUT's sandbox.
   Defaults = ro_tune sandbox; override via env ICAPHW_FAR_LO/HI (hex), ICAPHW_MAX_FDRI. */
static uint32_t be32(const uint8_t *p) {
    return ((uint32_t)p[0]<<24)|((uint32_t)p[1]<<16)|((uint32_t)p[2]<<8)|p[3];
}
static unsigned long env_u(const char *k, unsigned long d, int base) {
    const char *v = getenv(k); return v ? strtoul(v, 0, base) : d;
}
static int validate_seq(const uint8_t *buf, size_t nbytes) {
    unsigned long lo = env_u("ICAPHW_FAR_LO", 0x1420, 16);
    unsigned long hi = env_u("ICAPHW_FAR_HI", 0x1424, 16);   /* exclusive */
    unsigned long maxf = env_u("ICAPHW_MAX_FDRI", 606, 10);  /* 6 frames */
    size_t nwords = nbytes / 4; int saw_far = 0;
    for (size_t i = 0; i < nwords; i++) {
        uint32_t w = be32(buf + i*4);
        if (w == 0x30002001 && i+1 < nwords) {
            uint32_t far = be32(buf + (i+1)*4); saw_far = 1;
            if (!(far >= lo && far < hi)) {
                fprintf(stderr, "[guard] REFUSED: FAR 0x%08x outside sandbox [0x%lx,0x%lx)\n", far, lo, hi);
                return 0;
            }
        }
        if ((w & 0xFF000000u) == 0x50000000u) {              /* Type2 FDRI */
            uint32_t fn = w & 0x00FFFFFFu;
            if (fn > maxf) {
                fprintf(stderr, "[guard] REFUSED: FDRI %u words > max %lu\n", fn, maxf);
                return 0;
            }
        }
    }
    if (!saw_far) { fprintf(stderr, "[guard] REFUSED: no FAR write found in seq\n"); return 0; }
    return 1;
}

static uint8_t *slurp(const char *path, size_t *n) {
    FILE *f = fopen(path, "rb");
    if (!f) { perror(path); exit(2); }
    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (sz <= 0 || (sz % 4) != 0) {
        fprintf(stderr, "%s: size %ld not a positive multiple of 4\n", path, sz);
        exit(2);
    }
    uint8_t *b = malloc((size_t)sz);
    if (!b || fread(b, 1, (size_t)sz, f) != (size_t)sz) {
        fprintf(stderr, "%s: read failed\n", path);
        exit(2);
    }
    fclose(f);
    *n = (size_t)sz;
    return b;
}

static void usage(void) {
    fprintf(stderr,
        "usage: icaphw <gpio | regs | pcap-pr 0|1 | writeseq FILE | edit FILE>\n");
    exit(1);
}

int main(int argc, char **argv) {
    if (argc < 2)
        usage();

    int fd = open("/dev/mem", O_RDWR | O_SYNC);
    if (fd < 0) { perror("/dev/mem"); return 2; }
    hw     = map_page(fd, HWICAP_BASE);
    gpio   = map_page(fd, GPIO_BASE);
    devcfg = map_page(fd, DEVCFG_BASE);

    const char *op = argv[1];

    if (!strcmp(op, "gpio")) {
        uint32_t v = rd(gpio, 0);
        printf("GPIO 0x%08lx = 0x%08x  (bit0=%u)\n", GPIO_BASE, v, v & 1u);

    } else if (!strcmp(op, "regs")) {
        dump_regs();

    } else if (!strcmp(op, "pcap-pr")) {
        if (argc < 3) usage();
        set_pcap_pr(atoi(argv[2]) != 0);

    } else if (!strcmp(op, "writeseq")) {
        if (argc < 3) usage();
        size_t n; uint8_t *b = slurp(argv[2], &n);
        if (!validate_seq(b, n)) { free(b); return 3; }
        printf("[*] writeseq %zu words from %s\n", n / 4, argv[2]);
        dump_regs();
        int w = wf_write(b, n);
        printf("[*] wrote %d words\n", w);
        dump_regs();
        free(b);

    } else if (!strcmp(op, "edit")) {
        if (argc < 3) usage();
        size_t n; uint8_t *b = slurp(argv[2], &n);
        if (!validate_seq(b, n)) { free(b); return 3; }   /* guard before touching PCAP_PR */
        uint32_t before = rd(gpio, 0);
        printf("[*] edit %s: GPIO before = 0x%08x (bit0=%u)\n",
               argv[2], before, before & 1u);
        set_pcap_pr(0);                 /* hand config engine to ICAP */
        int w = wf_write(b, n);
        set_pcap_pr(1);                 /* restore PCAP ownership; edit persists */
        uint32_t after = rd(gpio, 0);
        printf("[*] wrote %d words; GPIO after = 0x%08x (bit0=%u)\n",
               w, after, after & 1u);
        printf("[%s] bit0 %u -> %u\n",
               (before & 1u) != (after & 1u) ? "CHANGED" : "NO-CHANGE",
               before & 1u, after & 1u);
        free(b);

    } else {
        usage();
    }
    return 0;
}
