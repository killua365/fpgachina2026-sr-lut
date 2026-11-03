`timescale 1ns/1ps

module tb_sr_lut_fusion_throughput;

  localparam int unsigned OUTPUTS = sr_lut_pkg::X4_OUTPUTS_PER_ENTRY;
  localparam int unsigned NUMERATOR_BITS = sr_lut_pkg::NUMERATOR_BITS;
  localparam int unsigned FUSION_BITS = sr_lut_pkg::FUSION_BITS;
  localparam int unsigned PIXEL_BITS = sr_lut_pkg::PIXEL_BITS;
  localparam int unsigned Q = sr_lut_pkg::X4_Q;
  localparam int unsigned PIXELS = 8;
  localparam int unsigned TASKS = PIXELS * 12;
  localparam int unsigned OUTPUT_BLOCKS = PIXELS * 3;

  logic clk;
  logic resetn;
  logic [(OUTPUTS * NUMERATOR_BITS) - 1:0] s_restored;
  logic [1:0] s_direction;
  logic [1:0] s_channel;
  logic s_valid;
  logic s_ready;
  logic s_last;
  logic [(OUTPUTS * FUSION_BITS) - 1:0] m_fused;
  logic [(OUTPUTS * NUMERATOR_BITS) - 1:0] m_rounded;
  logic [(OUTPUTS * PIXEL_BITS) - 1:0] m_clipped;
  logic [1:0] m_channel;
  logic m_valid;
  logic m_ready;
  logic m_last;

  integer accepted;
  integer outputs_seen;
  integer errors;
  integer cycle_count;
  integer previous_output_cycle;
  integer input_lane;
  integer output_lane;
  integer pixel_index;
  integer task_in_pixel;
  integer direction_value;
  integer channel_value;
  integer output_pixel;
  integer output_channel;
  integer expected_rounded;
  integer input_value;

  sr_lut_fusion #(
    .SAMPLE_BITS(sr_lut_pkg::X4_SAMPLE_BITS),
    .Q(Q),
    .OUTPUTS(OUTPUTS)
  ) dut (
    .clk_i(clk),
    .resetn_i(resetn),
    .s_restored_i(s_restored),
    .s_direction_i(s_direction),
    .s_channel_i(s_channel),
    .s_valid_i(s_valid),
    .s_ready_o(s_ready),
    .s_last_i(s_last),
    .m_fused_o(m_fused),
    .m_rounded_o(m_rounded),
    .m_clipped_o(m_clipped),
    .m_channel_o(m_channel),
    .m_valid_o(m_valid),
    .m_ready_i(m_ready),
    .m_last_o(m_last)
  );

  always #5 clk = ~clk;

  always_comb begin
    pixel_index = accepted / 12;
    task_in_pixel = accepted % 12;
    direction_value = task_in_pixel / 3;
    channel_value = task_in_pixel % 3;
    s_direction = direction_value[1:0];
    s_channel = channel_value[1:0];
    s_last = (accepted == TASKS - 1);
    s_restored = '0;
    for (input_lane = 0; input_lane < OUTPUTS;
         input_lane = input_lane + 1) begin
      input_value = (pixel_index * 4 + direction_value
                     + channel_value + input_lane) * Q;
      s_restored[(input_lane * NUMERATOR_BITS) +: NUMERATOR_BITS] =
          input_value[NUMERATOR_BITS - 1:0];
    end
  end

  always @(posedge clk) begin
    if (!resetn) begin
      accepted <= 0;
      outputs_seen <= 0;
      errors <= 0;
      cycle_count <= 0;
      previous_output_cycle <= -1;
    end else begin
      cycle_count <= cycle_count + 1;

      if (s_valid && !s_ready) begin
        errors <= errors + 1;
        $display("ERROR fusion inserted an input bubble at task %0d", accepted);
      end
      if (s_valid && s_ready) begin
        accepted <= accepted + 1;
      end

      if (m_valid && m_ready) begin
        output_pixel = outputs_seen / 3;
        output_channel = outputs_seen % 3;
        if (m_channel !== output_channel[1:0]) begin
          errors <= errors + 1;
          $display("ERROR output channel=%0d expected=%0d",
                   m_channel, output_channel);
        end
        if (outputs_seen > 0 && output_channel != 0
            && cycle_count != previous_output_cycle + 1) begin
          errors <= errors + 1;
          $display("ERROR channel output bubble interval=%0d",
                   cycle_count - previous_output_cycle);
        end
        for (output_lane = 0; output_lane < OUTPUTS;
             output_lane = output_lane + 1) begin
          expected_rounded =
              4 * (output_pixel * 4 + output_channel + output_lane) + 6;
          if ($signed(m_fused[(output_lane * FUSION_BITS) +: FUSION_BITS])
              !== (expected_rounded * Q)) begin
            errors <= errors + 1;
            $display("ERROR fused pixel=%0d channel=%0d lane=%0d",
                     output_pixel, output_channel, output_lane);
          end
          if ($signed(m_rounded[(output_lane * NUMERATOR_BITS)
                                +: NUMERATOR_BITS])
              !== expected_rounded) begin
            errors <= errors + 1;
            $display("ERROR rounded pixel=%0d channel=%0d lane=%0d",
                     output_pixel, output_channel, output_lane);
          end
          if (m_clipped[(output_lane * PIXEL_BITS) +: PIXEL_BITS]
              !== expected_rounded[PIXEL_BITS - 1:0]) begin
            errors <= errors + 1;
            $display("ERROR clipped pixel=%0d channel=%0d lane=%0d",
                     output_pixel, output_channel, output_lane);
          end
        end
        if (m_last !== (outputs_seen == OUTPUT_BLOCKS - 1)) begin
          errors <= errors + 1;
          $display("ERROR output last mismatch at block %0d", outputs_seen);
        end
        previous_output_cycle <= cycle_count;
        outputs_seen <= outputs_seen + 1;
      end
    end
  end

  initial begin
    clk = 1'b0;
    resetn = 1'b0;
    s_valid = 1'b0;
    m_ready = 1'b1;
    repeat (4) @(posedge clk);
    #1;
    resetn = 1'b1;
    s_valid = 1'b1;

    while (accepted < TASKS) begin
      @(posedge clk);
      #1;
    end
    s_valid = 1'b0;

    while (outputs_seen < OUTPUT_BLOCKS && cycle_count < 300) begin
      @(posedge clk);
      #1;
    end

    if (accepted != TASKS || outputs_seen != OUTPUT_BLOCKS) begin
      errors = errors + 1;
      $display("ERROR counts accepted=%0d/%0d outputs=%0d/%0d",
               accepted, TASKS, outputs_seen, OUTPUT_BLOCKS);
    end
    if (errors != 0) begin
      $fatal(1, "SR_LUT_FUSION_THROUGHPUT_FAIL errors=%0d", errors);
    end
    $display(
      "SR_LUT_FUSION_THROUGHPUT_PASS accepts=%0d outputs=%0d cycles=%0d",
      accepted, outputs_seen, cycle_count
    );
    $finish;
  end

endmodule
