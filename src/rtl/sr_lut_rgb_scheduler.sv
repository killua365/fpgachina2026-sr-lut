`timescale 1ns/1ps

module sr_lut_rgb_scheduler #(
  parameter int unsigned SAMPLE_BITS = sr_lut_pkg::X2_SAMPLE_BITS,
  parameter int unsigned Q = 1 << SAMPLE_BITS,
  parameter int unsigned LEVELS = sr_lut_pkg::X2_LEVELS,
  parameter int unsigned LOWER_BITS = $clog2(LEVELS),
  parameter int unsigned LUT_DEPTH = sr_lut_pkg::X2_LUT_DEPTH,
  parameter int unsigned ADDR_BITS = sr_lut_pkg::X2_ADDR_BITS,
  parameter int unsigned OUTPUTS = sr_lut_pkg::X2_OUTPUTS_PER_ENTRY,
  parameter int unsigned PIXEL_BITS = sr_lut_pkg::PIXEL_BITS,
  parameter int unsigned LANES = sr_lut_pkg::NEIGHBOR_COUNT,
  parameter int unsigned MASK_BITS = sr_lut_pkg::SIMPLEX_MASK_BITS,
  parameter int unsigned VERTICES = sr_lut_pkg::SIMPLEX_VERTEX_COUNT,
  parameter int unsigned WEIGHT_BITS = sr_lut_pkg::SIMPLEX_WEIGHT_BITS,
  parameter int unsigned NUMERATOR_BITS = sr_lut_pkg::NUMERATOR_BITS,
  parameter int unsigned CORE_COUNT = 1,
  parameter int unsigned OUTSTANDING_PER_CORE = 2,
  parameter string MEM_FILE = ""
) (
  input  logic clk_i,
  input  logic resetn_i,
  input  logic [(LANES * PIXEL_BITS) - 1:0] s_neighborhood_i,
  input  logic s_valid_i,
  output logic s_ready_o,
  input  logic s_last_i,
  output logic [(OUTPUTS * NUMERATOR_BITS) - 1:0] m_native_o,
  output logic [1:0] m_direction_o,
  output logic [1:0] m_channel_o,
  output logic m_valid_o,
  input  logic m_ready_i,
  output logic m_last_o
);

  localparam int unsigned CORE_ID_BITS =
      (CORE_COUNT <= 1) ? 1 : $clog2(CORE_COUNT);
  localparam int unsigned ORDER_DEPTH = CORE_COUNT * OUTSTANDING_PER_CORE;
  localparam int unsigned ORDER_PTR_BITS =
      (ORDER_DEPTH <= 1) ? 1 : $clog2(ORDER_DEPTH);
  localparam int unsigned ORDER_COUNT_BITS = $clog2(ORDER_DEPTH + 1);

  logic [(VERTICES * ADDR_BITS) - 1:0] task_addresses;
  logic [(VERTICES * WEIGHT_BITS) - 1:0] task_weights;
  logic [1:0] task_direction;
  logic [1:0] task_channel;
  logic task_valid;
  logic task_ready;
  logic task_last;

  logic [CORE_COUNT - 1:0] core_start;
  logic [CORE_COUNT - 1:0] core_ready;
  logic [CORE_COUNT - 1:0] core_valid;
  logic [CORE_COUNT - 1:0] core_result_ready;
  logic [(OUTPUTS * NUMERATOR_BITS) - 1:0]
      core_numerators [0:CORE_COUNT - 1];

  logic [CORE_ID_BITS - 1:0] order_core [0:ORDER_DEPTH - 1];
  logic [1:0] order_direction [0:ORDER_DEPTH - 1];
  logic [1:0] order_channel [0:ORDER_DEPTH - 1];
  logic order_last [0:ORDER_DEPTH - 1];
  logic [ORDER_PTR_BITS - 1:0] order_read_pointer;
  logic [ORDER_PTR_BITS - 1:0] order_write_pointer;
  logic [ORDER_COUNT_BITS - 1:0] order_count;

  logic [CORE_ID_BITS - 1:0] head_core;
  logic [CORE_ID_BITS - 1:0] dispatch_core;
  logic dispatch_found;
  logic dispatch_fire;
  logic output_pop;
  integer dispatch_index;
  integer reset_index;

  function automatic logic [ORDER_PTR_BITS - 1:0] next_order_pointer(
    input logic [ORDER_PTR_BITS - 1:0] pointer
  );
    begin
      if (pointer == ORDER_DEPTH - 1) begin
        next_order_pointer = '0;
      end else begin
        next_order_pointer = pointer + 1'b1;
      end
    end
  endfunction

  assign head_core = order_core[order_read_pointer];
  assign m_valid_o = (order_count != 0) && core_valid[head_core];
  assign m_native_o = core_numerators[head_core];
  assign m_direction_o = order_direction[order_read_pointer];
  assign m_channel_o = order_channel[order_read_pointer];
  assign m_last_o = order_last[order_read_pointer];
  assign output_pop = m_valid_o && m_ready_i;

  always_comb begin
    dispatch_found = 1'b0;
    dispatch_core = '0;
    for (dispatch_index = 0; dispatch_index < CORE_COUNT;
         dispatch_index = dispatch_index + 1) begin
      if (!dispatch_found && core_ready[dispatch_index]) begin
        dispatch_found = 1'b1;
        dispatch_core = dispatch_index[CORE_ID_BITS - 1:0];
      end
    end
  end

  assign task_ready = dispatch_found
      && ((order_count < ORDER_DEPTH) || output_pop);
  assign dispatch_fire = task_valid && task_ready;

  always_comb begin
    core_start = '0;
    if (dispatch_fire) begin
      core_start[dispatch_core] = 1'b1;
    end
  end

  always_comb begin
    core_result_ready = '0;
    if (order_count != 0) begin
      core_result_ready[head_core] = m_ready_i;
    end
  end

  sr_lut_task_pipeline #(
    .SAMPLE_BITS(SAMPLE_BITS),
    .Q(Q),
    .LEVELS(LEVELS),
    .LOWER_BITS(LOWER_BITS),
    .ADDR_BITS(ADDR_BITS),
    .PIXEL_BITS(PIXEL_BITS),
    .LANES(LANES),
    .MASK_BITS(MASK_BITS),
    .VERTICES(VERTICES),
    .WEIGHT_BITS(WEIGHT_BITS)
  ) task_pipeline (
    .clk_i(clk_i),
    .resetn_i(resetn_i),
    .s_neighborhood_i(s_neighborhood_i),
    .s_valid_i(s_valid_i),
    .s_ready_o(s_ready_o),
    .s_last_i(s_last_i),
    .m_addresses_o(task_addresses),
    .m_weights_o(task_weights),
    .m_direction_o(task_direction),
    .m_channel_o(task_channel),
    .m_valid_o(task_valid),
    .m_ready_i(task_ready),
    .m_last_o(task_last)
  );

  generate
    for (genvar core = 0; core < CORE_COUNT; core = core + 1) begin : cores
      logic unused_busy;

      sr_lut_interpolator #(
        .ADDR_BITS(ADDR_BITS),
        .LUT_DEPTH(LUT_DEPTH),
        .OUTPUTS(OUTPUTS),
        .WEIGHT_BITS(WEIGHT_BITS),
        .VERTICES(VERTICES),
        .NUMERATOR_BITS(NUMERATOR_BITS),
        .MEM_FILE(MEM_FILE)
      ) interpolator (
        .clk_i(clk_i),
        .resetn_i(resetn_i),
        .start_i(core_start[core]),
        .ready_o(core_ready[core]),
        .addresses_i(task_addresses),
        .weights_i(task_weights),
        .busy_o(unused_busy),
        .valid_o(core_valid[core]),
        .ready_i(core_result_ready[core]),
        .numerators_o(core_numerators[core])
      );
    end
  endgenerate

  always_ff @(posedge clk_i) begin
    if (!resetn_i) begin
      order_read_pointer <= '0;
      order_write_pointer <= '0;
      order_count <= '0;
      for (reset_index = 0; reset_index < ORDER_DEPTH;
           reset_index = reset_index + 1) begin
        order_core[reset_index] <= '0;
        order_direction[reset_index] <= '0;
        order_channel[reset_index] <= '0;
        order_last[reset_index] <= 1'b0;
      end
    end else begin
      if (output_pop) begin
        order_read_pointer <= next_order_pointer(order_read_pointer);
      end

      if (dispatch_fire) begin
        order_core[order_write_pointer] <= dispatch_core;
        order_direction[order_write_pointer] <= task_direction;
        order_channel[order_write_pointer] <= task_channel;
        order_last[order_write_pointer] <= task_last;
        order_write_pointer <= next_order_pointer(order_write_pointer);
      end

      case ({dispatch_fire, output_pop})
        2'b10: order_count <= order_count + 1'b1;
        2'b01: order_count <= order_count - 1'b1;
        default: begin end
      endcase
    end
  end

`ifndef SYNTHESIS
  initial begin
    if ((CORE_COUNT < 1) || (CORE_COUNT > 3)) begin
      $fatal(1, "scheduler CORE_COUNT must be in the range 1..3");
    end
    if (OUTSTANDING_PER_CORE != 2) begin
      $fatal(1, "scheduler requires two outstanding slots per core");
    end
    if ((OUTPUTS != 4) && (OUTPUTS != 16)) begin
      $fatal(1, "scheduler supports scale-2 or scale-4 LUT entries");
    end
    if (MEM_FILE == "") begin
      $fatal(1, "scheduler MEM_FILE must identify the LUT");
    end
  end

  always_ff @(posedge clk_i) begin
    if (resetn_i && dispatch_fire
        && (order_count == ORDER_DEPTH) && !output_pop) begin
      $fatal(1, "scheduler order FIFO overflow");
    end
  end
`endif

endmodule
