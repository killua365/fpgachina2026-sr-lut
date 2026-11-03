`timescale 1ns/1ps

module sr_lut_result_pipeline #(
  parameter int unsigned DATA_BITS = 128,
  parameter int unsigned DEPTH = 2,
  parameter int unsigned ADDR_BITS = (DEPTH <= 1) ? 1 : $clog2(DEPTH),
  parameter int unsigned COUNT_BITS = $clog2(DEPTH + 1)
) (
  input  logic clk_i,
  input  logic resetn_i,
  input  logic [DATA_BITS - 1:0] s_data_i,
  input  logic [1:0] s_direction_i,
  input  logic [1:0] s_channel_i,
  input  logic s_valid_i,
  output logic s_ready_o,
  input  logic s_last_i,
  output logic [DATA_BITS - 1:0] m_data_o,
  output logic [1:0] m_direction_o,
  output logic [1:0] m_channel_o,
  output logic m_valid_o,
  input  logic m_ready_i,
  output logic m_last_o
);

  logic [DATA_BITS - 1:0] data_memory [0:DEPTH - 1];
  logic [1:0] direction_memory [0:DEPTH - 1];
  logic [1:0] channel_memory [0:DEPTH - 1];
  logic last_memory [0:DEPTH - 1];
  logic [ADDR_BITS - 1:0] read_pointer;
  logic [ADDR_BITS - 1:0] write_pointer;
  logic [COUNT_BITS - 1:0] occupancy;
  logic push;
  logic pop;

  function automatic logic [ADDR_BITS - 1:0] next_pointer(
    input logic [ADDR_BITS - 1:0] pointer
  );
    begin
      if (pointer == DEPTH - 1) begin
        next_pointer = '0;
      end else begin
        next_pointer = pointer + 1'b1;
      end
    end
  endfunction

  // A two-entry queue makes ready a function of local registered state. The
  // fusion pipeline can no longer propagate output backpressure into the ROM.
  assign s_ready_o = (occupancy < DEPTH);
  assign m_data_o = data_memory[read_pointer];
  assign m_direction_o = direction_memory[read_pointer];
  assign m_channel_o = channel_memory[read_pointer];
  assign m_valid_o = (occupancy != 0);
  assign m_last_o = last_memory[read_pointer];
  assign push = s_valid_i && s_ready_o;
  assign pop = m_valid_o && m_ready_i;

  always_ff @(posedge clk_i) begin
    if (!resetn_i) begin
      read_pointer <= '0;
      write_pointer <= '0;
      occupancy <= '0;
    end else begin
      if (push) begin
        data_memory[write_pointer] <= s_data_i;
        direction_memory[write_pointer] <= s_direction_i;
        channel_memory[write_pointer] <= s_channel_i;
        last_memory[write_pointer] <= s_last_i;
        write_pointer <= next_pointer(write_pointer);
      end

      if (pop) begin
        read_pointer <= next_pointer(read_pointer);
      end

      case ({push, pop})
        2'b10: occupancy <= occupancy + 1'b1;
        2'b01: occupancy <= occupancy - 1'b1;
        default: begin end
      endcase
    end
  end

`ifndef SYNTHESIS
  initial begin
    if (DEPTH < 2) begin
      $fatal(1, "result pipeline DEPTH must be at least two");
    end
  end
`endif

endmodule
