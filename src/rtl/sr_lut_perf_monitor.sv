`timescale 1ns/1ps

module sr_lut_perf_monitor #(
  parameter int unsigned COUNTER_BITS = 32
) (
  input  logic clk_i,
  input  logic resetn_i,
  input  logic s_axis_tvalid_i,
  input  logic s_axis_tready_i,
  input  logic s_axis_tlast_i,
  input  logic m_axis_tvalid_i,
  input  logic m_axis_tready_i,
  input  logic m_axis_tlast_i,
  output logic [COUNTER_BITS - 1:0] frame_cycles_o,
  output logic [COUNTER_BITS - 1:0] input_backpressure_cycles_o,
  output logic [COUNTER_BITS - 1:0] output_backpressure_cycles_o,
  output logic [COUNTER_BITS - 1:0] input_starvation_cycles_o
);

  logic frame_active;
  logic input_complete;
  logic [COUNTER_BITS - 1:0] active_cycles;
  logic [COUNTER_BITS - 1:0] input_backpressure_cycles;
  logic [COUNTER_BITS - 1:0] output_backpressure_cycles;
  logic [COUNTER_BITS - 1:0] input_starvation_cycles;

  wire input_transfer = s_axis_tvalid_i && s_axis_tready_i;
  wire output_transfer = m_axis_tvalid_i && m_axis_tready_i;

  always_ff @(posedge clk_i) begin
    if (!resetn_i) begin
      frame_active <= 1'b0;
      input_complete <= 1'b0;
      active_cycles <= '0;
      input_backpressure_cycles <= '0;
      output_backpressure_cycles <= '0;
      input_starvation_cycles <= '0;
      frame_cycles_o <= '0;
      input_backpressure_cycles_o <= '0;
      output_backpressure_cycles_o <= '0;
      input_starvation_cycles_o <= '0;
    end else if (!frame_active) begin
      if (input_transfer) begin
        frame_active <= 1'b1;
        input_complete <= s_axis_tlast_i;
        active_cycles <= {{(COUNTER_BITS - 1){1'b0}}, 1'b1};
        input_backpressure_cycles <= '0;
        output_backpressure_cycles <= '0;
        input_starvation_cycles <= '0;
      end
    end else begin
      active_cycles <= active_cycles + 1'b1;

      if (input_transfer && s_axis_tlast_i) begin
        input_complete <= 1'b1;
      end
      if (s_axis_tvalid_i && !s_axis_tready_i) begin
        input_backpressure_cycles <= input_backpressure_cycles + 1'b1;
      end
      if (m_axis_tvalid_i && !m_axis_tready_i) begin
        output_backpressure_cycles <= output_backpressure_cycles + 1'b1;
      end
      if (!input_complete && s_axis_tready_i && !s_axis_tvalid_i) begin
        input_starvation_cycles <= input_starvation_cycles + 1'b1;
      end

      if (output_transfer && m_axis_tlast_i) begin
        frame_active <= 1'b0;
        input_complete <= 1'b0;
        frame_cycles_o <= active_cycles + 1'b1;
        input_backpressure_cycles_o <= input_backpressure_cycles
            + (s_axis_tvalid_i && !s_axis_tready_i);
        output_backpressure_cycles_o <= output_backpressure_cycles;
        input_starvation_cycles_o <= input_starvation_cycles
            + (!input_complete && s_axis_tready_i && !s_axis_tvalid_i);
      end
    end
  end

`ifndef SYNTHESIS
  initial begin
    if (COUNTER_BITS < 2) begin
      $fatal(1, "performance monitor counter width is too small");
    end
  end

  always_ff @(posedge clk_i) begin
    if (resetn_i && output_transfer && m_axis_tlast_i && !frame_active) begin
      $fatal(1, "output frame completed without an active input frame");
    end
  end
`endif

endmodule
