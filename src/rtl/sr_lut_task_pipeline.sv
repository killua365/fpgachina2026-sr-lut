`timescale 1ns/1ps

module sr_lut_task_pipeline #(
  parameter int unsigned SAMPLE_BITS = sr_lut_pkg::X2_SAMPLE_BITS,
  parameter int unsigned Q = 1 << SAMPLE_BITS,
  parameter int unsigned LEVELS = sr_lut_pkg::X2_LEVELS,
  parameter int unsigned LOWER_BITS = $clog2(LEVELS),
  parameter int unsigned ADDR_BITS = sr_lut_pkg::X2_ADDR_BITS,
  parameter int unsigned PIXEL_BITS = sr_lut_pkg::PIXEL_BITS,
  parameter int unsigned LANES = sr_lut_pkg::NEIGHBOR_COUNT,
  parameter int unsigned MASK_BITS = sr_lut_pkg::SIMPLEX_MASK_BITS,
  parameter int unsigned VERTICES = sr_lut_pkg::SIMPLEX_VERTEX_COUNT,
  parameter int unsigned WEIGHT_BITS = sr_lut_pkg::SIMPLEX_WEIGHT_BITS
) (
  input  logic clk_i,
  input  logic resetn_i,
  input  logic [(LANES * PIXEL_BITS) - 1:0] s_neighborhood_i,
  input  logic s_valid_i,
  output logic s_ready_o,
  input  logic s_last_i,
  output logic [(VERTICES * ADDR_BITS) - 1:0] m_addresses_o,
  output logic [(VERTICES * WEIGHT_BITS) - 1:0] m_weights_o,
  output logic [1:0] m_direction_o,
  output logic [1:0] m_channel_o,
  output logic m_valid_o,
  input  logic m_ready_i,
  output logic m_last_o
);

  logic [(LANES * LOWER_BITS) - 1:0] quantized_lower;
  logic [(LANES * SAMPLE_BITS) - 1:0] quantized_remainders;

  logic s0_valid;
  logic [(LANES * LOWER_BITS) - 1:0] s0_lower;
  logic [(LANES * SAMPLE_BITS) - 1:0] s0_remainders;
  logic [1:0] s0_direction;
  logic [1:0] s0_channel;
  logic s0_last;

  logic [(VERTICES * MASK_BITS) - 1:0] selected_masks;
  logic [(VERTICES * WEIGHT_BITS) - 1:0] selected_weights;

  logic s1_valid;
  logic [(LANES * LOWER_BITS) - 1:0] s1_lower;
  logic [(VERTICES * MASK_BITS) - 1:0] s1_masks;
  logic [(VERTICES * WEIGHT_BITS) - 1:0] s1_weights;
  logic [ADDR_BITS - 1:0] s1_base_ab;
  logic [1:0] s1_direction;
  logic [1:0] s1_channel;
  logic s1_last;

  logic s2_valid;
  logic [(LANES * LOWER_BITS) - 1:0] s2_lower;
  logic [(VERTICES * MASK_BITS) - 1:0] s2_masks;
  logic [(VERTICES * WEIGHT_BITS) - 1:0] s2_weights;
  logic [ADDR_BITS - 1:0] s2_base_abc;
  logic [1:0] s2_direction;
  logic [1:0] s2_channel;
  logic s2_last;

  logic s3_valid;
  logic [(VERTICES * MASK_BITS) - 1:0] s3_masks;
  logic [(VERTICES * WEIGHT_BITS) - 1:0] s3_weights;
  logic [ADDR_BITS - 1:0] s3_base;
  logic [1:0] s3_direction;
  logic [1:0] s3_channel;
  logic s3_last;

  logic s4_valid;
  logic [(VERTICES * ADDR_BITS) - 1:0] s4_addresses;
  logic [(VERTICES * WEIGHT_BITS) - 1:0] s4_weights;
  logic [1:0] s4_direction;
  logic [1:0] s4_channel;
  logic s4_last;

  logic s0_ready;
  logic s1_ready;
  logic s2_ready;
  logic s3_ready;
  logic s4_ready;
  logic [1:0] input_direction;
  logic [1:0] input_channel;
  integer vertex;

  function automatic logic [ADDR_BITS - 1:0] mask_offset(
    input logic [MASK_BITS - 1:0] mask_value
  );
    logic [ADDR_BITS - 1:0] offset_value;
    begin
      offset_value = '0;
      if (mask_value[3]) begin
        offset_value = offset_value + (LEVELS * LEVELS * LEVELS);
      end
      if (mask_value[2]) begin
        offset_value = offset_value + (LEVELS * LEVELS);
      end
      if (mask_value[1]) begin
        offset_value = offset_value + LEVELS;
      end
      if (mask_value[0]) begin
        offset_value = offset_value + 1'b1;
      end
      mask_offset = offset_value;
    end
  endfunction

  assign s4_ready = !s4_valid || m_ready_i;
  assign s3_ready = !s3_valid || s4_ready;
  assign s2_ready = !s2_valid || s3_ready;
  assign s1_ready = !s1_valid || s2_ready;
  assign s0_ready = !s0_valid || s1_ready;
  assign s_ready_o = s0_ready;

  assign m_addresses_o = s4_addresses;
  assign m_weights_o = s4_weights;
  assign m_direction_o = s4_direction;
  assign m_channel_o = s4_channel;
  assign m_valid_o = s4_valid;
  assign m_last_o = s4_last;

  sr_lut_quantizer #(
    .PIXEL_BITS(PIXEL_BITS),
    .SAMPLE_BITS(SAMPLE_BITS),
    .LEVELS(LEVELS),
    .LOWER_BITS(LOWER_BITS),
    .LANES(LANES)
  ) quantizer (
    .pixels_i(s_neighborhood_i),
    .lower_o(quantized_lower),
    .remainder_o(quantized_remainders)
  );

  sr_lut_simplex_selector #(
    .SAMPLE_BITS(SAMPLE_BITS),
    .Q(Q),
    .LANES(LANES),
    .MASK_BITS(MASK_BITS),
    .VERTICES(VERTICES),
    .WEIGHT_BITS(WEIGHT_BITS)
  ) simplex_selector (
    .remainders_i(s0_remainders),
    .masks_o(selected_masks),
    .weights_o(selected_weights)
  );

  always_ff @(posedge clk_i) begin
    if (!resetn_i) begin
      s0_valid <= 1'b0;
      s1_valid <= 1'b0;
      s2_valid <= 1'b0;
      s3_valid <= 1'b0;
      s4_valid <= 1'b0;
      s0_lower <= '0;
      s0_remainders <= '0;
      s1_lower <= '0;
      s1_masks <= '0;
      s1_weights <= '0;
      s1_base_ab <= '0;
      s2_lower <= '0;
      s2_masks <= '0;
      s2_weights <= '0;
      s2_base_abc <= '0;
      s3_masks <= '0;
      s3_weights <= '0;
      s3_base <= '0;
      s4_addresses <= '0;
      s4_weights <= '0;
      s0_direction <= '0;
      s0_channel <= '0;
      s0_last <= 1'b0;
      s1_direction <= '0;
      s1_channel <= '0;
      s1_last <= 1'b0;
      s2_direction <= '0;
      s2_channel <= '0;
      s2_last <= 1'b0;
      s3_direction <= '0;
      s3_channel <= '0;
      s3_last <= 1'b0;
      s4_direction <= '0;
      s4_channel <= '0;
      s4_last <= 1'b0;
      input_direction <= '0;
      input_channel <= '0;
    end else begin
      if (s4_ready) begin
        s4_valid <= s3_valid;
        if (s3_valid) begin
          for (vertex = 0; vertex < VERTICES; vertex = vertex + 1) begin
            s4_addresses[(vertex * ADDR_BITS) +: ADDR_BITS] <=
                s3_base + mask_offset(
                  s3_masks[(vertex * MASK_BITS) +: MASK_BITS]
                );
          end
          s4_weights <= s3_weights;
          s4_direction <= s3_direction;
          s4_channel <= s3_channel;
          s4_last <= s3_last;
        end
      end

      if (s3_ready) begin
        s3_valid <= s2_valid;
        if (s2_valid) begin
          s3_base <= (s2_base_abc * LEVELS)
              + s2_lower[(3 * LOWER_BITS) +: LOWER_BITS];
          s3_masks <= s2_masks;
          s3_weights <= s2_weights;
          s3_direction <= s2_direction;
          s3_channel <= s2_channel;
          s3_last <= s2_last;
        end
      end

      if (s2_ready) begin
        s2_valid <= s1_valid;
        if (s1_valid) begin
          s2_base_abc <= (s1_base_ab * LEVELS)
              + s1_lower[(2 * LOWER_BITS) +: LOWER_BITS];
          s2_lower <= s1_lower;
          s2_masks <= s1_masks;
          s2_weights <= s1_weights;
          s2_direction <= s1_direction;
          s2_channel <= s1_channel;
          s2_last <= s1_last;
        end
      end

      if (s1_ready) begin
        s1_valid <= s0_valid;
        if (s0_valid) begin
          s1_base_ab <=
              (s0_lower[(0 * LOWER_BITS) +: LOWER_BITS] * LEVELS)
              + s0_lower[(1 * LOWER_BITS) +: LOWER_BITS];
          s1_lower <= s0_lower;
          s1_masks <= selected_masks;
          s1_weights <= selected_weights;
          s1_direction <= s0_direction;
          s1_channel <= s0_channel;
          s1_last <= s0_last;
        end
      end

      if (s0_ready) begin
        s0_valid <= s_valid_i;
        if (s_valid_i) begin
          s0_lower <= quantized_lower;
          s0_remainders <= quantized_remainders;
          s0_direction <= input_direction;
          s0_channel <= input_channel;
          s0_last <= s_last_i;

          if (input_channel == 2) begin
            input_channel <= 0;
            if (input_direction == 3) begin
              input_direction <= 0;
            end else begin
              input_direction <= input_direction + 1'b1;
            end
          end else begin
            input_channel <= input_channel + 1'b1;
          end
        end
      end
    end
  end

`ifndef SYNTHESIS
  initial begin
    if ((LANES != 4) || (VERTICES != 5)) begin
      $fatal(1, "task pipeline requires four neighbors and five vertices");
    end
  end

  always_ff @(posedge clk_i) begin
    if (resetn_i && s_valid_i && s_ready_o && s_last_i
        && !(input_direction == 3 && input_channel == 2)) begin
      $fatal(1, "task pipeline input last is not direction 3 channel B");
    end
  end
`endif

endmodule
