`timescale 1ns/1ps

module tb_sr_lut_interpolator_throughput;

  import sr_lut_pkg::*;

  localparam int unsigned CASE_COUNT = 64;
  localparam int unsigned VECTOR_COUNT = 10_041;
  localparam int unsigned ADDRESS_BUS_BITS =
      SIMPLEX_VERTEX_COUNT * X4_ADDR_BITS;
  localparam int unsigned WEIGHT_BUS_BITS =
      SIMPLEX_VERTEX_COUNT * SIMPLEX_WEIGHT_BITS;
  localparam int unsigned RESULT_BUS_BITS =
      X4_OUTPUTS_PER_ENTRY * NUMERATOR_BITS;

  logic clk;
  logic resetn;
  logic start;
  logic ready;
  logic busy;
  logic valid;
  logic result_ready;
  logic [ADDRESS_BUS_BITS - 1:0] addresses;
  logic [WEIGHT_BUS_BITS - 1:0] weights;
  logic [RESULT_BUS_BITS - 1:0] numerators;

  logic [159:0] address_mem [0:VECTOR_COUNT - 1];
  logic [WEIGHT_BUS_BITS - 1:0] weight_mem [0:VECTOR_COUNT - 1];
  logic [RESULT_BUS_BITS - 1:0] numerator_mem [0:VECTOR_COUNT - 1];

  integer error_count;
  integer total_accepts;
  integer total_outputs;
  integer minimum_interval;
  integer hold_checks;

  sr_lut_interpolator #(
    .ADDR_BITS(X4_ADDR_BITS),
    .LUT_DEPTH(X4_LUT_DEPTH),
    .OUTPUTS(X4_OUTPUTS_PER_ENTRY),
    .MEM_FILE("data/lut/bram_mem/model_s_x4_5bit.mem")
  ) dut (
    .clk_i(clk),
    .resetn_i(resetn),
    .start_i(start),
    .ready_o(ready),
    .addresses_i(addresses),
    .weights_i(weights),
    .busy_o(busy),
    .valid_o(valid),
    .ready_i(result_ready),
    .numerators_o(numerators)
  );

  always #5 clk = ~clk;

  task automatic record_error(input string message);
    begin
      error_count = error_count + 1;
      if (error_count <= 20) begin
        $display("ERROR %s", message);
      end
    end
  endtask

  task automatic reset_dut;
    begin
      resetn = 1'b0;
      start = 1'b0;
      result_ready = 1'b0;
      addresses = '0;
      weights = '0;
      repeat (3) @(posedge clk);
      #1;
      resetn = 1'b1;
    end
  endtask

  task automatic load_case(input integer row);
    integer vertex;
    begin
      addresses = '0;
      for (vertex = 0; vertex < SIMPLEX_VERTEX_COUNT;
           vertex = vertex + 1) begin
        addresses[(vertex * X4_ADDR_BITS) +: X4_ADDR_BITS] =
            address_mem[row][(vertex * 32) +: X4_ADDR_BITS];
      end
      weights = weight_mem[row];
    end
  endtask

  task automatic run_burst(input logic use_stalls);
    integer input_index;
    integer output_index;
    integer cycle_index;
    integer previous_accept_cycle;
    integer accept_interval;
    logic hold_valid;
    logic [RESULT_BUS_BITS - 1:0] held_numerators;
    begin
      reset_dut();
      input_index = 0;
      output_index = 0;
      cycle_index = 0;
      previous_accept_cycle = -1;
      hold_valid = 1'b0;

      while (input_index < CASE_COUNT || output_index < CASE_COUNT) begin
        @(negedge clk);
        result_ready = !use_stalls || ((cycle_index % 13) < 7);
        #1;

        if (valid && result_ready) begin
          if (numerators !== numerator_mem[output_index]) begin
            record_error($sformatf(
              "flow=%0d output=%0d numerator mismatch",
              use_stalls, output_index
            ));
          end
          output_index = output_index + 1;
          total_outputs = total_outputs + 1;
          hold_valid = 1'b0;
        end else if (valid) begin
          if (hold_valid && numerators !== held_numerators) begin
            record_error("interpolator output changed during backpressure");
          end
          hold_valid = 1'b1;
          held_numerators = numerators;
          hold_checks = hold_checks + 1;
        end else begin
          hold_valid = 1'b0;
        end

        start = 1'b0;
        if (input_index < CASE_COUNT && ready) begin
          load_case(input_index);
          start = 1'b1;
          if (!use_stalls && previous_accept_cycle >= 0) begin
            accept_interval = cycle_index - previous_accept_cycle;
            if (accept_interval != 4) begin
              record_error($sformatf(
                "steady accept interval=%0d expected=4", accept_interval
              ));
            end
            if (accept_interval < minimum_interval) begin
              minimum_interval = accept_interval;
            end
          end
          previous_accept_cycle = cycle_index;
          input_index = input_index + 1;
          total_accepts = total_accepts + 1;
        end

        @(posedge clk);
        #1;
        cycle_index = cycle_index + 1;
        if (cycle_index > 10_000) begin
          $fatal(1, "interpolator throughput test timed out");
        end
      end

      @(negedge clk);
      start = 1'b0;
      result_ready = 1'b0;
    end
  endtask

  initial begin
    $readmemh(
      "data/golden/rtl_core/x4_5bit/addresses.mem", address_mem
    );
    $readmemh(
      "data/golden/rtl_core/x4_5bit/weights.mem", weight_mem
    );
    $readmemh(
      "data/golden/rtl_core/x4_5bit/numerators.mem", numerator_mem
    );

    clk = 1'b0;
    resetn = 1'b0;
    start = 1'b0;
    result_ready = 1'b0;
    addresses = '0;
    weights = '0;
    error_count = 0;
    total_accepts = 0;
    total_outputs = 0;
    minimum_interval = 1_000_000;
    hold_checks = 0;

    run_burst(1'b0);
    run_burst(1'b1);

    if (minimum_interval != 4) begin
      record_error($sformatf(
        "minimum accept interval=%0d expected=4", minimum_interval
      ));
    end

    if (error_count != 0) begin
      $fatal(
        1,
        "SR_LUT_INTERPOLATOR_THROUGHPUT_FAIL errors=%0d accepts=%0d outputs=%0d",
        error_count, total_accepts, total_outputs
      );
    end

    $display(
      "SR_LUT_INTERPOLATOR_THROUGHPUT_PASS accepts=%0d outputs=%0d minimum_interval=%0d hold_checks=%0d",
      total_accepts, total_outputs, minimum_interval, hold_checks
    );
    $finish;
  end

endmodule
