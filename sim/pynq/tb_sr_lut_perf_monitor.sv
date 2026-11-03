`timescale 1ns/1ps

module tb_sr_lut_perf_monitor;

  logic clk;
  logic resetn;
  logic s_valid;
  logic s_ready;
  logic s_last;
  logic m_valid;
  logic m_ready;
  logic m_last;
  logic [31:0] frame_cycles;
  logic [31:0] input_backpressure_cycles;
  logic [31:0] output_backpressure_cycles;
  logic [31:0] input_starvation_cycles;

  sr_lut_perf_monitor dut (
    .clk_i(clk),
    .resetn_i(resetn),
    .s_axis_tvalid_i(s_valid),
    .s_axis_tready_i(s_ready),
    .s_axis_tlast_i(s_last),
    .m_axis_tvalid_i(m_valid),
    .m_axis_tready_i(m_ready),
    .m_axis_tlast_i(m_last),
    .frame_cycles_o(frame_cycles),
    .input_backpressure_cycles_o(input_backpressure_cycles),
    .output_backpressure_cycles_o(output_backpressure_cycles),
    .input_starvation_cycles_o(input_starvation_cycles)
  );

  always #5 clk = ~clk;

  task automatic drive_cycle(
    input logic next_s_valid,
    input logic next_s_ready,
    input logic next_s_last,
    input logic next_m_valid,
    input logic next_m_ready,
    input logic next_m_last
  );
    begin
      @(negedge clk);
      s_valid = next_s_valid;
      s_ready = next_s_ready;
      s_last = next_s_last;
      m_valid = next_m_valid;
      m_ready = next_m_ready;
      m_last = next_m_last;
      @(posedge clk);
      #1;
    end
  endtask

  initial begin
    clk = 1'b0;
    resetn = 1'b0;
    s_valid = 1'b0;
    s_ready = 1'b0;
    s_last = 1'b0;
    m_valid = 1'b0;
    m_ready = 1'b0;
    m_last = 1'b0;

    repeat (3) @(posedge clk);
    #1 resetn = 1'b1;

    drive_cycle(1, 1, 0, 0, 0, 0);
    drive_cycle(1, 0, 0, 0, 0, 0);
    drive_cycle(0, 1, 0, 0, 0, 0);
    drive_cycle(1, 1, 1, 0, 0, 0);
    drive_cycle(0, 0, 0, 1, 0, 0);
    drive_cycle(0, 0, 0, 1, 1, 1);

    if (frame_cycles !== 6
        || input_backpressure_cycles !== 1
        || output_backpressure_cycles !== 1
        || input_starvation_cycles !== 1) begin
      $fatal(
        1,
        "PERF_MONITOR_FAIL first frame=%0d input_bp=%0d output_bp=%0d starve=%0d",
        frame_cycles,
        input_backpressure_cycles,
        output_backpressure_cycles,
        input_starvation_cycles
      );
    end

    drive_cycle(1, 1, 1, 0, 0, 0);
    drive_cycle(0, 0, 0, 1, 1, 1);

    if (frame_cycles !== 2
        || input_backpressure_cycles !== 0
        || output_backpressure_cycles !== 0
        || input_starvation_cycles !== 0) begin
      $fatal(
        1,
        "PERF_MONITOR_FAIL second frame=%0d input_bp=%0d output_bp=%0d starve=%0d",
        frame_cycles,
        input_backpressure_cycles,
        output_backpressure_cycles,
        input_starvation_cycles
      );
    end

    $display(
      "PERF_MONITOR_PASS frame_cycles=%0d input_bp=%0d output_bp=%0d starve=%0d",
      frame_cycles,
      input_backpressure_cycles,
      output_backpressure_cycles,
      input_starvation_cycles
    );
    $finish;
  end

endmodule
