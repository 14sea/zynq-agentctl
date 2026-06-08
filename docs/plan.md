# 計劃：zynq-agentctl — Agent 控制 FPGA 電路狀態的閉環（先 A 後 B）

## Context（為什麼做這個）

下一階段目標：讓一個 agent（Claude）控制 EBAZ4205(XC7Z010) 的硬件電路狀態——以 live ICAP LUT-INIT 手術改寫 PL 真值表，無 reset。

用戶的關鍵洞察：**形態 A 此刻已存在**——host(WSL) 上的 Claude Code 就是 agent，`/dev/ebaz-uart` 就是通道，過去燒 NAND / loadb / ICAP 手術全是這個 ad-hoc 閉環。所以本計劃不是「搭起 A」，而是把這個臨時閉環**收斂成一個 first-class、可重複、帶反饋的「感知→決策→動作→驗證」回路**，並提供穩定的 agent↔板 命令面。

已敲定的架構決定：
- **執行器路徑 = Linux `/dev/mem` + HWICAP**（否掉 A9 AMP / OpenAMP；也不走 NEORV32 軟核路徑）。
- **先 A 後 B**：A = agent 跑 host、板當執行器（本計劃主體）；B = agent 搬上板 + UART-PPP 聯網（本計劃末尾僅留 scaffold 路線圖，不實作）。
- **獨立項目** `/home/test/zynq_agentctl`，從 `zynq_xpart` / `xilinx` **拷貝**所需檔案，**絕不修改原項目**（見 [[feedback-independent-project-dir]]）。

### 已驗證可復用的事實（來自 zynq_xpart T2.2）
- HWICAP@`0x41400000`（暫存器 WF=+0x100, RF=+0x104, SZ=+0x108, CR=+0x10C, SR=+0x110, WFV=+0x114, RFO=+0x118；CR bit0=Write, SR bit0=DONE/bit2=EOS）。`icap_clk` 必須接 FCLK0（設計裡已接）。
- AXI-GPIO input@`0x41200000`，bit0 = 被測 LUT6 的 INIT[0]（6 輸入全綁 0）。
- 配置引擎 MUX 交給 ICAP 的唯一缺片：`devcfg.CTRL[PCAP_PR]`=bit27 @`0xF8007000`（`0x4c00e07f`→`0x4400e07f` 交給 ICAP；寫完還原 `0x4c00e07f`，編輯持久）。
- 目標幀：FAR=`0x00400d9a`, word 73, bit 15（prjxray `bit_00400d9a_073_15`）。
- 幀序列由 `hwicap-make-framewrite.py <A.bit> <B.bit> <FAR> <wofs=73> <out.bin>` 從 **RAW .bit** 的 FDRI 流抽取（不可用 prjxray .bits，缺 ECC word50）。最小序列 = sync/RCRC/IDCODE(0x03722093)/WCFG/FAR/FDRI(202w=幀+鄰幀 pad)/CRC=0/DESYNC，**無 GRESTORE/GTS**。
- 板上環境：glibc + busybox(`CONFIG_DEVMEM=y`) + `fpgautil`，**無 Python**；交叉編譯器 `arm-linux-gcc` 在 `xilinx/build/buildroot/output/host/bin/`。
- Buildroot Linux 從 NAND 啟動，`fpgautil -b <file>` A-route 運行時重配 PL 已驗證（~37 ms，state=operating）。

### 本計劃唯一的真實未知（P1 當場驗證）
T2.2 的 live edit 是在 **miner U-Boot（無驅動）** 下打的。本計劃要在**跑著 `xilinx-devcfg`/`fpga_manager` 驅動的 Linux** 下用 `/dev/mem` 改 PCAP_PR 並驅動 HWICAP。新變數 = 活躍的 devcfg 驅動是否與我們的直接寄存器 poke 衝突。U-Boot 路徑作為已知良好的對照基準保留（隔離問題用）。

---

## 新項目目錄結構 `/home/test/zynq_agentctl`

```
zynq_agentctl/
  README.md                      # 項目說明（A 閉環 + B 路線圖）
  .gitignore                     # 忽略 *.bit(大)/build/.env 等，比照 zynq_xpart
  board/
    ebaz4205.cfg                 # ← 拷自 xilinx/  （openocd 恢復用）
    lut_A.bit  lut_B.bit         # ← 拷自 zynq_xpart/vivado/hwicap_lut/build/  (各 2.04MB)
    allowlist.sha256             # ← 拷自 zynq_xpart/board/ (measured-load 門, 可選)
  firmware/
    icaphw.c                     # ★新寫：板上 /dev/mem HWICAP 執行器（唯一從零的程式碼）
    Makefile                     # 用 arm-linux-gcc 交叉編譯 → icaphw (ARM ELF)
  host/
    uart-poke.py uart-push-b64.py uboot-intercept.py  # ← 拷自 xilinx/scripts/
    hwicap-make-framewrite.py    # ← 拷自 zynq_xpart/scripts/
    measured-load.py             # ← 拷自 zynq_xpart/scripts/ (可選)
    agentctl.py                  # ★新寫：host 端 agent 命令面 wrapper（薄層）
  seq/
    seqAB.bin  seqBA.bin         # 由 make-framewrite 生成的兩向幀序列（INIT 0↔1）
  docs/
    plan.md                      # 本計劃落地版
    loop.md                      # 閉環演示說明
```

> 拷貝用 `cp`，不 symlink、不 `git submodule`，徹底隔離。`zynq_xpart`/`xilinx` 一個檔案都不動。

---

## P0 — 建項目 + 起點驗證（純復用，無新邏輯）

1. `mkdir /home/test/zynq_agentctl` 及子目錄；`git init`；按上表 `cp` 拷入檔案。
2. 寫 `README.md` / `.gitignore`（比照 [[reference-github-repo]] 的 .gitignore 慣例，至少忽略 `board/*.bit`、`firmware/icaphw`、`__pycache__`）。
3. 把板子帶到 **Buildroot Linux shell**（非 U-Boot）：若在 U-Boot，`reset`；登入 `root\r`（空密碼，見 [[ebaz4205-bring-up]] 登入時序，勿連發兩個 `\r`）。
4. 推 `lut_A.bit` 到 `/tmp/lut_A.bit`（`host/uart-push-b64.py`，~3 min @115200）。
5. `fpgautil -b /tmp/lut_A.bit` → 確認 `cat /sys/class/fpga_manager/fpga0/state` = `operating`、dmesg 乾淨。
6. `busybox devmem 0x41200000` → 期望 bit0 = **0**（lut_A，INIT[0]=0）。
   * 驗收：GPIO probe 讀到 0，證明設計就位、起點態正確。

## P1 — 寫板上 `/dev/mem` HWICAP 執行器（核心新工程）

**`firmware/icaphw.c`**（mmap `/dev/mem`，移植 `hwicap-uart.py` 的 U-Boot mw/md 邏輯為原生記憶體訪問）：
- mmap 三個頁：HWICAP `0x41400000`、GPIO `0x41200000`、DEVCFG `0xF8007000`（各 `mmap` 對齊到 page，`O_RDWR|O_SYNC`）。
- 子命令：
  - `gpio` → 印 `0x41200000` 的值（感知）。
  - `regs` → 印 SR/WFV/CR/ASR/RFO（健康檢查，期望 SR=0x5、WFV=0x3f）。
  - `pcap-pr <0|1>` → 讀-改-寫 `0xF8007000` 的 bit27。
  - `writeseq <file.bin>` → 讀 big-endian uint32 流，按 `hwicap-uart.py:wf_write()` 的 **WFV-vacancy chunking + CR.Write(0x1) + 等 CR 清零** 演算法寫入 WF。
  - `edit <file.bin>` → 組合動作：印 gpio(before) → `pcap-pr 0` → `writeseq` → `pcap-pr 1` → 印 gpio(after)。
- `Makefile`：`CC=…/output/host/bin/arm-linux-gcc`，`-O2 -static`（或動態，glibc 已在 rootfs），輸出 ~20KB ELF。

**生成幀序列**（host）：
- `host/hwicap-make-framewrite.py board/lut_A.bit board/lut_B.bit 0x00400d9a 73 seq/seqAB.bin`（→寫 B 幀，INIT 0→1）。
- A/B 對調再跑一次 → `seq/seqBA.bin`（→寫 A 幀，INIT 1→0）。

**板上驗證（P1 主驗收 = 解決唯一未知）**：
1. 推 `icaphw` + `seqAB.bin` + `seqBA.bin` 到 `/tmp/`。
2. `chmod +x /tmp/icaphw`；`/tmp/icaphw regs` → 期望 SR=0x5/WFV=0x3f（HWICAP 健康）。
3. `/tmp/icaphw gpio` → 0。
4. `/tmp/icaphw edit /tmp/seqAB.bin` → before=0, after=**1**（**Linux 下首次 live LUT edit，無 reset**）。
5. `/tmp/icaphw edit /tmp/seqBA.bin` → before=1, after=**0**（可逆）。
   * **若 devcfg 驅動衝突**（after 不變/卡 PCFG_INIT）：fallback 依序——(a) 先 `echo 0 > /sys/class/fpga_manager/fpga0/...` 或 unbind `xilinx-devcfg`，重試；(b) 用 U-Boot 路徑（`zynq_xpart/scripts/hwicap-uart.py`）打同一個 `seqAB.bin` 作 A/B 對照，確認失敗只來自「Linux 驅動」這一個變數，再決定是否需 DTS-disable devcfg。
   * 驗收：Linux `/dev/mem` 路徑跑通雙向 live edit。`/dev/mem + HWICAP` 路徑坐實。

## P2 — 命令面 + 端到端閉環（agent = host 上的我）

**`host/agentctl.py`**（薄 wrapper，把 P1 的板上動作收斂成冪等指令，供任何 Claude session 調用）：
- `ensure-linux` → 確保板在 Linux shell（必要時 reset + 登入；UART 經 `/dev/ebaz-uart`）。
- `setup` → 推 `lut_A.bit`/`icaphw`/`seq*.bin`（缺才推）+ `fpgautil -b` + `regs` 健康檢查。
- `perceive` → 回 `icaphw gpio`（當前 LUT 態）。
- `act <state>` → 依目標態送 `edit seqAB.bin`/`seqBA.bin`。
- `verify` → `perceive` 與目標比對，回 ok/retry。
- 復用 `host/uart-poke.py`、`uart-push-b64.py`；輸出走 `--quiet`、低噪音（見 [[feedback-tool-output-noise]]，勿用「dump/extract bitstream」措辭）。

**閉環演示** `docs/loop.md`：agent 跑完整 **感知→決策→動作→驗證**——讀 probe → 定目標真值表 → 選/生成幀 → `edit` → 回讀 probe 確認 → 不符則重試。第一版只在 INIT[0]∈{0,1} 兩態間 toggle 即足以證明回路；之後可擴到更多 LUT 位。

## P3 —（可選）固化與門控

- 把 `icaphw` + `lut_A.bit` 烤進 rootfs（Buildroot post-build 或 mtd5），免每 session UART 推 2MB（注意 [[ebaz4205-bring-up]] 的 Buildroot 增量重建陷阱：新包要先 `make <pkg>`）。
- 接 `measured-load.py` 的 sha256 門（M5）在 `setup` 前驗 `lut_A.bit`/`seq*.bin` 可信。
- MCP tool 化 / `/loop` 自主化（讓非我 session 或自主回路也能驅動）。

---

## B 階段 scaffold（路線圖，本計劃不實作）

目標：agent 真正搬上板（用戶原願景）。前置死結 = **聯網**（以太硬件壞、PS USB 未引出，見 [[ebaz4205-bring-up]]）。可行路線：
1. **UART-PPP + host NAT**：板上 `pppd`（需 Buildroot 加 `BR2_PACKAGE_PPPD`）over `/dev/ttyPS0`，host 端 `pppd` + iptables MASQUERADE 轉發出網。115200 ≈ 11 KB/s，夠跑 Anthropic API 的 JSON（慢但通）；可後續提 UART 波特率。
2. **板上輕量 agent**：256 MB DDR 跑不動 Node 版 Claude Code → 用 **Python + anthropic SDK** 的精簡 agent（但 rootfs 目前無 Python，需 `BR2_PACKAGE_PYTHON3` + `python-requests`/SDK，rootfs 體積與 64MB jffs2 上限要核）。agent 直接在板上調 P1 的 `icaphw`（本機 `/dev/mem`，無 UART 中介）。
3. 風險點：DDR/rootfs 容量、PPP 穩定性（CH340 brownout）、SDK 在 ARMv7 的依賴。這些等 A 驗證完另開 session 細評。

---

## 不要重蹈的坑（沿用兩項目記憶）
- 失敗的 `fpgautil -b <壞bit>` 會卡死 DEVCFG（Timeout PCFG_INIT），只能斷電；`lut_A.bit` 已知良好+CRC disable，勿故意載壞檔。
- HWICAP `CR=0x4`(Abort) 會 wedge 寫路徑，別用。
- 恢復基石：openocd SLCR soft-reset(`mww phys 0xF8000008 0xDF0D; mww phys 0xF8000200 1`)+UART 砸 `'d'` → miner U-Boot。
- 硬件操作全程用 Opus，**絕不降級 Sonnet**；需用戶親手跑的命令統一收尾批量給出（見 [[feedback-sudo-workflow]]、[[feedback-tool-output-noise]]）。
- 提交策略：本地 commit 免問，**push 前必問**（見 [[feedback-commit-push-policy]]）。

## 驗證總綱（end-to-end）
1. P0：`fpgautil` 載 lut_A → `devmem 0x41200000` bit0=0。
2. P1：`icaphw edit seqAB.bin` → probe 0→1；`edit seqBA.bin` → 1→0（Linux 下、無 reset、可逆）。
3. P2：`agentctl.py` 跑一輪 perceive→act→verify，回讀確認態變更。
4. 全程 dmesg 無 error、`fpga_manager` state=operating。
