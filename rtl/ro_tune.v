// ro_tune — tunable ring oscillator + gated frequency counter (zynq-agentctl F0).
//
// A ring oscillator whose loop length (hence frequency) is selected by ONE
// DONT_TOUCH LUT6 ("tuning LUT") configured as a pass-through of one of 6 delay
// taps. The agent changes the tuning LUT's INIT live via HWICAP/ICAP to pick a
// different tap -> different loop delay -> different frequency. Because the
// actual frequency at each setting depends on this die's process + current
// voltage/temperature, there is NO pre-known "correct" INIT: the agent must read
// the measured frequency back and search/adapt. That is the whole point.
//
//   loop_in -> [buf0..buf5 DONT_TOUCH chain] -> taps t0..t5
//   tuning LUT6 (INIT = pass-through of tap k) -> sel_out
//   loop_in = NAND(enable, sel_out)            (1 inversion -> oscillates)
//   sel_out clocks ro_cnt (RO domain).
//
// Frequency counter (FCLK0 domain): enable the RO for a fixed window of FCLK
// cycles, stop it, let it settle, latch ro_cnt -> count_o, clear, repeat.
// PS reads count_o over AXI-GPIO ch1 (proportional to RO frequency); status_o
// (ch2) carries a measurement id + current setting so the host sees progress.
//
// Tuning LUT INIT cheat-sheet (O = I_k pass-through):
//   tap0 64'hAAAAAAAAAAAAAAAA  tap1 64'hCCCCCCCCCCCCCCCC  tap2 64'hF0F0F0F0F0F0F0F0
//   tap3 64'hFF00FF00FF00FF00  tap4 64'hFFFF0000FFFF0000   tap5 64'hFFFFFFFF00000000
// Built default = tap0 (setting A). build_ro_tune.tcl rewrites INIT for A/B bits.

module ro_tune #(
    parameter [31:0] WINDOW = 32'd65536   // RO-enable gate, in FCLK0 cycles
)(
    input  wire        clk,       // FCLK0
    input  wire        resetn,    // active-low
    output reg  [31:0] count_o,   // latest window's RO edge count  (AXI-GPIO ch1)
    output wire [31:0] status_o   // {meas_id[15:0], state[3:0], ...}  (ch2)
);
    // ---------------- ring oscillator ----------------
    wire        enable;           // from FSM
    wire        loop_in;          // loop node (NAND output)
    wire [5:0]  tap;
    wire        sel_out;          // tuning LUT output (selected tap)

    // delay chain of DONT_TOUCH buffer LUT1s; tap[k] has k buffers of delay
    wire [5:0] b;
    genvar i;
    assign tap[0] = loop_in;
    (* DONT_TOUCH = "TRUE" *) LUT1 #(.INIT(2'h2)) buf0 (.O(b[0]), .I0(loop_in));
    generate for (i = 1; i < 6; i = i + 1) begin : gchain
        (* DONT_TOUCH = "TRUE" *) LUT1 #(.INIT(2'h2)) bufk (.O(b[i]), .I0(b[i-1]));
    end endgenerate
    assign tap[1] = b[0];
    assign tap[2] = b[1];
    assign tap[3] = b[2];
    assign tap[4] = b[3];
    assign tap[5] = b[4];

    // tuning LUT: O = selected tap. INIT chosen so O follows one I_k (default tap0).
    (* DONT_TOUCH = "TRUE" *)
    LUT6 #(.INIT(64'hAAAAAAAAAAAAAAAA)) tune_lut (
        .O(sel_out),
        .I0(tap[0]), .I1(tap[1]), .I2(tap[2]),
        .I3(tap[3]), .I4(tap[4]), .I5(tap[5]));

    // inverting enable gate closes the loop: enable=1 -> loop_in = ~sel_out
    (* DONT_TOUCH = "TRUE" *) LUT2 #(.INIT(4'h7)) nand_gate ( // O = ~(I0 & I1)
        .O(loop_in), .I0(sel_out), .I1(enable));

    // ---------------- RO-domain edge counter ----------------
    wire ro_clk = sel_out;
    reg  ro_rst;                  // synchronous-ish clear (asserted while RO stopped)
    reg [31:0] ro_cnt;
    always @(posedge ro_clk or posedge ro_rst) begin
        if (ro_rst) ro_cnt <= 32'd0;
        else        ro_cnt <= ro_cnt + 32'd1;
    end

    // ---------------- FCLK0 measurement FSM ----------------
    localparam S_RUN = 2'd0, S_SETTLE = 2'd1, S_CAP = 2'd2, S_CLR = 2'd3;
    reg [1:0]  state;
    reg [31:0] wcnt;
    reg [3:0]  settle;
    reg [15:0] meas_id;
    reg        en_r;

    assign enable   = en_r;
    assign status_o = {meas_id, 12'b0, state};

    always @(posedge clk) begin
        if (!resetn) begin
            state <= S_RUN; wcnt <= WINDOW; en_r <= 1'b1; ro_rst <= 1'b0;
            settle <= 4'd0; count_o <= 32'd0; meas_id <= 16'd0;
        end else begin
            case (state)
                S_RUN: begin                 // RO running, count the window down
                    en_r <= 1'b1; ro_rst <= 1'b0;
                    if (wcnt == 32'd0) begin state <= S_SETTLE; settle <= 4'd8; en_r <= 1'b0; end
                    else wcnt <= wcnt - 32'd1;
                end
                S_SETTLE: begin              // RO stopped; wait for ro_cnt to be static
                    en_r <= 1'b0;
                    if (settle == 4'd0) state <= S_CAP; else settle <= settle - 4'd1;
                end
                S_CAP: begin                 // latch the measurement
                    count_o <= ro_cnt; meas_id <= meas_id + 16'd1;
                    ro_rst <= 1'b1; state <= S_CLR;
                end
                S_CLR: begin                 // clear RO counter, restart window
                    ro_rst <= 1'b0; wcnt <= WINDOW; state <= S_RUN; en_r <= 1'b1;
                end
            endcase
        end
    end
endmodule
