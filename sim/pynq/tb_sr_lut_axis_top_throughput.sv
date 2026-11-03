`timescale 1ns/1ps

// Measure the SR datapath with an ideal AXI source and sink.  This deliberately
// bypasses DMA, JPEG, DDR and network timing so the result is a core-level
// throughput reference rather than an end-to-end camera number.
module tb_sr_lut_axis_top_throughput;

  localparam int unsigned WIDTH = 480;
  localparam int unsigned HEIGHT = 270;
  localparam int unsigned INPUT_PIXELS = WIDTH * HEIGHT;
  localparam int unsigned OUTPUT_PIXELS = INPUT_PIXELS * 16;

  logic clk;
  logic resetn;
  logic frame_start;
  logic [23:0] s_data;
  logic s_valid;
  logic s_ready;
  logic s_last;
  logic [23:0] m_data;
  logic m_valid;
  logic m_ready;
  logic m_last;

  integer input_count;
  integer output_count;
  integer cycle_count;
  integer input_stall_count;
  integer errors;
  logic last_seen;
  longint unsigned fps_milli;

  sr_lut_axis_top_dynamic #(
    .SCALE(4),
    .SAMPLE_BITS(sr_lut_pkg::X4_SAMPLE_BITS),
    .Q(sr_lut_pkg::X4_Q),
    .LEVELS(sr_lut_pkg::X4_LEVELS),
    .LUT_DEPTH(sr_lut_pkg::X4_LUT_DEPTH),
    .ADDR_BITS(sr_lut_pkg::X4_ADDR_BITS),
    .MAX_FRAME_WIDTH(480),
    .MAX_FRAME_HEIGHT(320),
    .SCHEDULER_CORES(3),
    .MEM_FILE("data/lut/bram_mem/model_s_x4_5bit.mem")
  ) dut (
    .aclk(clk),
    .aresetn(resetn),
    .s_axis_tdata_i(s_data),
    .s_axis_tvalid_i(s_valid),
    .s_axis_tready_o(s_ready),
    .s_axis_tlast_i(s_last),
    .active_width_i(WIDTH[9:0]),
    .active_height_i(HEIGHT[9:0]),
    .frame_start_i(frame_start),
    .m_axis_tdata_o(m_data),
    .m_axis_tvalid_o(m_valid),
    .m_axis_tready_i(m_ready),
    .m_axis_tlast_o(m_last)
  );

  always #5 clk = ~clk;

  initial begin
    clk = 1'b0;
    resetn = 1'b0;
    frame_start = 1'b0;
    s_data = '0;
    s_valid = 1'b0;
    s_last = 1'b0;
    m_ready = 1'b1;
    input_count = 0;
    output_count = 0;
    cycle_count = 0;
    input_stall_count = 0;
    errors = 0;
    last_seen = 1'b0;

    repeat (4) @(posedge clk);
    #1;
    resetn = 1'b1;

    @(negedge clk);
    frame_start = 1'b1;
    @(posedge clk);
    #1;
    frame_start = 1'b0;

    while (input_count < INPUT_PIXELS || output_count < OUTPUT_PIXELS) begin
      @(negedge clk);
      s_valid = input_count < INPUT_PIXELS;
      s_last = s_valid && (input_count == INPUT_PIXELS - 1);
      if (s_valid && s_ready) begin
        input_count = input_count + 1;
      end else if (s_valid) begin
        input_stall_count = input_stall_count + 1;
      end
      if (m_valid && m_ready) begin
        output_count = output_count + 1;
        if (m_last) begin
          last_seen = 1'b1;
          if (output_count != OUTPUT_PIXELS) begin
            errors = errors + 1;
            $display("ERROR output TLAST arrived at word %0d", output_count);
          end
        end
      end
      @(posedge clk);
      #1;

      cycle_count = cycle_count + 1;
      if (cycle_count > 6_000_000) begin
        $fatal(1, "SR_LUT_AXIS_TOP_THROUGHPUT_TIMEOUT input=%0d output=%0d",
               input_count, output_count);
      end
    end

    s_valid = 1'b0;
    s_last = 1'b0;
    fps_milli = 64'd100_000_000_000 / cycle_count;
    if (input_count != INPUT_PIXELS || output_count != OUTPUT_PIXELS) begin
      errors = errors + 1;
    end
    if (!last_seen) begin
      errors = errors + 1;
    end

    if (errors != 0) begin
      $fatal(1,
             "SR_LUT_AXIS_TOP_IDEAL_THROUGHPUT_FAIL errors=%0d input=%0d output=%0d cycles=%0d",
             errors, input_count, output_count, cycle_count);
    end
    $display(
      "SR_LUT_AXIS_TOP_IDEAL_THROUGHPUT_PASS input=%0d output=%0d cycles=%0d input_stalls=%0d fps=%0d.%03d",
      input_count, output_count, cycle_count, input_stall_count,
      fps_milli / 1000, fps_milli % 1000
    );
    $finish;
  end

endmodule
