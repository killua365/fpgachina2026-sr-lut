`timescale 1ns/1ps

module sr_lut_axis_top_dynamic #(
  parameter int unsigned SCALE = sr_lut_pkg::X2_SCALE,
  parameter int unsigned SAMPLE_BITS = sr_lut_pkg::X2_SAMPLE_BITS,
  parameter int unsigned Q = 1 << SAMPLE_BITS,
  parameter int unsigned LEVELS = sr_lut_pkg::X2_LEVELS,
  parameter int unsigned LUT_DEPTH = sr_lut_pkg::X2_LUT_DEPTH,
  parameter int unsigned ADDR_BITS = sr_lut_pkg::X2_ADDR_BITS,
  parameter int unsigned MAX_FRAME_WIDTH = 480,
  parameter int unsigned MAX_FRAME_HEIGHT = 320,
  parameter int unsigned PIXEL_BITS = sr_lut_pkg::PIXEL_BITS,
  parameter int unsigned CHANNELS = 3,
  parameter int unsigned RGB_BITS = PIXEL_BITS * CHANNELS,
  parameter int unsigned OUTPUTS = SCALE * SCALE,
  parameter int unsigned NUMERATOR_BITS = sr_lut_pkg::NUMERATOR_BITS,
  parameter int unsigned FUSION_BITS = sr_lut_pkg::FUSION_BITS,
  parameter int unsigned SCHEDULER_CORES = 1,
  parameter string MEM_FILE = ""
) (
  input  logic aclk,
  input  logic aresetn,
  input  logic [RGB_BITS - 1:0] s_axis_tdata_i,
  input  logic s_axis_tvalid_i,
  output logic s_axis_tready_o,
  input  logic s_axis_tlast_i,
  input  logic [9:0] active_width_i,
  input  logic [9:0] active_height_i,
  input  logic frame_start_i,
  output logic [RGB_BITS - 1:0] m_axis_tdata_o,
  output logic m_axis_tvalid_o,
  input  logic m_axis_tready_i,
  output logic m_axis_tlast_o
);

  logic [(sr_lut_pkg::NEIGHBOR_COUNT * PIXEL_BITS) - 1:0]
      neighborhood_data;
  logic neighborhood_valid;
  logic neighborhood_ready;
  logic neighborhood_last;
  logic [(sr_lut_pkg::NEIGHBOR_COUNT * PIXEL_BITS) - 1:0]
      buffered_neighborhood_data;
  logic buffered_neighborhood_valid;
  logic buffered_neighborhood_ready;
  logic buffered_neighborhood_last;

  logic [(OUTPUTS * NUMERATOR_BITS) - 1:0] native_block;
  logic [(OUTPUTS * NUMERATOR_BITS) - 1:0] restored_block;
  logic [1:0] native_direction;
  logic [1:0] native_channel;
  logic native_valid;
  logic native_ready;
  logic native_last;

  logic [(OUTPUTS * NUMERATOR_BITS) - 1:0] pipelined_restored_block;
  logic [1:0] pipelined_direction;
  logic [1:0] pipelined_channel;
  logic pipelined_valid;
  logic pipelined_ready;
  logic pipelined_last;

  logic [(OUTPUTS * FUSION_BITS) - 1:0] fused_block;
  logic [(OUTPUTS * NUMERATOR_BITS) - 1:0] rounded_block;
  logic [(OUTPUTS * PIXEL_BITS) - 1:0] clipped_block;
  logic [1:0] fused_channel;
  logic fused_valid;
  logic fused_ready;
  logic fused_last;

  logic [(OUTPUTS * PIXEL_BITS) - 1:0] buffered_block;
  logic [1:0] buffered_channel;
  logic buffered_valid;
  logic buffered_ready;
  logic buffered_last;

  sr_lut_line_buffer_dynamic #(
    .MAX_FRAME_WIDTH(MAX_FRAME_WIDTH),
    .MAX_FRAME_HEIGHT(MAX_FRAME_HEIGHT),
    .RGB_BITS(RGB_BITS),
    .CHANNEL_BITS(PIXEL_BITS),
    .CHANNELS(CHANNELS),
    .NEIGHBOR_BITS(sr_lut_pkg::NEIGHBOR_COUNT * PIXEL_BITS)
  ) line_buffer (
    .clk_i(aclk),
    .resetn_i(aresetn),
    .s_axis_tdata_i(s_axis_tdata_i),
    .s_axis_tvalid_i(s_axis_tvalid_i),
    .s_axis_tready_o(s_axis_tready_o),
    .s_axis_tlast_i(s_axis_tlast_i),
    .active_width_i(active_width_i),
    .active_height_i(active_height_i),
    .frame_start_i(frame_start_i),
    .neighborhood_data_o(neighborhood_data),
    .neighborhood_valid_o(neighborhood_valid),
    .neighborhood_ready_i(neighborhood_ready),
    .neighborhood_last_o(neighborhood_last)
  );

  // Register the distributed line-memory output before quantization. This
  // keeps the line-memory select path local while preserving one word/cycle.
  assign neighborhood_ready = !buffered_neighborhood_valid
      || buffered_neighborhood_ready;

  always_ff @(posedge aclk) begin
    if (!aresetn || frame_start_i) begin
      buffered_neighborhood_data <= '0;
      buffered_neighborhood_valid <= 1'b0;
      buffered_neighborhood_last <= 1'b0;
    end else if (neighborhood_ready) begin
      buffered_neighborhood_valid <= neighborhood_valid;
      if (neighborhood_valid) begin
        buffered_neighborhood_data <= neighborhood_data;
        buffered_neighborhood_last <= neighborhood_last;
      end
    end
  end

  sr_lut_rgb_scheduler #(
    .SAMPLE_BITS(SAMPLE_BITS),
    .Q(Q),
    .LEVELS(LEVELS),
    .LUT_DEPTH(LUT_DEPTH),
    .ADDR_BITS(ADDR_BITS),
    .OUTPUTS(OUTPUTS),
    .PIXEL_BITS(PIXEL_BITS),
    .NUMERATOR_BITS(NUMERATOR_BITS),
    .CORE_COUNT(SCHEDULER_CORES),
    .MEM_FILE(MEM_FILE)
  ) rgb_scheduler (
    .clk_i(aclk),
    .resetn_i(aresetn),
    .s_neighborhood_i(buffered_neighborhood_data),
    .s_valid_i(buffered_neighborhood_valid),
    .s_ready_o(buffered_neighborhood_ready),
    .s_last_i(buffered_neighborhood_last),
    .m_native_o(native_block),
    .m_direction_o(native_direction),
    .m_channel_o(native_channel),
    .m_valid_o(native_valid),
    .m_ready_i(native_ready),
    .m_last_o(native_last)
  );

  sr_lut_rotation_map #(
    .SCALE(SCALE),
    .ELEMENT_BITS(NUMERATOR_BITS),
    .ELEMENTS(OUTPUTS)
  ) rotation_map (
    .block_i(native_block),
    .direction_i(native_direction),
    .block_o(restored_block)
  );

  sr_lut_result_pipeline #(
    .DATA_BITS(OUTPUTS * NUMERATOR_BITS)
  ) result_pipeline (
    .clk_i(aclk),
    .resetn_i(aresetn),
    .s_data_i(restored_block),
    .s_direction_i(native_direction),
    .s_channel_i(native_channel),
    .s_valid_i(native_valid),
    .s_ready_o(native_ready),
    .s_last_i(native_last),
    .m_data_o(pipelined_restored_block),
    .m_direction_o(pipelined_direction),
    .m_channel_o(pipelined_channel),
    .m_valid_o(pipelined_valid),
    .m_ready_i(pipelined_ready),
    .m_last_o(pipelined_last)
  );

  sr_lut_fusion #(
    .SAMPLE_BITS(SAMPLE_BITS),
    .Q(Q),
    .OUTPUTS(OUTPUTS),
    .CHANNELS(CHANNELS),
    .NUMERATOR_BITS(NUMERATOR_BITS),
    .FUSION_BITS(FUSION_BITS),
    .PIXEL_BITS(PIXEL_BITS)
  ) fusion (
    .clk_i(aclk),
    .resetn_i(aresetn),
    .s_restored_i(pipelined_restored_block),
    .s_direction_i(pipelined_direction),
    .s_channel_i(pipelined_channel),
    .s_valid_i(pipelined_valid),
    .s_ready_o(pipelined_ready),
    .s_last_i(pipelined_last),
    .m_fused_o(fused_block),
    .m_rounded_o(rounded_block),
    .m_clipped_o(clipped_block),
    .m_channel_o(fused_channel),
    .m_valid_o(fused_valid),
    .m_ready_i(fused_ready),
    .m_last_o(fused_last)
  );

  sr_lut_block_fifo #(
    .BLOCK_BITS(OUTPUTS * PIXEL_BITS),
    .DEPTH(4)
  ) block_fifo (
    .clk_i(aclk),
    .resetn_i(aresetn),
    .s_block_i(clipped_block),
    .s_channel_i(fused_channel),
    .s_valid_i(fused_valid),
    .s_ready_o(fused_ready),
    .s_last_i(fused_last),
    .m_block_o(buffered_block),
    .m_channel_o(buffered_channel),
    .m_valid_o(buffered_valid),
    .m_ready_i(buffered_ready),
    .m_last_o(buffered_last)
  );

  sr_lut_output_fifo_dynamic #(
    .SCALE(SCALE),
    .MAX_FRAME_WIDTH(MAX_FRAME_WIDTH),
    .MAX_FRAME_HEIGHT(MAX_FRAME_HEIGHT),
    .PIXEL_BITS(PIXEL_BITS),
    .CHANNELS(CHANNELS),
    .BLOCK_ELEMENTS(OUTPUTS),
    .RGB_BITS(RGB_BITS)
  ) output_fifo (
    .clk_i(aclk),
    .resetn_i(aresetn),
    .s_block_i(buffered_block),
    .s_channel_i(buffered_channel),
    .s_valid_i(buffered_valid),
    .s_ready_o(buffered_ready),
    .s_last_i(buffered_last),
    .active_width_i(active_width_i),
    .active_height_i(active_height_i),
    .frame_start_i(frame_start_i),
    .m_axis_tdata_o(m_axis_tdata_o),
    .m_axis_tvalid_o(m_axis_tvalid_o),
    .m_axis_tready_i(m_axis_tready_i),
    .m_axis_tlast_o(m_axis_tlast_o)
  );

`ifndef SYNTHESIS
  initial begin
    if ((SCALE != 2) && (SCALE != 4)) begin
      $fatal(1, "AXI top supports only scale 2 or 4");
    end
    if ((MAX_FRAME_WIDTH < 2) || (MAX_FRAME_HEIGHT < 2)) begin
      $fatal(1, "AXI top requires frame dimensions of at least two");
    end
    if ((CHANNELS != 3) || (RGB_BITS != (PIXEL_BITS * CHANNELS))) begin
      $fatal(1, "AXI top requires packed RGB channels");
    end
    if (OUTPUTS != (SCALE * SCALE)) begin
      $fatal(1, "AXI top OUTPUTS must equal SCALE * SCALE");
    end
    if (Q != (1 << SAMPLE_BITS)) begin
      $fatal(1, "AXI top Q must equal 2**SAMPLE_BITS");
    end
    if (LEVELS != ((1 << (PIXEL_BITS - SAMPLE_BITS)) + 1)) begin
      $fatal(1, "AXI top LEVELS does not match quantization parameters");
    end
    if (ADDR_BITS != $clog2(LUT_DEPTH)) begin
      $fatal(1, "AXI top ADDR_BITS does not match LUT_DEPTH");
    end
    if ((SCHEDULER_CORES < 1) || (SCHEDULER_CORES > 3)) begin
      $fatal(1, "AXI top SCHEDULER_CORES must be in the range 1..3");
    end
    if (MEM_FILE == "") begin
      $fatal(1, "AXI top MEM_FILE must identify the LUT");
    end
  end

`endif

endmodule
