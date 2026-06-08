# Cross-compile icaphw for the EBAZ4205 Buildroot target (ARMv7 glibc).
# Toolchain comes from the xilinx bring-up Buildroot output.
# dynamic + stripped: rootfs ships the same bootlin glibc, so the tiny dynamic
# ELF (~10KB) is safe and far cheaper to push over UART than a static one (~600KB).
CC ?= /home/test/xilinx/build/buildroot/output/host/bin/arm-linux-gcc
CFLAGS ?= -O2 -Wall -Wextra -s

icaphw: icaphw.c
	$(CC) $(CFLAGS) -o $@ $<

clean:
	rm -f icaphw

.PHONY: clean
