`timescale 1ns/1ps

module tb_sr_lut_interpolator;

  import sr_lut_pkg::*;

  localparam int unsigned CASE_COUNT = 10_041;
  localparam int unsigned VERTEX_COUNT = SIMPLEX_VERTEX_COUNT;
  localparam int unsigned MAX_PRINTED_ERRORS = 20;
  localparam int unsigned INTERPOLATOR_LATENCY = 5;

  localparam int unsigned X2_LOWER_BUS_BITS = NEIGHBOR_COUNT * X2_LOWER_BITS;
  localparam int unsigned X2_REMAINDER_BUS_BITS = NEIGHBOR_COUNT * X2_SAMPLE_BITS;
  localparam int unsigned X2_MASK_BUS_BITS = VERTEX_COUNT * SIMPLEX_MASK_BITS;
  localparam int unsigned X2_WEIGHT_BUS_BITS = VERTEX_COUNT * SIMPLEX_WEIGHT_BITS;
  localparam int unsigned X2_ADDRESS_BUS_BITS = VERTEX_COUNT * X2_ADDR_BITS;

  localparam int unsigned X4_LOWER_BUS_BITS = NEIGHBOR_COUNT * X4_LOWER_BITS;
  localparam int unsigned X4_REMAINDER_BUS_BITS = NEIGHBOR_COUNT * X4_SAMPLE_BITS;
  localparam int unsigned X4_MASK_BUS_BITS = VERTEX_COUNT * SIMPLEX_MASK_BITS;
  localparam int unsigned X4_WEIGHT_BUS_BITS = VERTEX_COUNT * SIMPLEX_WEIGHT_BITS;
  localparam int unsigned X4_ADDRESS_BUS_BITS = VERTEX_COUNT * X4_ADDR_BITS;

  logic clk;
  logic resetn;

  logic [31:0] x2_inputs_mem [0:CASE_COUNT - 1];
  logic [31:0] x2_lower_mem [0:CASE_COUNT - 1];
  logic [31:0] x2_remainders_mem [0:CASE_COUNT - 1];
  logic [X2_MASK_BUS_BITS - 1:0] x2_masks_mem [0:CASE_COUNT - 1];
  logic [X2_WEIGHT_BUS_BITS - 1:0] x2_weights_mem [0:CASE_COUNT - 1];
  logic [159:0] x2_addresses_mem [0:CASE_COUNT - 1];
  logic [X2_OUTPUTS_PER_ENTRY * NUMERATOR_BITS - 1:0]
      x2_numerators_mem [0:CASE_COUNT - 1];

  logic [31:0] x4_inputs_mem [0:CASE_COUNT - 1];
  logic [31:0] x4_lower_mem [0:CASE_COUNT - 1];
  logic [31:0] x4_remainders_mem [0:CASE_COUNT - 1];
  logic [X4_MASK_BUS_BITS - 1:0] x4_masks_mem [0:CASE_COUNT - 1];
  logic [X4_WEIGHT_BUS_BITS - 1:0] x4_weights_mem [0:CASE_COUNT - 1];
  logic [159:0] x4_addresses_mem [0:CASE_COUNT - 1];
  logic [X4_OUTPUTS_PER_ENTRY * NUMERATOR_BITS - 1:0]
      x4_numerators_mem [0:CASE_COUNT - 1];

  logic [31:0] x2_pixels;
  logic [X2_LOWER_BUS_BITS - 1:0] x2_lower;
  logic [X2_REMAINDER_BUS_BITS - 1:0] x2_remainders;
  logic [X2_MASK_BUS_BITS - 1:0] x2_masks;
  logic [X2_WEIGHT_BUS_BITS - 1:0] x2_chain_weights;
  logic [X2_ADDRESS_BUS_BITS - 1:0] x2_chain_addresses;
  logic [X2_ADDRESS_BUS_BITS - 1:0] x2_direct_addresses;
  logic [X2_WEIGHT_BUS_BITS - 1:0] x2_direct_weights;
  logic [X2_ADDRESS_BUS_BITS - 1:0] x2_interpolator_addresses;
  logic [X2_WEIGHT_BUS_BITS - 1:0] x2_interpolator_weights;
  logic x2_use_chain;
  logic x2_start;
  logic x2_ready;
  logic x2_busy;
  logic x2_valid;
  logic [X2_OUTPUTS_PER_ENTRY * NUMERATOR_BITS - 1:0] x2_numerators;

  logic [31:0] x4_pixels;
  logic [X4_LOWER_BUS_BITS - 1:0] x4_lower;
  logic [X4_REMAINDER_BUS_BITS - 1:0] x4_remainders;
  logic [X4_MASK_BUS_BITS - 1:0] x4_masks;
  logic [X4_WEIGHT_BUS_BITS - 1:0] x4_chain_weights;
  logic [X4_ADDRESS_BUS_BITS - 1:0] x4_chain_addresses;
  logic [X4_ADDRESS_BUS_BITS - 1:0] x4_direct_addresses;
  logic [X4_WEIGHT_BUS_BITS - 1:0] x4_direct_weights;
  logic [X4_ADDRESS_BUS_BITS - 1:0] x4_interpolator_addresses;
  logic [X4_WEIGHT_BUS_BITS - 1:0] x4_interpolator_weights;
  logic x4_use_chain;
  logic x4_start;
  logic x4_ready;
  logic x4_busy;
  logic x4_valid;
  logic [X4_OUTPUTS_PER_ENTRY * NUMERATOR_BITS - 1:0] x4_numerators;

  integer error_count;
  integer transaction_count;
  integer numerator_check_count;
  integer chain_check_count;

  assign x2_interpolator_addresses =
      x2_use_chain ? x2_chain_addresses : x2_direct_addresses;
  assign x2_interpolator_weights =
      x2_use_chain ? x2_chain_weights : x2_direct_weights;
  assign x4_interpolator_addresses =
      x4_use_chain ? x4_chain_addresses : x4_direct_addresses;
  assign x4_interpolator_weights =
      x4_use_chain ? x4_chain_weights : x4_direct_weights;

  sr_lut_quantizer #(
    .SAMPLE_BITS(X2_SAMPLE_BITS),
    .LEVELS(X2_LEVELS),
    .LOWER_BITS(X2_LOWER_BITS)
  ) quantizer_x2 (
    .pixels_i(x2_pixels),
    .lower_o(x2_lower),
    .remainder_o(x2_remainders)
  );

  sr_lut_simplex_selector #(
    .SAMPLE_BITS(X2_SAMPLE_BITS),
    .Q(X2_Q)
  ) selector_x2 (
    .remainders_i(x2_remainders),
    .masks_o(x2_masks),
    .weights_o(x2_chain_weights)
  );

  sr_lut_address_gen #(
    .LEVELS(X2_LEVELS),
    .LOWER_BITS(X2_LOWER_BITS),
    .ADDR_BITS(X2_ADDR_BITS)
  ) address_gen_x2 (
    .lower_i(x2_lower),
    .masks_i(x2_masks),
    .addresses_o(x2_chain_addresses)
  );

  sr_lut_interpolator #(
    .ADDR_BITS(X2_ADDR_BITS),
    .LUT_DEPTH(X2_LUT_DEPTH),
    .OUTPUTS(X2_OUTPUTS_PER_ENTRY),
    .MEM_FILE("data/lut/bram_mem/model_s_x2_4bit.mem")
  ) interpolator_x2 (
    .clk_i(clk),
    .resetn_i(resetn),
    .start_i(x2_start),
    .ready_o(x2_ready),
    .addresses_i(x2_interpolator_addresses),
    .weights_i(x2_interpolator_weights),
    .busy_o(x2_busy),
    .valid_o(x2_valid),
    .ready_i(1'b1),
    .numerators_o(x2_numerators)
  );

  sr_lut_quantizer #(
    .SAMPLE_BITS(X4_SAMPLE_BITS),
    .LEVELS(X4_LEVELS),
    .LOWER_BITS(X4_LOWER_BITS)
  ) quantizer_x4 (
    .pixels_i(x4_pixels),
    .lower_o(x4_lower),
    .remainder_o(x4_remainders)
  );

  sr_lut_simplex_selector #(
    .SAMPLE_BITS(X4_SAMPLE_BITS),
    .Q(X4_Q)
  ) selector_x4 (
    .remainders_i(x4_remainders),
    .masks_o(x4_masks),
    .weights_o(x4_chain_weights)
  );

  sr_lut_address_gen #(
    .LEVELS(X4_LEVELS),
    .LOWER_BITS(X4_LOWER_BITS),
    .ADDR_BITS(X4_ADDR_BITS)
  ) address_gen_x4 (
    .lower_i(x4_lower),
    .masks_i(x4_masks),
    .addresses_o(x4_chain_addresses)
  );

  sr_lut_interpolator #(
    .ADDR_BITS(X4_ADDR_BITS),
    .LUT_DEPTH(X4_LUT_DEPTH),
    .OUTPUTS(X4_OUTPUTS_PER_ENTRY),
    .MEM_FILE("data/lut/bram_mem/model_s_x4_5bit.mem")
  ) interpolator_x4 (
    .clk_i(clk),
    .resetn_i(resetn),
    .start_i(x4_start),
    .ready_o(x4_ready),
    .addresses_i(x4_interpolator_addresses),
    .weights_i(x4_interpolator_weights),
    .busy_o(x4_busy),
    .valid_o(x4_valid),
    .ready_i(1'b1),
    .numerators_o(x4_numerators)
  );

  always #5 clk = ~clk;

  task automatic record_error(input string message);
    begin
      error_count = error_count + 1;
      if (error_count <= MAX_PRINTED_ERRORS) begin
        $display("ERROR %s", message);
      end
    end
  endtask

  task automatic wait_x2_result(input integer row);
    integer cycles;
    begin
      cycles = 0;
      while (1) begin
        @(posedge clk);
        #1;
        cycles = cycles + 1;
        if (x2_valid) begin
          break;
        end
        if (cycles > 8) begin
          record_error($sformatf("x2 row=%0d did not produce valid", row));
          break;
        end
      end
      if (cycles != INTERPOLATOR_LATENCY) begin
        record_error($sformatf(
            "x2 row=%0d latency=%0d expected=%0d",
            row, cycles, INTERPOLATOR_LATENCY
        ));
      end
      if (x2_valid && (x2_numerators !== x2_numerators_mem[row])) begin
        record_error($sformatf("x2 row=%0d numerator mismatch", row));
      end
      if (x2_valid) begin
        numerator_check_count = numerator_check_count + X2_OUTPUTS_PER_ENTRY;
      end
    end
  endtask

  task automatic wait_x4_result(input integer row);
    integer cycles;
    begin
      cycles = 0;
      while (1) begin
        @(posedge clk);
        #1;
        cycles = cycles + 1;
        if (x4_valid) begin
          break;
        end
        if (cycles > 8) begin
          record_error($sformatf("x4 row=%0d did not produce valid", row));
          break;
        end
      end
      if (cycles != INTERPOLATOR_LATENCY) begin
        record_error($sformatf(
            "x4 row=%0d latency=%0d expected=%0d",
            row, cycles, INTERPOLATOR_LATENCY
        ));
      end
      if (x4_valid && (x4_numerators !== x4_numerators_mem[row])) begin
        record_error($sformatf("x4 row=%0d numerator mismatch", row));
      end
      if (x4_valid) begin
        numerator_check_count = numerator_check_count + X4_OUTPUTS_PER_ENTRY;
      end
    end
  endtask

  task automatic start_x2(input logic use_chain);
    begin
      x2_use_chain = use_chain;
      @(negedge clk);
      while (!x2_ready) begin
        @(negedge clk);
      end
      x2_start = 1'b1;
      @(posedge clk);
      #1;
      x2_start = 1'b0;
    end
  endtask

  task automatic start_x4(input logic use_chain);
    begin
      x4_use_chain = use_chain;
      @(negedge clk);
      while (!x4_ready) begin
        @(negedge clk);
      end
      x4_start = 1'b1;
      @(posedge clk);
      #1;
      x4_start = 1'b0;
    end
  endtask

  task automatic run_x2_direct;
    integer row;
    integer vertex;
    logic [X2_ADDRESS_BUS_BITS - 1:0] address_bus;
    logic [X2_WEIGHT_BUS_BITS - 1:0] weight_bus;
    begin
      $display("E4 interpolator x2 direct: checking %0d rows", CASE_COUNT);
      for (row = 0; row < CASE_COUNT; row = row + 1) begin
        address_bus = '0;
        weight_bus = '0;
        for (vertex = 0; vertex < VERTEX_COUNT; vertex = vertex + 1) begin
          address_bus[(vertex * X2_ADDR_BITS) +: X2_ADDR_BITS] =
              x2_addresses_mem[row][(vertex * 32) +: X2_ADDR_BITS];
          weight_bus[(vertex * SIMPLEX_WEIGHT_BITS) +: SIMPLEX_WEIGHT_BITS] =
              x2_weights_mem[row][(vertex * SIMPLEX_WEIGHT_BITS)
                                  +: SIMPLEX_WEIGHT_BITS];
        end
        x2_direct_addresses = address_bus;
        x2_direct_weights = weight_bus;
        start_x2(1'b0);
        wait_x2_result(row);
        transaction_count = transaction_count + 1;
      end
    end
  endtask

  task automatic run_x2_chain;
    integer row;
    integer lane;
    begin
      $display("E4 interpolator x2 E1-E3 chain: checking %0d rows", CASE_COUNT);
      x2_use_chain = 1'b1;
      for (row = 0; row < CASE_COUNT; row = row + 1) begin
        x2_pixels = x2_inputs_mem[row];
        #1;
        for (lane = 0; lane < NEIGHBOR_COUNT; lane = lane + 1) begin
          if (x2_lower[(lane * X2_LOWER_BITS) +: X2_LOWER_BITS]
              !== x2_lower_mem[row][(lane * 8) +: X2_LOWER_BITS]) begin
            record_error($sformatf("x2 row=%0d lane=%0d lower chain mismatch", row, lane));
          end
          if (x2_remainders[(lane * X2_SAMPLE_BITS) +: X2_SAMPLE_BITS]
              !== x2_remainders_mem[row][(lane * 8) +: X2_SAMPLE_BITS]) begin
            record_error($sformatf("x2 row=%0d lane=%0d remainder chain mismatch", row, lane));
          end
        end
        if (x2_masks !== x2_masks_mem[row]) begin
          record_error($sformatf("x2 row=%0d mask chain mismatch", row));
        end
        if (x2_chain_weights !== x2_weights_mem[row]) begin
          record_error($sformatf("x2 row=%0d weight chain mismatch", row));
        end
        for (integer vertex = 0; vertex < VERTEX_COUNT; vertex = vertex + 1) begin
          if (x2_chain_addresses[(vertex * X2_ADDR_BITS) +: X2_ADDR_BITS]
              !== x2_addresses_mem[row][(vertex * 32) +: X2_ADDR_BITS]) begin
            record_error($sformatf("x2 row=%0d vertex=%0d address chain mismatch", row, vertex));
          end
        end
        start_x2(1'b1);
        wait_x2_result(row);
        chain_check_count = chain_check_count + 1;
        transaction_count = transaction_count + 1;
      end
    end
  endtask

  task automatic run_x4_direct;
    integer row;
    integer vertex;
    logic [X4_ADDRESS_BUS_BITS - 1:0] address_bus;
    logic [X4_WEIGHT_BUS_BITS - 1:0] weight_bus;
    begin
      $display("E4 interpolator x4 direct: checking %0d rows", CASE_COUNT);
      for (row = 0; row < CASE_COUNT; row = row + 1) begin
        address_bus = '0;
        weight_bus = '0;
        for (vertex = 0; vertex < VERTEX_COUNT; vertex = vertex + 1) begin
          address_bus[(vertex * X4_ADDR_BITS) +: X4_ADDR_BITS] =
              x4_addresses_mem[row][(vertex * 32) +: X4_ADDR_BITS];
          weight_bus[(vertex * SIMPLEX_WEIGHT_BITS) +: SIMPLEX_WEIGHT_BITS] =
              x4_weights_mem[row][(vertex * SIMPLEX_WEIGHT_BITS)
                                  +: SIMPLEX_WEIGHT_BITS];
        end
        x4_direct_addresses = address_bus;
        x4_direct_weights = weight_bus;
        start_x4(1'b0);
        wait_x4_result(row);
        transaction_count = transaction_count + 1;
      end
    end
  endtask

  task automatic run_x4_chain;
    integer row;
    integer lane;
    begin
      $display("E4 interpolator x4 E1-E3 chain: checking %0d rows", CASE_COUNT);
      x4_use_chain = 1'b1;
      for (row = 0; row < CASE_COUNT; row = row + 1) begin
        x4_pixels = x4_inputs_mem[row];
        #1;
        for (lane = 0; lane < NEIGHBOR_COUNT; lane = lane + 1) begin
          if (x4_lower[(lane * X4_LOWER_BITS) +: X4_LOWER_BITS]
              !== x4_lower_mem[row][(lane * 8) +: X4_LOWER_BITS]) begin
            record_error($sformatf("x4 row=%0d lane=%0d lower chain mismatch", row, lane));
          end
          if (x4_remainders[(lane * X4_SAMPLE_BITS) +: X4_SAMPLE_BITS]
              !== x4_remainders_mem[row][(lane * 8) +: X4_SAMPLE_BITS]) begin
            record_error($sformatf("x4 row=%0d lane=%0d remainder chain mismatch", row, lane));
          end
        end
        if (x4_masks !== x4_masks_mem[row]) begin
          record_error($sformatf("x4 row=%0d mask chain mismatch", row));
        end
        if (x4_chain_weights !== x4_weights_mem[row]) begin
          record_error($sformatf("x4 row=%0d weight chain mismatch", row));
        end
        for (integer vertex = 0; vertex < VERTEX_COUNT; vertex = vertex + 1) begin
          if (x4_chain_addresses[(vertex * X4_ADDR_BITS) +: X4_ADDR_BITS]
              !== x4_addresses_mem[row][(vertex * 32) +: X4_ADDR_BITS]) begin
            record_error($sformatf("x4 row=%0d vertex=%0d address chain mismatch", row, vertex));
          end
        end
        start_x4(1'b1);
        wait_x4_result(row);
        chain_check_count = chain_check_count + 1;
        transaction_count = transaction_count + 1;
      end
    end
  endtask

  initial begin
    $readmemh("data/golden/rtl_core/x2_4bit/inputs.mem", x2_inputs_mem);
    $readmemh("data/golden/rtl_core/x2_4bit/lower.mem", x2_lower_mem);
    $readmemh("data/golden/rtl_core/x2_4bit/remainders.mem", x2_remainders_mem);
    $readmemh("data/golden/rtl_core/x2_4bit/masks.mem", x2_masks_mem);
    $readmemh("data/golden/rtl_core/x2_4bit/weights.mem", x2_weights_mem);
    $readmemh("data/golden/rtl_core/x2_4bit/addresses.mem", x2_addresses_mem);
    $readmemh("data/golden/rtl_core/x2_4bit/numerators.mem", x2_numerators_mem);
    $readmemh("data/golden/rtl_core/x4_5bit/inputs.mem", x4_inputs_mem);
    $readmemh("data/golden/rtl_core/x4_5bit/lower.mem", x4_lower_mem);
    $readmemh("data/golden/rtl_core/x4_5bit/remainders.mem", x4_remainders_mem);
    $readmemh("data/golden/rtl_core/x4_5bit/masks.mem", x4_masks_mem);
    $readmemh("data/golden/rtl_core/x4_5bit/weights.mem", x4_weights_mem);
    $readmemh("data/golden/rtl_core/x4_5bit/addresses.mem", x4_addresses_mem);
    $readmemh("data/golden/rtl_core/x4_5bit/numerators.mem", x4_numerators_mem);

    clk = 1'b0;
    resetn = 1'b0;
    x2_pixels = '0;
    x4_pixels = '0;
    x2_direct_addresses = '0;
    x2_direct_weights = '0;
    x4_direct_addresses = '0;
    x4_direct_weights = '0;
    x2_use_chain = 1'b0;
    x4_use_chain = 1'b0;
    x2_start = 1'b0;
    x4_start = 1'b0;
    error_count = 0;
    transaction_count = 0;
    numerator_check_count = 0;
    chain_check_count = 0;

    repeat (2) @(posedge clk);
    #1;
    resetn = 1'b1;

    run_x2_direct();
    run_x2_chain();
    run_x4_direct();
    run_x4_chain();

    if (error_count != 0) begin
      $fatal(
        1,
        "E4_INTERPOLATOR_FAIL errors=%0d transactions=%0d numerator_checks=%0d chain_checks=%0d",
        error_count, transaction_count, numerator_check_count, chain_check_count
      );
    end

    $display(
      "E4_INTERPOLATOR_PASS transactions=%0d numerator_checks=%0d chain_checks=%0d latency=%0d",
      transaction_count, numerator_check_count, chain_check_count,
      INTERPOLATOR_LATENCY
    );
    $finish;
  end

endmodule
