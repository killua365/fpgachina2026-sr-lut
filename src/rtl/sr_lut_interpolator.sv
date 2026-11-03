`timescale 1ns/1ps

module sr_lut_interpolator #(
  parameter int unsigned ADDR_BITS = sr_lut_pkg::X2_ADDR_BITS,
  parameter int unsigned LUT_DEPTH = sr_lut_pkg::X2_LUT_DEPTH,
  parameter int unsigned OUTPUTS = sr_lut_pkg::X2_OUTPUTS_PER_ENTRY,
  parameter int unsigned LUT_VALUE_BITS = sr_lut_pkg::LUT_VALUE_BITS,
  parameter int unsigned WEIGHT_BITS = sr_lut_pkg::SIMPLEX_WEIGHT_BITS,
  parameter int unsigned VERTICES = sr_lut_pkg::SIMPLEX_VERTEX_COUNT,
  parameter int unsigned NUMERATOR_BITS = sr_lut_pkg::NUMERATOR_BITS,
  parameter string MEM_FILE = ""
) (
  input  logic clk_i,
  input  logic resetn_i,
  input  logic start_i,
  output logic ready_o,
  input  logic [(VERTICES * ADDR_BITS) - 1:0] addresses_i,
  input  logic [(VERTICES * WEIGHT_BITS) - 1:0] weights_i,
  output logic busy_o,
  output logic valid_o,
  input  logic ready_i,
  output logic [(OUTPUTS * NUMERATOR_BITS) - 1:0] numerators_o
);

  localparam int unsigned LUT_WORD_BITS = OUTPUTS * LUT_VALUE_BITS;
  localparam int unsigned PRODUCT_BITS = LUT_VALUE_BITS + WEIGHT_BITS;
  localparam int unsigned MAX_OUTSTANDING = 2;

  logic [1:0] issue_phase;
  logic task_active;
  logic final_pending;
  logic task_accept;

  logic [(VERTICES * ADDR_BITS) - 1:0] latched_addresses;
  logic [(VERTICES * WEIGHT_BITS) - 1:0] latched_weights;
  logic signed [NUMERATOR_BITS - 1:0] accumulators [0:OUTPUTS - 1];
  logic signed [NUMERATOR_BITS - 1:0] product_a [0:OUTPUTS - 1];
  logic signed [NUMERATOR_BITS - 1:0] product_b [0:OUTPUTS - 1];

  logic rom_enable_a;
  logic rom_enable_b;
  logic [ADDR_BITS - 1:0] rom_address_a;
  logic [ADDR_BITS - 1:0] rom_address_b;
  logic [LUT_WORD_BITS - 1:0] rom_data_a;
  logic [LUT_WORD_BITS - 1:0] rom_data_b;
  logic [LUT_WORD_BITS - 1:0] pipelined_rom_data_a;
  logic [LUT_WORD_BITS - 1:0] pipelined_rom_data_b;

  logic [(OUTPUTS * NUMERATOR_BITS) - 1:0] result_fifo [0:1];
  logic result_read_pointer;
  logic result_write_pointer;
  logic [1:0] result_count;
  logic [1:0] outstanding_count;
  logic result_push;
  logic result_pop;

  integer output_index;

  function automatic logic signed [NUMERATOR_BITS - 1:0] weighted_value(
    input logic [LUT_VALUE_BITS - 1:0] lut_bits,
    input logic [WEIGHT_BITS - 1:0] weight_bits
  );
    logic signed [LUT_VALUE_BITS - 1:0] lut_value;
    logic signed [WEIGHT_BITS - 1:0] weight_value;
    logic signed [PRODUCT_BITS - 1:0] product;
    begin
      lut_value = $signed(lut_bits);
      weight_value = $signed(weight_bits);
      product = lut_value * weight_value;
      weighted_value = {
        {(NUMERATOR_BITS - PRODUCT_BITS){product[PRODUCT_BITS - 1]}},
        product
      };
    end
  endfunction

  assign valid_o = (result_count != 0);
  assign numerators_o = result_fifo[result_read_pointer];
  assign result_pop = valid_o && ready_i;
  assign ready_o = (issue_phase == 0)
      && ((outstanding_count < MAX_OUTSTANDING) || result_pop);
  assign busy_o = !ready_o;
  assign task_accept = start_i && ready_o;
  assign result_push = final_pending;

  sr_lut_rom #(
    .DEPTH(LUT_DEPTH),
    .ADDR_BITS(ADDR_BITS),
    .WORD_BITS(LUT_WORD_BITS),
    .MEM_FILE(MEM_FILE)
  ) lut_rom (
    .clk_i(clk_i),
    .read_enable_a_i(rom_enable_a),
    .read_address_a_i(rom_address_a),
    .read_data_a_o(rom_data_a),
    .read_enable_b_i(rom_enable_b),
    .read_address_b_i(rom_address_b),
    .read_data_b_o(rom_data_b)
  );

  always_comb begin
    rom_enable_a = 1'b0;
    rom_enable_b = 1'b0;
    rom_address_a = '0;
    rom_address_b = '0;

    case (issue_phase)
      0: begin
        if (task_accept) begin
          rom_enable_a = 1'b1;
          rom_enable_b = 1'b1;
          rom_address_a = addresses_i[(0 * ADDR_BITS) +: ADDR_BITS];
          rom_address_b = addresses_i[(1 * ADDR_BITS) +: ADDR_BITS];
        end
      end
      1: begin
        if (task_active) begin
          rom_enable_a = 1'b1;
          rom_enable_b = 1'b1;
          rom_address_a = latched_addresses[(2 * ADDR_BITS) +: ADDR_BITS];
          rom_address_b = latched_addresses[(3 * ADDR_BITS) +: ADDR_BITS];
        end
      end
      2: begin
        if (task_active) begin
          rom_enable_a = 1'b1;
          rom_address_a = latched_addresses[(4 * ADDR_BITS) +: ADDR_BITS];
        end
      end
      default: begin end
    endcase
  end

  always_ff @(posedge clk_i) begin
    if (!resetn_i) begin
      issue_phase <= 0;
      task_active <= 1'b0;
      final_pending <= 1'b0;
      latched_addresses <= '0;
      latched_weights <= '0;
      pipelined_rom_data_a <= '0;
      pipelined_rom_data_b <= '0;
      result_read_pointer <= 1'b0;
      result_write_pointer <= 1'b0;
      result_count <= 0;
      outstanding_count <= 0;
      result_fifo[0] <= '0;
      result_fifo[1] <= '0;
      for (output_index = 0; output_index < OUTPUTS;
           output_index = output_index + 1) begin
        accumulators[output_index] <= '0;
        product_a[output_index] <= '0;
        product_b[output_index] <= '0;
      end
    end else begin
      if (result_push) begin
        for (output_index = 0; output_index < OUTPUTS;
             output_index = output_index + 1) begin
          result_fifo[result_write_pointer]
              [(output_index * NUMERATOR_BITS) +: NUMERATOR_BITS]
              <= accumulators[output_index] + product_a[output_index];
        end
        result_write_pointer <= !result_write_pointer;
      end

      if (result_pop) begin
        result_read_pointer <= !result_read_pointer;
      end

      case ({result_push, result_pop})
        2'b10: result_count <= result_count + 1'b1;
        2'b01: result_count <= result_count - 1'b1;
        default: begin end
      endcase

      case ({task_accept, result_pop})
        2'b10: outstanding_count <= outstanding_count + 1'b1;
        2'b01: outstanding_count <= outstanding_count - 1'b1;
        default: begin end
      endcase

      case (issue_phase)
        0: begin
          final_pending <= task_active;

          if (task_active) begin
            for (output_index = 0; output_index < OUTPUTS;
                 output_index = output_index + 1) begin
              accumulators[output_index] <= accumulators[output_index]
                  + product_a[output_index]
                  + product_b[output_index];
              product_a[output_index] <= weighted_value(
                  pipelined_rom_data_a[(output_index * LUT_VALUE_BITS)
                                       +: LUT_VALUE_BITS],
                  latched_weights[(4 * WEIGHT_BITS) +: WEIGHT_BITS]
              );
            end
          end

          task_active <= task_accept;
          if (task_accept) begin
            latched_addresses <= addresses_i;
            latched_weights <= weights_i;
            issue_phase <= 1;
          end
        end

        1: begin
          final_pending <= 1'b0;
          if (task_active) begin
            pipelined_rom_data_a <= rom_data_a;
            pipelined_rom_data_b <= rom_data_b;
          end
          issue_phase <= 2;
        end

        2: begin
          if (task_active) begin
            for (output_index = 0; output_index < OUTPUTS;
                 output_index = output_index + 1) begin
              product_a[output_index] <= weighted_value(
                  pipelined_rom_data_a[(output_index * LUT_VALUE_BITS)
                                       +: LUT_VALUE_BITS],
                  latched_weights[(0 * WEIGHT_BITS) +: WEIGHT_BITS]
              );
              product_b[output_index] <= weighted_value(
                  pipelined_rom_data_b[(output_index * LUT_VALUE_BITS)
                                       +: LUT_VALUE_BITS],
                  latched_weights[(1 * WEIGHT_BITS) +: WEIGHT_BITS]
              );
            end
            pipelined_rom_data_a <= rom_data_a;
            pipelined_rom_data_b <= rom_data_b;
          end
          issue_phase <= 3;
        end

        default: begin
          if (task_active) begin
            for (output_index = 0; output_index < OUTPUTS;
                 output_index = output_index + 1) begin
              accumulators[output_index] <=
                  product_a[output_index] + product_b[output_index];
              product_a[output_index] <= weighted_value(
                  pipelined_rom_data_a[(output_index * LUT_VALUE_BITS)
                                       +: LUT_VALUE_BITS],
                  latched_weights[(2 * WEIGHT_BITS) +: WEIGHT_BITS]
              );
              product_b[output_index] <= weighted_value(
                  pipelined_rom_data_b[(output_index * LUT_VALUE_BITS)
                                       +: LUT_VALUE_BITS],
                  latched_weights[(3 * WEIGHT_BITS) +: WEIGHT_BITS]
              );
            end
            pipelined_rom_data_a <= rom_data_a;
          end
          issue_phase <= 0;
        end
      endcase
    end
  end

`ifndef SYNTHESIS
  initial begin
    if ((VERTICES != 5) || (LUT_VALUE_BITS != 8) || (WEIGHT_BITS != 7)) begin
      $fatal(1, "Interpolator requires five int8 vertices and signed 7-bit weights");
    end
    if (NUMERATOR_BITS < PRODUCT_BITS) begin
      $fatal(1, "NUMERATOR_BITS is too small for a signed product");
    end
    if (ADDR_BITS != $clog2(LUT_DEPTH)) begin
      $fatal(1, "ADDR_BITS does not match LUT_DEPTH");
    end
  end

  always_ff @(posedge clk_i) begin
    if (resetn_i && start_i && !ready_o) begin
      $fatal(1, "interpolator input asserted without ready");
    end
    if (resetn_i && result_push && (result_count == 2) && !result_pop) begin
      $fatal(1, "interpolator result FIFO overflow");
    end
  end
`endif

endmodule
