`timescale 1ns/1ps

module tb_sr_lut_address_gen;

  import sr_lut_pkg::*;

  localparam int unsigned CORE_CASES = 10041;
  localparam int unsigned MAX_PRINTED_ERRORS = 20;
  localparam int unsigned MASK_BUS_BITS =
      SIMPLEX_VERTEX_COUNT * SIMPLEX_MASK_BITS;
  localparam int unsigned SIMPLEX_WEIGHT_COUNT =
      SIMPLEX_VERTEX_COUNT * SIMPLEX_WEIGHT_BITS;

  logic [(NEIGHBOR_COUNT * PIXEL_BITS) - 1:0] x2_pixels;
  logic [(NEIGHBOR_COUNT * X2_LOWER_BITS) - 1:0] x2_quant_lower;
  logic [(NEIGHBOR_COUNT * X2_SAMPLE_BITS) - 1:0] x2_quant_remainders;
  logic [(NEIGHBOR_COUNT * X2_LOWER_BITS) - 1:0] x2_direct_lower;
  logic [(NEIGHBOR_COUNT * X2_SAMPLE_BITS) - 1:0] x2_direct_remainders;
  logic [MASK_BUS_BITS - 1:0] x2_direct_masks;
  logic x2_use_chain;
  logic [(NEIGHBOR_COUNT * X2_LOWER_BITS) - 1:0] x2_address_lower;
  logic [MASK_BUS_BITS - 1:0] x2_address_masks;
  logic [(SIMPLEX_VERTEX_COUNT * X2_ADDR_BITS) - 1:0] x2_addresses;
  logic [MASK_BUS_BITS - 1:0] x2_selected_masks;
  logic [SIMPLEX_WEIGHT_COUNT - 1:0] x2_unused_weights;

  logic [(NEIGHBOR_COUNT * PIXEL_BITS) - 1:0] x4_pixels;
  logic [(NEIGHBOR_COUNT * X4_LOWER_BITS) - 1:0] x4_quant_lower;
  logic [(NEIGHBOR_COUNT * X4_SAMPLE_BITS) - 1:0] x4_quant_remainders;
  logic [(NEIGHBOR_COUNT * X4_LOWER_BITS) - 1:0] x4_direct_lower;
  logic [(NEIGHBOR_COUNT * X4_SAMPLE_BITS) - 1:0] x4_direct_remainders;
  logic [MASK_BUS_BITS - 1:0] x4_direct_masks;
  logic x4_use_chain;
  logic [(NEIGHBOR_COUNT * X4_LOWER_BITS) - 1:0] x4_address_lower;
  logic [MASK_BUS_BITS - 1:0] x4_address_masks;
  logic [(SIMPLEX_VERTEX_COUNT * X4_ADDR_BITS) - 1:0] x4_addresses;
  logic [MASK_BUS_BITS - 1:0] x4_selected_masks;
  logic [SIMPLEX_WEIGHT_COUNT - 1:0] x4_unused_weights;

  logic [31:0] input_vectors [0:CORE_CASES - 1];
  logic [31:0] lower_vectors [0:CORE_CASES - 1];
  logic [31:0] remainder_vectors [0:CORE_CASES - 1];
  logic [MASK_BUS_BITS - 1:0] mask_vectors [0:CORE_CASES - 1];
  logic [159:0] address_vectors [0:CORE_CASES - 1];

  string vector_root;
  integer error_count;
  integer address_evaluation_count;

  assign x2_address_lower = x2_use_chain ? x2_quant_lower : x2_direct_lower;
  assign x2_address_masks = x2_use_chain ? x2_selected_masks : x2_direct_masks;
  assign x4_address_lower = x4_use_chain ? x4_quant_lower : x4_direct_lower;
  assign x4_address_masks = x4_use_chain ? x4_selected_masks : x4_direct_masks;

  sr_lut_quantizer #(
    .SAMPLE_BITS(X2_SAMPLE_BITS),
    .LEVELS(X2_LEVELS),
    .LOWER_BITS(X2_LOWER_BITS)
  ) quantizer_x2 (
    .pixels_i(x2_pixels),
    .lower_o(x2_quant_lower),
    .remainder_o(x2_quant_remainders)
  );

  sr_lut_simplex_selector #(
    .SAMPLE_BITS(X2_SAMPLE_BITS),
    .Q(X2_Q)
  ) simplex_x2 (
    .remainders_i(x2_quant_remainders),
    .masks_o(x2_selected_masks),
    .weights_o(x2_unused_weights)
  );

  sr_lut_address_gen #(
    .LOWER_BITS(X2_LOWER_BITS),
    .LEVELS(X2_LEVELS),
    .ADDR_BITS(X2_ADDR_BITS)
  ) dut_x2 (
    .lower_i(x2_address_lower),
    .masks_i(x2_address_masks),
    .addresses_o(x2_addresses)
  );

  sr_lut_quantizer #(
    .SAMPLE_BITS(X4_SAMPLE_BITS),
    .LEVELS(X4_LEVELS),
    .LOWER_BITS(X4_LOWER_BITS)
  ) quantizer_x4 (
    .pixels_i(x4_pixels),
    .lower_o(x4_quant_lower),
    .remainder_o(x4_quant_remainders)
  );

  sr_lut_simplex_selector #(
    .SAMPLE_BITS(X4_SAMPLE_BITS),
    .Q(X4_Q)
  ) simplex_x4 (
    .remainders_i(x4_quant_remainders),
    .masks_o(x4_selected_masks),
    .weights_o(x4_unused_weights)
  );

  sr_lut_address_gen #(
    .LOWER_BITS(X4_LOWER_BITS),
    .LEVELS(X4_LEVELS),
    .ADDR_BITS(X4_ADDR_BITS)
  ) dut_x4 (
    .lower_i(x4_address_lower),
    .masks_i(x4_address_masks),
    .addresses_o(x4_addresses)
  );

  task automatic record_error(
    input string configuration,
    input string phase,
    input integer case_index,
    input integer vertex,
    input logic [31:0] expected_address,
    input logic [31:0] actual_address,
    input string reason
  );
    begin
      error_count = error_count + 1;
      if (error_count <= MAX_PRINTED_ERRORS) begin
        $display(
          "ERROR config=%s phase=%s case=%0d vertex=%0d expected=%08h actual=%08h reason=%s",
          configuration, phase, case_index, vertex,
          expected_address, actual_address, reason
        );
      end
    end
  endtask

  task automatic check_x2_addresses(
    input string phase,
    input integer case_index
  );
    integer vertex;
    logic [31:0] expected_address;
    logic [31:0] actual_address;
    begin
      for (vertex = 0; vertex < SIMPLEX_VERTEX_COUNT; vertex++) begin
        expected_address = address_vectors[case_index][
          (vertex * 32) +: 32
        ];
        actual_address = '0;
        actual_address[X2_ADDR_BITS - 1:0] = x2_addresses[
          (vertex * X2_ADDR_BITS) +: X2_ADDR_BITS
        ];
        address_evaluation_count = address_evaluation_count + 1;
        if (actual_address !== expected_address) begin
          record_error(
            "x2_4bit", phase, case_index, vertex,
            expected_address, actual_address, "address_mismatch"
          );
        end
        if (actual_address >= X2_LUT_DEPTH) begin
          record_error(
            "x2_4bit", phase, case_index, vertex,
            expected_address, actual_address, "address_out_of_range"
          );
        end
      end
    end
  endtask

  task automatic check_x4_addresses(
    input string phase,
    input integer case_index
  );
    integer vertex;
    logic [31:0] expected_address;
    logic [31:0] actual_address;
    begin
      for (vertex = 0; vertex < SIMPLEX_VERTEX_COUNT; vertex++) begin
        expected_address = address_vectors[case_index][
          (vertex * 32) +: 32
        ];
        actual_address = '0;
        actual_address[X4_ADDR_BITS - 1:0] = x4_addresses[
          (vertex * X4_ADDR_BITS) +: X4_ADDR_BITS
        ];
        address_evaluation_count = address_evaluation_count + 1;
        if (actual_address !== expected_address) begin
          record_error(
            "x4_5bit", phase, case_index, vertex,
            expected_address, actual_address, "address_mismatch"
          );
        end
        if (actual_address >= X4_LUT_DEPTH) begin
          record_error(
            "x4_5bit", phase, case_index, vertex,
            expected_address, actual_address, "address_out_of_range"
          );
        end
      end
    end
  endtask

  task automatic load_vectors(input string configuration);
    begin
      $readmemh($sformatf("%s/%s/inputs.mem", vector_root, configuration),
                input_vectors);
      $readmemh($sformatf("%s/%s/lower.mem", vector_root, configuration),
                lower_vectors);
      $readmemh(
        $sformatf("%s/%s/remainders.mem", vector_root, configuration),
        remainder_vectors
      );
      $readmemh($sformatf("%s/%s/masks.mem", vector_root, configuration),
                mask_vectors);
      $readmemh($sformatf("%s/%s/addresses.mem", vector_root, configuration),
                address_vectors);
    end
  endtask

  task automatic run_x2_tests;
    integer case_index;
    integer lane;
    begin
      $display("E3 x2_4bit: loading 10041 address cases");
      load_vectors("x2_4bit");

      x2_use_chain = 1'b0;
      for (case_index = 0; case_index < CORE_CASES; case_index++) begin
        x2_direct_lower = '0;
        x2_direct_remainders = '0;
        x2_direct_masks = mask_vectors[case_index];
        for (lane = 0; lane < NEIGHBOR_COUNT; lane++) begin
          if ((lower_vectors[case_index][(lane * 8) +: 8]
               >> X2_LOWER_BITS) != 0) begin
            $fatal(1, "x2 lower vector exceeds LOWER_BITS");
          end
          if ((remainder_vectors[case_index][(lane * 8) +: 8]
               >> X2_SAMPLE_BITS) != 0) begin
            $fatal(1, "x2 remainder vector exceeds SAMPLE_BITS");
          end
          x2_direct_lower[(lane * X2_LOWER_BITS) +: X2_LOWER_BITS] =
              lower_vectors[case_index][(lane * 8) +: X2_LOWER_BITS];
          x2_direct_remainders[(lane * X2_SAMPLE_BITS) +: X2_SAMPLE_BITS] =
              remainder_vectors[case_index][(lane * 8) +: X2_SAMPLE_BITS];
        end
        #1;
        check_x2_addresses("direct_lower_masks", case_index);
      end

      x2_use_chain = 1'b1;
      for (case_index = 0; case_index < CORE_CASES; case_index++) begin
        x2_pixels = input_vectors[case_index];
        x2_direct_lower = '0;
        x2_direct_remainders = '0;
        x2_direct_masks = mask_vectors[case_index];
        for (lane = 0; lane < NEIGHBOR_COUNT; lane++) begin
          x2_direct_lower[(lane * X2_LOWER_BITS) +: X2_LOWER_BITS] =
              lower_vectors[case_index][(lane * 8) +: X2_LOWER_BITS];
          x2_direct_remainders[(lane * X2_SAMPLE_BITS) +: X2_SAMPLE_BITS] =
              remainder_vectors[case_index][(lane * 8) +: X2_SAMPLE_BITS];
        end
        #1;
        if (x2_quant_lower !== x2_direct_lower) begin
          record_error("x2_4bit", "quantizer_chain", case_index, 0,
                       32'b0, 32'b0, "lower_chain_mismatch");
        end
        if (x2_quant_remainders !== x2_direct_remainders) begin
          record_error("x2_4bit", "quantizer_chain", case_index, 0,
                       32'b0, 32'b0, "remainder_chain_mismatch");
        end
        if (x2_selected_masks !== x2_direct_masks) begin
          record_error("x2_4bit", "quantizer_chain", case_index, 0,
                       32'b0, 32'b0, "mask_chain_mismatch");
        end
        check_x2_addresses("quantizer_simplex_chain", case_index);
      end
    end
  endtask

  task automatic run_x4_tests;
    integer case_index;
    integer lane;
    begin
      $display("E3 x4_5bit: loading 10041 address cases");
      load_vectors("x4_5bit");

      x4_use_chain = 1'b0;
      for (case_index = 0; case_index < CORE_CASES; case_index++) begin
        x4_direct_lower = '0;
        x4_direct_remainders = '0;
        x4_direct_masks = mask_vectors[case_index];
        for (lane = 0; lane < NEIGHBOR_COUNT; lane++) begin
          if ((lower_vectors[case_index][(lane * 8) +: 8]
               >> X4_LOWER_BITS) != 0) begin
            $fatal(1, "x4 lower vector exceeds LOWER_BITS");
          end
          if ((remainder_vectors[case_index][(lane * 8) +: 8]
               >> X4_SAMPLE_BITS) != 0) begin
            $fatal(1, "x4 remainder vector exceeds SAMPLE_BITS");
          end
          x4_direct_lower[(lane * X4_LOWER_BITS) +: X4_LOWER_BITS] =
              lower_vectors[case_index][(lane * 8) +: X4_LOWER_BITS];
          x4_direct_remainders[(lane * X4_SAMPLE_BITS) +: X4_SAMPLE_BITS] =
              remainder_vectors[case_index][(lane * 8) +: X4_SAMPLE_BITS];
        end
        #1;
        check_x4_addresses("direct_lower_masks", case_index);
      end

      x4_use_chain = 1'b1;
      for (case_index = 0; case_index < CORE_CASES; case_index++) begin
        x4_pixels = input_vectors[case_index];
        x4_direct_lower = '0;
        x4_direct_remainders = '0;
        x4_direct_masks = mask_vectors[case_index];
        for (lane = 0; lane < NEIGHBOR_COUNT; lane++) begin
          x4_direct_lower[(lane * X4_LOWER_BITS) +: X4_LOWER_BITS] =
              lower_vectors[case_index][(lane * 8) +: X4_LOWER_BITS];
          x4_direct_remainders[(lane * X4_SAMPLE_BITS) +: X4_SAMPLE_BITS] =
              remainder_vectors[case_index][(lane * 8) +: X4_SAMPLE_BITS];
        end
        #1;
        if (x4_quant_lower !== x4_direct_lower) begin
          record_error("x4_5bit", "quantizer_chain", case_index, 0,
                       32'b0, 32'b0, "lower_chain_mismatch");
        end
        if (x4_quant_remainders !== x4_direct_remainders) begin
          record_error("x4_5bit", "quantizer_chain", case_index, 0,
                       32'b0, 32'b0, "remainder_chain_mismatch");
        end
        if (x4_selected_masks !== x4_direct_masks) begin
          record_error("x4_5bit", "quantizer_chain", case_index, 0,
                       32'b0, 32'b0, "mask_chain_mismatch");
        end
        check_x4_addresses("quantizer_simplex_chain", case_index);
      end
    end
  endtask

  initial begin
    error_count = 0;
    address_evaluation_count = 0;
    x2_pixels = '0;
    x2_direct_lower = '0;
    x2_direct_remainders = '0;
    x2_direct_masks = '0;
    x2_use_chain = 1'b0;
    x4_pixels = '0;
    x4_direct_lower = '0;
    x4_direct_remainders = '0;
    x4_direct_masks = '0;
    x4_use_chain = 1'b0;

    if (!$value$plusargs("VECTOR_ROOT=%s", vector_root)) begin
      vector_root = "data/golden/rtl_core";
    end

    if ((X2_ADDR_BITS != 17) || (X4_ADDR_BITS != 13)) begin
      $fatal(1, "Invalid address width constants in sr_lut_pkg");
    end

    run_x2_tests();
    run_x4_tests();

    if (error_count != 0) begin
      $fatal(
        1,
        "E3_ADDRESS_FAIL errors=%0d address_evaluations=%0d",
        error_count,
        address_evaluation_count
      );
    end

    $display(
      "E3_ADDRESS_PASS configurations=2 vector_rows=%0d address_evaluations=%0d",
      2 * CORE_CASES,
      address_evaluation_count
    );
    $finish;
  end

endmodule
