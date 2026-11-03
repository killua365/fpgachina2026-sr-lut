`timescale 1ns/1ps

module sr_lut_fusion #(
  parameter int unsigned SAMPLE_BITS = sr_lut_pkg::X2_SAMPLE_BITS,
  parameter int unsigned Q = 1 << SAMPLE_BITS,
  parameter int unsigned OUTPUTS = sr_lut_pkg::X2_OUTPUTS_PER_ENTRY,
  parameter int unsigned CHANNELS = 3,
  parameter int unsigned NUMERATOR_BITS = sr_lut_pkg::NUMERATOR_BITS,
  parameter int unsigned FUSION_BITS = sr_lut_pkg::FUSION_BITS,
  parameter int unsigned PIXEL_BITS = sr_lut_pkg::PIXEL_BITS
) (
  input  logic clk_i,
  input  logic resetn_i,
  input  logic [(OUTPUTS * NUMERATOR_BITS) - 1:0] s_restored_i,
  input  logic [1:0] s_direction_i,
  input  logic [1:0] s_channel_i,
  input  logic s_valid_i,
  output logic s_ready_o,
  input  logic s_last_i,
  output logic [(OUTPUTS * FUSION_BITS) - 1:0] m_fused_o,
  output logic [(OUTPUTS * NUMERATOR_BITS) - 1:0] m_rounded_o,
  output logic [(OUTPUTS * PIXEL_BITS) - 1:0] m_clipped_o,
  output logic [1:0] m_channel_o,
  output logic m_valid_o,
  input  logic m_ready_i,
  output logic m_last_o
);

  logic signed [FUSION_BITS - 1:0] accumulators
      [0:CHANNELS - 1][0:OUTPUTS - 1];
  logic [(OUTPUTS * FUSION_BITS) - 1:0] fused_stage_data;
  logic [1:0] fused_stage_channel;
  logic fused_stage_valid;
  logic fused_stage_ready;
  logic fused_stage_last;

  logic [(OUTPUTS * FUSION_BITS) - 1:0] rounded_stage_fused;
  logic [(OUTPUTS * NUMERATOR_BITS) - 1:0] rounded_stage_data;
  logic [1:0] rounded_stage_channel;
  logic rounded_stage_valid;
  logic rounded_stage_ready;
  logic rounded_stage_last;

  logic output_stage_ready;
  logic input_fire;
  logic [1:0] expected_direction;
  logic [1:0] expected_channel;
  integer channel_index;
  integer output_index;

  function automatic logic signed [FUSION_BITS - 1:0]
      extend_numerator(input logic [NUMERATOR_BITS - 1:0] bits);
    begin
      extend_numerator = {
        {(FUSION_BITS - NUMERATOR_BITS){bits[NUMERATOR_BITS - 1]}},
        bits
      };
    end
  endfunction

  function automatic logic signed [NUMERATOR_BITS - 1:0]
      round_ties_to_even(
        input logic signed [FUSION_BITS - 1:0] value
      );
    logic [FUSION_BITS - 1:0] magnitude;
    logic [FUSION_BITS - 1:0] quotient;
    logic [FUSION_BITS - 1:0] remainder;
    logic [FUSION_BITS - 1:0] rounded_magnitude;
    logic increment;
    logic signed [FUSION_BITS:0] signed_result;
    begin
      if (value[FUSION_BITS - 1]) begin
        magnitude = $unsigned(-value);
      end else begin
        magnitude = $unsigned(value);
      end

      quotient = magnitude / Q;
      remainder = magnitude % Q;
      increment = ((remainder * 2) > Q)
          || (((remainder * 2) == Q) && quotient[0]);
      rounded_magnitude = quotient + increment;

      if (value[FUSION_BITS - 1]) begin
        signed_result = -$signed({1'b0, rounded_magnitude});
      end else begin
        signed_result = $signed({1'b0, rounded_magnitude});
      end
      round_ties_to_even = signed_result[NUMERATOR_BITS - 1:0];
    end
  endfunction

  function automatic logic [PIXEL_BITS - 1:0] clip_to_pixel(
    input logic signed [NUMERATOR_BITS - 1:0] value
  );
    begin
      if (value < 0) begin
        clip_to_pixel = '0;
      end else if (value > ((1 << PIXEL_BITS) - 1)) begin
        clip_to_pixel = {PIXEL_BITS{1'b1}};
      end else begin
        clip_to_pixel = value[PIXEL_BITS - 1:0];
      end
    end
  endfunction

  assign output_stage_ready = !m_valid_o || m_ready_i;
  assign rounded_stage_ready = !rounded_stage_valid || output_stage_ready;
  assign fused_stage_ready = !fused_stage_valid || rounded_stage_ready;
  assign s_ready_o = !s_valid_i || (s_direction_i != 3)
      || fused_stage_ready;
  assign input_fire = s_valid_i && s_ready_o;

  always_ff @(posedge clk_i) begin
    if (!resetn_i) begin
      m_fused_o <= '0;
      m_rounded_o <= '0;
      m_clipped_o <= '0;
      m_channel_o <= 0;
      m_valid_o <= 1'b0;
      m_last_o <= 1'b0;
      fused_stage_data <= '0;
      fused_stage_channel <= 0;
      fused_stage_valid <= 1'b0;
      fused_stage_last <= 1'b0;
      rounded_stage_fused <= '0;
      rounded_stage_data <= '0;
      rounded_stage_channel <= 0;
      rounded_stage_valid <= 1'b0;
      rounded_stage_last <= 1'b0;
      expected_direction <= 0;
      expected_channel <= 0;

      for (channel_index = 0; channel_index < CHANNELS;
           channel_index = channel_index + 1) begin
        for (output_index = 0; output_index < OUTPUTS;
             output_index = output_index + 1) begin
          accumulators[channel_index][output_index] <= '0;
        end
      end
    end else begin
      if (output_stage_ready) begin
        m_valid_o <= rounded_stage_valid;
        if (rounded_stage_valid) begin
          m_fused_o <= rounded_stage_fused;
          m_rounded_o <= rounded_stage_data;
          for (output_index = 0; output_index < OUTPUTS;
               output_index = output_index + 1) begin
            m_clipped_o[(output_index * PIXEL_BITS)
                        +: PIXEL_BITS] <= clip_to_pixel(
                rounded_stage_data[(output_index * NUMERATOR_BITS)
                                   +: NUMERATOR_BITS]
            );
          end
          m_channel_o <= rounded_stage_channel;
          m_last_o <= rounded_stage_last;
        end
      end

      if (rounded_stage_ready) begin
        rounded_stage_valid <= fused_stage_valid;
        if (fused_stage_valid) begin
          rounded_stage_fused <= fused_stage_data;
          for (output_index = 0; output_index < OUTPUTS;
               output_index = output_index + 1) begin
            rounded_stage_data[(output_index * NUMERATOR_BITS)
                               +: NUMERATOR_BITS] <= round_ties_to_even(
                fused_stage_data[(output_index * FUSION_BITS)
                                 +: FUSION_BITS]
            );
          end
          rounded_stage_channel <= fused_stage_channel;
          rounded_stage_last <= fused_stage_last;
        end
      end

      if (fused_stage_ready) begin
        fused_stage_valid <= input_fire && (s_direction_i == 3);
        if (input_fire && (s_direction_i == 3)) begin
          for (output_index = 0; output_index < OUTPUTS;
               output_index = output_index + 1) begin
            fused_stage_data[(output_index * FUSION_BITS)
                             +: FUSION_BITS] <=
                accumulators[s_channel_i][output_index]
                + extend_numerator(
                    s_restored_i[(output_index * NUMERATOR_BITS)
                                 +: NUMERATOR_BITS]
                  );
          end
          fused_stage_channel <= s_channel_i;
          fused_stage_last <= s_last_i;
        end
      end

      if (input_fire) begin
        for (output_index = 0; output_index < OUTPUTS;
             output_index = output_index + 1) begin
          if (s_direction_i == 0) begin
            accumulators[s_channel_i][output_index] <=
                extend_numerator(
                  s_restored_i[(output_index * NUMERATOR_BITS)
                               +: NUMERATOR_BITS]
                );
          end else if (s_direction_i != 3) begin
            accumulators[s_channel_i][output_index] <=
                accumulators[s_channel_i][output_index]
                + extend_numerator(
                    s_restored_i[(output_index * NUMERATOR_BITS)
                                 +: NUMERATOR_BITS]
                  );
          end
        end

        if (expected_channel == 2) begin
          expected_channel <= 0;
          if (expected_direction == 3) begin
            expected_direction <= 0;
          end else begin
            expected_direction <= expected_direction + 1;
          end
        end else begin
          expected_channel <= expected_channel + 1;
        end
      end
    end
  end

`ifndef SYNTHESIS
  initial begin
    if ((OUTPUTS != 4) && (OUTPUTS != 16)) begin
      $fatal(1, "fusion supports scale-2 or scale-4 blocks");
    end
    if (CHANNELS != 3) begin
      $fatal(1, "fusion requires exactly three RGB channels");
    end
    if (FUSION_BITS < (NUMERATOR_BITS + 2)) begin
      $fatal(1, "FUSION_BITS must preserve a four-direction sum");
    end
    if (Q != (1 << SAMPLE_BITS)) begin
      $fatal(1, "Q must equal 2**SAMPLE_BITS");
    end
  end

  always_ff @(posedge clk_i) begin
    if (resetn_i && s_valid_i && s_ready_o) begin
      if (s_direction_i !== expected_direction
          || s_channel_i !== expected_channel) begin
        $fatal(
          1,
          "fusion input order mismatch direction=%0d/%0d channel=%0d/%0d",
          s_direction_i, expected_direction,
          s_channel_i, expected_channel
        );
      end
      if (s_last_i
          && !(s_direction_i == 3 && s_channel_i == 2)) begin
        $fatal(1, "fusion input last is not direction 3 channel B");
      end
    end
  end
`endif

endmodule
