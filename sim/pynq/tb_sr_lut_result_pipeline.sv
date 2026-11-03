`timescale 1ns/1ps

module tb_sr_lut_result_pipeline;

  localparam int unsigned DATA_BITS = 32;

  logic clk;
  logic resetn;
  logic [DATA_BITS - 1:0] s_data;
  logic [1:0] s_direction;
  logic [1:0] s_channel;
  logic s_valid;
  logic s_ready;
  logic s_last;
  logic [DATA_BITS - 1:0] m_data;
  logic [1:0] m_direction;
  logic [1:0] m_channel;
  logic m_valid;
  logic m_ready;
  logic m_last;

  sr_lut_result_pipeline #(
    .DATA_BITS(DATA_BITS),
    .DEPTH(2)
  ) dut (
    .clk_i(clk),
    .resetn_i(resetn),
    .s_data_i(s_data),
    .s_direction_i(s_direction),
    .s_channel_i(s_channel),
    .s_valid_i(s_valid),
    .s_ready_o(s_ready),
    .s_last_i(s_last),
    .m_data_o(m_data),
    .m_direction_o(m_direction),
    .m_channel_o(m_channel),
    .m_valid_o(m_valid),
    .m_ready_i(m_ready),
    .m_last_o(m_last)
  );

  always #5 clk = ~clk;

  task automatic drive(
    input logic [DATA_BITS - 1:0] data_value,
    input logic [1:0] direction_value,
    input logic [1:0] channel_value,
    input logic last_value,
    input logic valid_value,
    input logic ready_value
  );
    begin
      @(negedge clk);
      s_data = data_value;
      s_direction = direction_value;
      s_channel = channel_value;
      s_last = last_value;
      s_valid = valid_value;
      m_ready = ready_value;
    end
  endtask

  initial begin
    clk = 1'b0;
    resetn = 1'b0;
    s_data = '0;
    s_direction = '0;
    s_channel = '0;
    s_valid = 1'b0;
    s_last = 1'b0;
    m_ready = 1'b0;

    repeat (3) @(posedge clk);
    #1 resetn = 1'b1;

    drive(32'h1111_0001, 0, 0, 0, 1, 0);
    drive(32'h2222_0002, 3, 2, 1, 1, 0);
    @(negedge clk);
    if (s_ready !== 1'b0 || m_valid !== 1'b1
        || m_data !== 32'h1111_0001
        || m_direction !== 0 || m_channel !== 0 || m_last !== 0) begin
      $fatal(1, "SR_LUT_RESULT_PIPELINE_FAIL full/head state");
    end

    // Changing downstream ready must not change upstream ready combinationally.
    m_ready = 1'b1;
    #1;
    if (s_ready !== 1'b0) begin
      $fatal(1, "SR_LUT_RESULT_PIPELINE_FAIL ready combinationally bypassed");
    end

    @(posedge clk);
    #1;
    if (s_ready !== 1'b1 || m_data !== 32'h2222_0002
        || m_direction !== 3 || m_channel !== 2 || m_last !== 1) begin
      $fatal(1, "SR_LUT_RESULT_PIPELINE_FAIL second entry");
    end

    drive(32'h3333_0003, 1, 1, 0, 1, 1);
    @(posedge clk);
    #1;
    if (m_valid !== 1'b1 || m_data !== 32'h3333_0003
        || m_direction !== 1 || m_channel !== 1 || m_last !== 0) begin
      $fatal(1, "SR_LUT_RESULT_PIPELINE_FAIL simultaneous push/pop");
    end

    drive('0, 0, 0, 0, 0, 1);
    @(posedge clk);
    #1;
    if (m_valid !== 1'b0 || s_ready !== 1'b1) begin
      $fatal(1, "SR_LUT_RESULT_PIPELINE_FAIL empty state");
    end

    $display("SR_LUT_RESULT_PIPELINE_PASS depth=2 ready_isolated=1");
    $finish;
  end

endmodule
