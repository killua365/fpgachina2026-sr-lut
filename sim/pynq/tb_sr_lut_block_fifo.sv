`timescale 1ns/1ps

module tb_sr_lut_block_fifo;

  localparam int unsigned BLOCK_BITS = 32;
  localparam int unsigned DEPTH = 4;

  logic clk;
  logic resetn;
  logic [BLOCK_BITS - 1:0] s_block;
  logic [1:0] s_channel;
  logic s_valid;
  logic s_ready;
  logic s_last;
  logic [BLOCK_BITS - 1:0] m_block;
  logic [1:0] m_channel;
  logic m_valid;
  logic m_ready;
  logic m_last;

  logic [BLOCK_BITS - 1:0] expected_block [0:31];
  logic [1:0] expected_channel [0:31];
  logic expected_last [0:31];
  integer expected_read;
  integer expected_write;
  integer expected_count;
  integer accepted_count;
  integer emitted_count;
  integer full_stall_cycles;
  logic [BLOCK_BITS - 1:0] stalled_block;
  logic [1:0] stalled_channel;
  logic stalled_last;

  sr_lut_block_fifo #(
    .BLOCK_BITS(BLOCK_BITS),
    .DEPTH(DEPTH)
  ) dut (
    .clk_i(clk),
    .resetn_i(resetn),
    .s_block_i(s_block),
    .s_channel_i(s_channel),
    .s_valid_i(s_valid),
    .s_ready_o(s_ready),
    .s_last_i(s_last),
    .m_block_o(m_block),
    .m_channel_o(m_channel),
    .m_valid_o(m_valid),
    .m_ready_i(m_ready),
    .m_last_o(m_last)
  );

  always #5 clk = ~clk;

  always @(posedge clk) begin
    if (resetn) begin
      if (m_valid && m_ready) begin
        if (expected_count == 0) begin
          $fatal(1, "SR_LUT_BLOCK_FIFO_FAIL unexpected output");
        end
        if (m_block !== expected_block[expected_read]
            || m_channel !== expected_channel[expected_read]
            || m_last !== expected_last[expected_read]) begin
          $fatal(
            1,
            "SR_LUT_BLOCK_FIFO_FAIL output=%h/%0d/%0d expected=%h/%0d/%0d",
            m_block,
            m_channel,
            m_last,
            expected_block[expected_read],
            expected_channel[expected_read],
            expected_last[expected_read]
          );
        end
        expected_read = expected_read + 1;
        expected_count = expected_count - 1;
        emitted_count = emitted_count + 1;
      end

      if (s_valid && s_ready) begin
        expected_block[expected_write] = s_block;
        expected_channel[expected_write] = s_channel;
        expected_last[expected_write] = s_last;
        expected_write = expected_write + 1;
        expected_count = expected_count + 1;
        accepted_count = accepted_count + 1;
      end
    end
  end

  task automatic drive_word(
    input logic [BLOCK_BITS - 1:0] block_value,
    input logic [1:0] channel_value,
    input logic last_value,
    input logic ready_value
  );
    begin
      @(negedge clk);
      s_block = block_value;
      s_channel = channel_value;
      s_last = last_value;
      s_valid = 1'b1;
      m_ready = ready_value;
    end
  endtask

  task automatic idle_cycle(input logic ready_value);
    begin
      @(negedge clk);
      s_valid = 1'b0;
      m_ready = ready_value;
    end
  endtask

  initial begin
    clk = 1'b0;
    resetn = 1'b0;
    s_block = '0;
    s_channel = '0;
    s_valid = 1'b0;
    s_last = 1'b0;
    m_ready = 1'b0;
    expected_read = 0;
    expected_write = 0;
    expected_count = 0;
    accepted_count = 0;
    emitted_count = 0;
    full_stall_cycles = 0;

    repeat (3) @(posedge clk);
    #1 resetn = 1'b1;

    drive_word(32'h1000_0001, 0, 0, 0);
    drive_word(32'h2000_0002, 1, 0, 0);
    drive_word(32'h3000_0003, 2, 0, 0);
    drive_word(32'h4000_0004, 0, 0, 0);

    @(negedge clk);
    if (s_ready !== 1'b0 || m_valid !== 1'b1) begin
      $fatal(1, "SR_LUT_BLOCK_FIFO_FAIL full/valid flags");
    end
    stalled_block = m_block;
    stalled_channel = m_channel;
    stalled_last = m_last;

    repeat (3) begin
      s_block = 32'h5000_0005;
      s_channel = 1;
      s_last = 1'b0;
      s_valid = 1'b1;
      m_ready = 1'b0;
      @(posedge clk);
      #1;
      if (s_ready !== 1'b0
          || m_block !== stalled_block
          || m_channel !== stalled_channel
          || m_last !== stalled_last) begin
        $fatal(1, "SR_LUT_BLOCK_FIFO_FAIL unstable while full");
      end
      full_stall_cycles = full_stall_cycles + 1;
      @(negedge clk);
    end

    // The first ready cycle removes the head. Because full is registered,
    // the held fifth word is accepted on the following cycle.
    m_ready = 1'b1;
    @(negedge clk);
    if (s_ready !== 1'b1) begin
      $fatal(1, "SR_LUT_BLOCK_FIFO_FAIL did not reopen after pop");
    end
    @(posedge clk);

    drive_word(32'h6000_0006, 2, 0, 1);
    drive_word(32'h7000_0007, 0, 0, 1);
    drive_word(32'h8000_0008, 1, 0, 1);
    drive_word(32'h9000_0009, 2, 0, 1);
    drive_word(32'ha000_000a, 0, 0, 1);
    drive_word(32'hb000_000b, 1, 0, 1);
    drive_word(32'hc000_000c, 2, 1, 1);

    idle_cycle(1);
    while (expected_count != 0 || m_valid) begin
      idle_cycle(1);
    end
    @(posedge clk);
    #1;

    if (accepted_count != 12 || emitted_count != 12
        || expected_count != 0 || m_valid !== 1'b0
        || full_stall_cycles != 3) begin
      $fatal(
        1,
        "SR_LUT_BLOCK_FIFO_FAIL accepted=%0d emitted=%0d pending=%0d stalls=%0d",
        accepted_count,
        emitted_count,
        expected_count,
        full_stall_cycles
      );
    end

    $display(
      "SR_LUT_BLOCK_FIFO_PASS accepted=%0d emitted=%0d full_stalls=%0d",
      accepted_count,
      emitted_count,
      full_stall_cycles
    );
    $finish;
  end

endmodule
